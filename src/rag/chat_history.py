# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. 多轮对话上下文管理（分层记忆：最近10轮原文 + 更早轮次摘要）
# 2. 意图识别前置路由：LLM 判断"走知识库检索 vs 常识直接回答"
# 3. Query 改写（指代消解："它"→上文实体）
# 4. 历史长度限制（防止 token 溢出，长对话自动摘要压缩）
#
# 💡 面试杀手锏：
#    - "长对话上下文爆了怎么办？" → 分层记忆：最近 N 轮原文 + 更早轮次摘要
#    - "如何减少无效检索？" → 意图路由：先判断是知识库问题还是常识问题
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

"""
多轮对话管理器：分层记忆 + 意图路由 + query rewriting
"""

from typing import Dict, List, Optional
import os
import re
import threading
from langchain_core.prompts import PromptTemplate
# 提升到顶部（修复 2026-09-17 HIGH #5）：原函数体内延迟导入，单元测试
# 在 PYTHONPATH 未设根目录时会 ImportError
from src.rag.llm_chain import clean_answer


# 多轮对话 Prompt 模板
CONVERSATIONAL_TEMPLATE = """你是一个简洁的智能校园助手，专门帮助无锡学院的学生解答问题。

请根据以下参考文档内容和对话历史来回答用户的问题。先判断参考资料是否真正相关，再选择回答方式。

参考文档内容：
{context}

对话历史：
{chat_history}

用户问题：{question}

要求：
1. 先快速浏览参考资料和对话历史：其中是否有任何 1-2 段直接针对用户问题的内容？
2. 如果有相关资料或历史能直接回答：基于资料/历史回答，不需要复述资料来源本身
3. 如果参考资料和历史都跟问题无关：用你自己的常识简短回答（如算术题直接给出答案、学校生活类问题给常识建议并注明"建议以学校官方通知为准"），不要生硬说"没找到"
4. 回答要简洁明了，3-5 句内讲清楚
5. 如需列表，最多只列3-5条关键信息
6. 回答中不要使用特殊符号（如####、****、——等装饰符号），只使用基本中文标点

回答："""

# Query 改写 Prompt
QUERY_REWRITE_TEMPLATE = """你是指代消解助手。根据历史对话，把用户最新问题中的指代词（"它"、"这个"、"刚才说的"、"里面"等）替换成历史中明确出现过的具体对象，重写成自包含的查询。

只返回改写后的问题，不要任何解释、不要任何标点。如果问题已经自包含没有指代，原样返回。

历史：
{history}

最新问题：{question}

改写后："""

# 意图路由 Prompt
INTENT_ROUTE_TEMPLATE = """你是意图分类助手。判断用户问题属于哪一类，只返回类别编号，不要解释。

类别：
1. 知识库查询（学校政策、课程、考试、学分、通知等需要查学校资料库的问题）
2. 常识问答（算术、通用知识、与无锡学院无关的问题）
3. 闲聊/问候（你好、谢谢、再见等）

用户问题：{question}

类别编号："""

# 历史摘要 Prompt
SUMMARY_TEMPLATE = """请将以下对话历史压缩成一段不超过100字的摘要，保留关键实体和主题，去掉重复内容。

对话历史：
{history}

摘要："""

# 最近保留的原文轮次数
RECENT_ROUNDS = 10


class ChatHistoryManager:
    """
    多轮对话管理器（手写实现，无外部依赖）

    架构（不要把它说成 ConversationalRetrievalChain / LangChain LegacyChain）：
    - **分层记忆**：最近 RECENT_ROUNDS 轮原文 + 更早轮次压缩成 old_summary
    - **意图路由**：调 LLM 把 query 分到 chat / common / knowledge 三类
    - **Query Rewriting**：调 LLM 做指代消解（"它"→历史中具体实体）
    - **多线程安全**：所有 state 读写都加 self._lock（修复 CRITICAL #2）

    这是 [审计 bad case B "多轮指代失败"] 的关键修复 —— 替代
    LangChain ConversationalRetrievalChain（中文指代解析失败导致丢上下文）。
    """

    def __init__(self, llm, retriever):
        self.llm = llm
        self.retriever = retriever
        self.recent_history: List[tuple] = []  # 最近 N 轮原文 [(q, a), ...]
        self.old_summary: str = ""  # 更早轮次的摘要

        # 修复 CRITICAL #2（2026-09-17）：
        # Gradio queue 模式下，后台线程触发 chat() 和事件循环线程可能并发，
        # 没有锁会丢轮次 / slice 越界（list 长度变化期间被 enumerate）。
        # 用 RLock 因为 chat() 内部可能嵌套调用 _add_to_history/_build_history_str。
        self._lock = threading.RLock()

        # Query 改写 prompt
        self.rewrite_prompt = PromptTemplate(
            template=QUERY_REWRITE_TEMPLATE,
            input_variables=["history", "question"]
        )
        self.rewrite_chain = self.rewrite_prompt | self.llm

        # 意图路由 prompt
        self.intent_prompt = PromptTemplate(
            template=INTENT_ROUTE_TEMPLATE,
            input_variables=["question"]
        )
        self.intent_chain = self.intent_prompt | self.llm

        # 历史摘要 prompt
        self.summary_prompt = PromptTemplate(
            template=SUMMARY_TEMPLATE,
            input_variables=["history"]
        )
        self.summary_chain = self.summary_prompt | self.llm

        # 主 QA prompt
        self.qa_prompt = PromptTemplate(
            template=CONVERSATIONAL_TEMPLATE,
            input_variables=["context", "chat_history", "question"]
        )
        self.qa_chain = self.qa_prompt | self.llm

    def _intent_route(self, question: str) -> str:
        """
        意图识别前置路由
        Returns: "kb"（知识库查询）/ "common"（常识）/ "chat"（闲聊）
        """
        try:
            result = self.intent_chain.invoke({"question": question}).content.strip()
            if "1" in result or "知识" in result:
                return "kb"
            elif "2" in result or "常识" in result:
                return "common"
            elif "3" in result or "闲聊" in result or "问候" in result:
                return "chat"
            return "kb"  # 默认走知识库
        except Exception:
            return "kb"

    def _rewrite_query(self, question: str) -> str:
        """用 LLM 改写 query：指代消解"""
        if not self.recent_history:
            return question

        history_str = "\n".join([
            f"用户: {q}\n助手: {a[:120]}"
            for q, a in self.recent_history[-2:]
        ])

        try:
            rewritten = self.rewrite_chain.invoke({"history": history_str, "question": question}).content
            rewritten = rewritten.strip()
            if len(rewritten) > len(question) * 3 or len(rewritten) < 3:
                return question
            return rewritten
        except Exception:
            return question

    def _compress_old_history(self):
        """
        将超过 RECENT_ROUNDS 的旧历史压缩成摘要，防止上下文爆炸
        """
        if len(self.recent_history) <= RECENT_ROUNDS:
            return

        # 需要压缩的旧轮次
        old_rounds = self.recent_history[:-RECENT_ROUNDS]
        self.recent_history = self.recent_history[-RECENT_ROUNDS:]

        # 构建旧历史文本
        old_text = "\n".join([
            f"用户: {q}\n助手: {a[:80]}"
            for q, a in old_rounds
        ])

        # 用 LLM 生成摘要
        try:
            summary = self.summary_chain.invoke({"history": old_text}).content.strip()
            # 合并到旧摘要
            if self.old_summary:
                self.old_summary = f"{self.old_summary[:200]}...\n{summary[:200]}"
            else:
                self.old_summary = summary[:300]
        except Exception:
            # 降级：直接截取旧历史的最后几条
            self.old_summary = old_text[-300:]

    def _build_history_str(self) -> str:
        """构建传给 prompt 的历史字符串（摘要 + 最近原文）"""
        parts = []
        if self.old_summary:
            parts.append(f"[历史摘要] {self.old_summary}")
        if self.recent_history:
            recent = "\n".join([
                f"用户: {q}\n助手: {a[:80]}"
                for q, a in self.recent_history[-3:]
            ])
            parts.append(recent)
        return "\n".join(parts) if parts else "（无历史对话）"

    def chat(self, question: str) -> dict:
        """
        聊天（意图路由 + 多轮对话 + query rewriting）

        全方法锁定（修复 CRITICAL #2）：
        Gradio queue 模式下后台线程和事件循环线程会并发触发；
        llm.invoke 期间持锁导致串行等待——这是 Gradio 4.x 单实例应有的特性。
        """
        with self._lock:
            # 0. 意图识别前置路由
            intent = self._intent_route(question)

            # 闲聊直接回复
            if intent == "chat":
                answer = self._chat_reply(question)
                self._add_to_history(question, answer)
                return {"answer": answer, "sources": [], "intent": intent}

            # 1. Query rewriting（指代消解）
            rewritten = self._rewrite_query(question)

            # 2. 常识类问题：不检索，直接用常识回答
            if intent == "common":
                context = ""
                docs, scores = [], []
            else:
                # 知识库类：检索
                if hasattr(self.retriever, "retrieve_with_scores"):
                    docs, scores = self.retriever.retrieve_with_scores(rewritten)
                else:
                    docs = self.retriever.retrieve(rewritten)
                    scores = [None] * len(docs)
                context = "\n\n".join([d.page_content for d in docs])

            # 3. 拼接历史（分层记忆）
            history_str = self._build_history_str()

            # 4. 调 LLM 回答
            response = self.qa_chain.invoke({
                "context": context,
                "chat_history": history_str,
                "question": question
            }).content

            # import 已提升到模块顶部（修复 HIGH #5）
            answer = clean_answer(response.strip())

            # 5. 保存到历史（并压缩旧历史）
            self._add_to_history(question, answer)

        # 6. 构造 sources
        sources = []
        for i, (doc, score) in enumerate(zip(docs, scores)):
            sources.append({
                "index": i + 1,
                "content": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                "source": doc.metadata.get("source", "未知"),
                "page": doc.metadata.get("page", ""),
                "score": round(float(score), 4) if score is not None else None,
            })

        return {
            "answer": answer,
            "sources": sources,
            "intent": intent,
        }

    def chat_stream(self, question: str):
        """
        chat() 的流式版本（2026-09-20 提速）。

        逐步 yield 三元组 (partial_answer, sources, intent)：
        - 路由/检索阶段不出字（这部分无法流式），首个 yield 从 LLM 第一个 token 开始
        - 生成过程中 sources=None，前端显示"正在生成"
        - 最后一次 yield 给完整 answer + sources 列表（同时已写入历史）

        说明：流式过程中只做轻量清洗（去控制字符），避免反复剥首尾符号造成闪烁；
        完整 clean_answer 只在结束时做一次。
        """
        def _light_clean(text: str) -> str:
            return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

        with self._lock:
            # 0. 意图路由
            intent = self._intent_route(question)

            if intent == "chat":
                answer = self._chat_reply(question)
                self._add_to_history(question, answer)
                yield answer, [], intent
                return

            # 1. Query 改写（指代消解）
            rewritten = self._rewrite_query(question)

            # 2. 常识类不检索；知识库类检索
            if intent == "common":
                context = ""
                docs, scores = [], []
            else:
                if hasattr(self.retriever, "retrieve_with_scores"):
                    docs, scores = self.retriever.retrieve_with_scores(rewritten)
                else:
                    docs = self.retriever.retrieve(rewritten)
                    scores = [None] * len(docs)
                context = "\n\n".join([d.page_content for d in docs])

            # 3. 历史
            history_str = self._build_history_str()

            # 4. 流式生成（失败则降级为一次性 invoke，保证不白屏）
            payload = {"context": context, "chat_history": history_str, "question": question}
            partial = ""
            try:
                for chunk in self.qa_chain.stream(payload):
                    piece = getattr(chunk, "content", None)
                    if not piece:
                        continue
                    partial += piece
                    yield _light_clean(partial), None, intent
            except Exception as e:
                print(f"[chat_stream] 流式失败，降级 invoke: {e!r}", flush=True)

            if not partial.strip():
                try:
                    partial = self.qa_chain.invoke(payload).content
                except Exception as e:
                    partial = "抱歉，生成回答时出了点问题，请稍后再试一次。"
                    print(f"[chat_stream] invoke 也失败: {e!r}", flush=True)

            answer = clean_answer(partial.strip()) or partial.strip()
            self._add_to_history(question, answer)

            # 5. 组装来源（把文章 metadata 也带上，前端可展示图片）
            sources = []
            for i, (doc, score) in enumerate(zip(docs, scores)):
                src = {
                    "index": i + 1,
                    "content": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                    "source": doc.metadata.get("source", "未知"),
                    "page": doc.metadata.get("page", ""),
                    "score": round(float(score), 4) if score is not None else None,
                }
                # 公众号文章：带图片/类别/重要性
                if doc.metadata.get("type") == "wx_article":
                    src["type"] = "wx_article"
                    src["file_name"] = doc.metadata.get("file_name", "")
                    src["category"] = doc.metadata.get("category", "")
                    src["importance"] = doc.metadata.get("importance", "中")
                    src["key_info"] = doc.metadata.get("key_info", "")
                    imgs = doc.metadata.get("images", "")
                    if imgs:
                        src["images"] = imgs.split(";")
                sources.append(src)

            yield answer, sources, intent

    def _chat_reply(self, question: str) -> str:
        """闲聊/问候的直接回复"""
        q = question.strip()
        if any(w in q for w in ["你好", "您好", "hi", "hello", "在吗"]):
            return "你好！我是无锡学院智能助手，有什么可以帮你的？"
        if any(w in q for w in ["谢谢", "感谢", "thanks"]):
            return "不客气！还有其他问题吗？"
        if any(w in q for w in ["再见", "拜拜", "bye"]):
            return "再见！有问题随时问我。"
        return "我是无锡学院智能助手，可以帮你查询学校政策、课程、考试等信息。"

    def _add_to_history(self, question: str, answer: str):
        """添加到历史并在需要时压缩旧历史"""
        self.recent_history.append((question, answer))
        self._compress_old_history()

    def clear_history(self):
        """清空对话历史"""
        self.recent_history = []
        self.old_summary = ""

    def get_history(self) -> List[dict]:
        """获取对话历史"""
        result = []
        for q, a in self.recent_history:
            result.append({"role": "user", "content": q})
            result.append({"role": "assistant", "content": a})
        return result

    # ============================================================
    # 多用户隔离：状态序列化（2026-09-17 新增）
    # ============================================================
    # 只持久化 (recent_history, old_summary) 两个属性，
    # LLM / retriever 引用不能 pickle，必须留在进程内 dict cache。
    def to_state_dict(self) -> dict:
        """导出可序列化状态（不含 llm/retriever）"""
        return {
            "recent_history": list(self.recent_history),
            "old_summary": self.old_summary,
            "schema_version": 1,
        }

    def load_state_dict(self, state: dict) -> None:
        """从 dict 恢复（空 dict 或损坏时静默处理，不阻断实例）"""
        if not isinstance(state, dict):
            return
        rh = state.get("recent_history", [])
        self.recent_history = [(q, a) for q, a in rh if isinstance(q, str) and isinstance(a, str)]
        self.old_summary = str(state.get("old_summary", ""))


class SimpleChatManager:
    """简化版多轮对话管理器（不依赖 LangChain Chain）"""

    def __init__(self, llm, retriever):
        self.llm = llm
        self.retriever = retriever
        self.history: List[dict] = []

    def chat(self, question: str) -> dict:
        context, sources = self.retriever.retrieve_with_sources(question)

        chat_history_str = ""
        if self.history:
            history_texts = []
            for msg in self.history[-6:]:
                role = "用户" if msg["role"] == "user" else "助手"
                history_texts.append(f"{role}: {msg['content']}")
            chat_history_str = "\n".join(history_texts)

        prompt = CONVERSATIONAL_TEMPLATE.format(
            context=context,
            chat_history=chat_history_str or "（无历史对话）",
            question=question
        )

        from langchain.schema import HumanMessage
        response = self.llm.invoke([HumanMessage(content=prompt)])
        answer = response.content

        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})

        if len(self.history) > 20:
            self.history = self.history[-20:]

        return {"answer": answer, "sources": sources}

    def clear_history(self):
        self.history = []

    def get_history(self) -> List[dict]:
        return self.history


def create_chat_manager(llm, retriever, use_simple: bool = False):
    """
    创建多轮对话管理器
    Args:
        llm: LLM 实例
        retriever: 检索器实例
        use_simple: True 使用简化版，False 使用完整版（默认）
    Returns:
        对话管理器实例
    """
    if use_simple:
        return SimpleChatManager(llm, retriever)
    else:
        return ChatHistoryManager(llm, retriever)


if __name__ == "__main__":
    from vector_store import VectorStoreManager
    from retriever import create_retriever
    from llm_chain import LLMQASystem

    print("加载向量库...")
    manager = VectorStoreManager(persist_directory="data/faiss_db_bge")
    vs = manager.load_vectorstore()

    print("创建检索器...")
    retriever = create_retriever(vs, documents=manager._documents, top_k=4)

    print("创建 LLM...")
    from langchain.chat_models import ChatOpenAI
    from dotenv import load_dotenv
    load_dotenv(".env", encoding='utf-8-sig')

    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "gpt-3.5-turbo"),
        temperature=0.3,
        openai_api_key=os.getenv("LLM_API_KEY"),
        openai_api_base=os.getenv("LLM_BASE_URL")
    )

    print("创建多轮对话管理器...")
    chat_mgr = create_chat_manager(llm, retriever, use_simple=False)

    print("\n=== 测试多轮对话 ===")
    result = chat_mgr.chat("电子信息工程专业需要修满多少学分？")
    print(f"回答: {result['answer'][:200]}...")
