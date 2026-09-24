# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. LLM API 调用（ChatOpenAI 兼容 DeepSeek API）
# 2. Prompt 工程（System Prompt / User Prompt 分离设计）
# 3. 引用溯源（LLM 输出中带 [来源1] 标记，与检索文档块对应）
# 4. 幻觉控制（强制"资料库没找到就回答'不知道'"）
# 5. 响应格式控制（禁止特殊符号，让回答简洁直接）
#
# 💡 面试高频问题：
#    - "如何减少 LLM 幻觉？" → prompt 强制约束 + 仅基于参考资料 + Ragas faithfulness
#    - "为什么用 DeepSeek 而不是 GPT？" → 中文强 + 价格低 + API 兼容 OpenAI
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

"""
LLM 问答链：拼接 prompt + 检索片段，调用 LLM 生成答案
"""

import os
import re
import json
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.documents import Document


def clean_answer(text: str) -> str:
    """
    清洗回答中的特殊符号
    """
    if not text:
        return text

    # 去除控制字符
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

    # 去除多余的装饰符号
    text = re.sub(r'[*#@]{3,}', '', text)  # 连续3个以上相同符号
    text = re.sub(r'[_=-]{5,}', '', text)  # 长横线下划线

    # 去除首尾空白和符号
    text = text.strip()

    # 去除首尾的非中文英文数字字符（保留基本标点）
    text = re.sub(r'^[^\w一-鿿，。！？、：；""''（）【】]+|[^\w一-鿿，。！？、：；""''（）【】]+$', '', text)

    # 修复多余空格
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def format_history_for_prompt(history, max_turns: int = 6) -> str:
    """把多轮对话历史格式化成 prompt 片段（2026-09-24 加：LLM 真多轮）

    输入格式：[{"role": "user"/"assistant", "content": "..."}, ...]
    输出格式：
        之前的对话历史(最近的对话):
        用户: ...
        助手: ...
        用户: ...

    Args:
        history: 多轮历史 list,取最近 max_turns*2 条
        max_turns: 最多取几轮(默认 6 轮,够覆盖指代/追问场景)

    Returns:
        str: 历史区段文字,空 list 时返回空字符串
    """
    if not history:
        return ""
    cleaned = []
    for h in history:
        if not isinstance(h, dict):
            continue
        role = h.get("role")
        content = h.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        content_str = str(content).strip()
        if len(content_str) > 500:
            content_str = content_str[:500] + "…"
        cleaned.append((role, content_str))
    if not cleaned:
        return ""
    cleaned = cleaned[-(max_turns * 2):]
    lines = ["之前的对话历史(最近的对话):", ""]
    role_zh = {"user": "用户", "assistant": "助手"}
    for role, content in cleaned:
        content_one_line = content.replace("\n", " ")
        lines.append(f"{role_zh[role]}: {content_one_line}")
    return "\n".join(lines)

# 加载 .env 文件（处理 BOM 头）
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
load_dotenv(env_path, encoding='utf-8-sig')


# 默认 Prompt 模板（中文）
DEFAULT_TEMPLATE = """你是一个简洁的智能校园助手，专门帮助无锡学院的学生解答问题。

合规要求（2026-09-21，依据《无锡学院关于试卷印制、保管和保存的实施办法（试行）》）：
试卷（含空白卷、参考答案）属学院教学档案，由学院统一归档保存，保密管理，不得向学生披露。
- 你可以与考生讨论考试的元信息（考试时间、范围、形式、占分比、复习建议等）
- 但**严禁**透露任何具体考题内容、参考答案、往年真题、空白试卷
- 若用户追问具体考题/答案/往年卷子，礼貌拒绝并引导到课程大纲/教学日历/任课教师

请根据以下参考文档内容 + 之前的对话历史，回答用户当前的问题。先判断参考资料/对话历史是否与用户问题真正相关，再选择回答方式。

参考文档内容：
{context}

{history_section}

用户问题：{question}

要求：
1. 先快速浏览参考资料和对话历史：其中是否有任何 1-2 段直接针对用户问题的内容？
2. 如果参考资料含相关关键词/日期/规则/数字（即使没有完全直接匹配的答案）：**必须从原文抽取信息作答**，例如"根据 X 通知第 Y 段..."即使参考里没写确切日期，也要引用原文里提到的相关线索（如"第 1 周开始上课"），不要建议查外部、不要绕开
3. 如果参考资料与问题**完全无关**（如问数电电路却给体育课通知）：用你自己的常识简短回答，并注明"以上只是常识，建议以学校官方通知为准"
4. **如果用户追问"刚才说的"、"上面那个"、"它"、"那个东西"等指代词**：必须参考对话历史理解指代对象，不要生硬地说"我不知道你指什么"
5. 回答要简洁明了，3-5 句内讲清楚
6. 如需列表，最多只列3-5条关键信息
7. 回答中不要使用特殊符号（如####、****、——等装饰符号），只使用基本中文标点
8. **禁止使用"不知道"、"无法"、"没法"等拒绝性词汇**；如需表达"资料没找到"，改用"参考资料中暂未列出"、"现有资料未直接说明"等中性表述

回答："""

# 带引用来源的 Prompt 模板
CITATION_TEMPLATE = """你是一个智能校园助手，专门帮助无锡学院的学生解答问题。

合规要求（2026-09-21，依据《无锡学院关于试卷印制、保管和保存的实施办法（试行）》）：
试卷（含空白卷、参考答案）属学院教学档案，保密管理，不得向学生披露。
- 你可以与考生讨论考试的元信息（考试时间、范围、形式、占分比、复习建议等）
- 但**严禁**透露任何具体考题内容、参考答案、往年真题、空白试卷
- 若用户追问具体考题/答案/往年卷子，礼貌拒绝并引导到课程大纲/教学日历/任课教师

请根据以下参考文档内容回答用户的问题。如果参考内容没有相关信息，请如实告知用户。

参考文档内容：
{context}

用户问题：{question}

要求：
1. 先快速浏览参考资料：其中是否有任何 1-2 段直接针对用户问题的内容？
2. 如果有相关资料：基于资料回答，并尽量引用具体信息；不需要解释资料来源本身
3. 如果参考资料与问题无关或资料库里没有相关内容：用你自己的常识简短回答（如算术题直接给出答案、学校生活类问题给常识建议并注明"建议以学校官方通知为准"），不要生硬说"没找到"
4. 回答要清晰、准确、简洁
5. 回答中标注参考来源的编号
6. 回答中不要使用特殊符号（如####、****、——等装饰符号），只使用基本中文标点

回答（附引用来源）："""

# Fallback Prompt 模板：调用方判断为"召回失败"时使用
NO_CONTEXT_TEMPLATE = """你是一个简洁的智能校园助手，专门帮助无锡学院的学生解答问题。

合规要求（2026-09-21，依据《无锡学院关于试卷印制、保管和保存的实施办法（试行）》）：
试卷（含空白卷、参考答案）属学院教学档案，保密管理，不得向学生披露。
- 你可以与考生讨论考试的元信息（考试时间、范围、形式、占分比、复习建议等）
- 但**严禁**透露任何具体考题内容、参考答案、往年真题、空白试卷
- 若用户追问具体考题/答案/往年卷子，礼貌拒绝并引导到课程大纲/教学日历/任课教师

注意：参考资料中没有与用户问题直接相关的内容（可能是冷门问题、超出资料库范围的常识问题、或口语化问法）。

请：
1. 用你自己的知识简短回答（不要长篇大论）
2. 如果是数学/逻辑/算术/常识问题，直接回答正确结论（如 "1+1=2"）
3. 如果是学校生活类问题（图书馆、食堂、校历等），可以基于常识给一个**简短建议**（如"图书馆一般周一到周日开放，具体时间建议查看学校官网"），但要明确告诉用户"以上只是常识建议，建议以学校官方通知为准"
4. 如果是开放性问题（如"Python 怎么学"），给一个简短的入门路径建议（3-5 步）
5. 回答中不要使用特殊符号（如####、****、——等装饰符号），只使用基本中文标点

用户问题：{question}

回答："""


class LLMQASystem:
    """LLM 问答系统"""

    def __init__(
        self,
        retriever,
        model_name: str = "gpt-3.5-turbo",
        temperature: float = 0.3,
        template: str = None,
    ):
        """
        初始化 LLM 问答系统
        Args:
            retriever: 检索器实例
            model_name: LLM 模型名称
            temperature: 温度参数
            template: Prompt 模板
        """
        self.retriever = retriever
        self.temperature = temperature
        self.template = template or DEFAULT_TEMPLATE

        # 初始化 LLM
        self.llm = self._init_llm(model_name)

        # 创建主 Prompt（带 context + history，多轮能理解指代；内置 fallback 语义：LLM 自主判断参考资料是否相关）
        self.prompt = PromptTemplate(
            template=self.template,
            input_variables=["context", "question", "history_section"]
        )

        # 创建 Fallback Prompt（完全无召回时使用，节省 token）
        self.fallback_prompt = PromptTemplate(
            template=NO_CONTEXT_TEMPLATE,
            input_variables=["question"]
        )

        # 创建主问答链（LCEL: prompt | llm）
        self.chain = self.prompt | self.llm

        # 创建 fallback 问答链
        self.fallback_chain = self.fallback_prompt | self.llm

    def _init_llm(self, model_name: str):
        """初始化 LLM"""
        # 从环境变量读取 API 配置
        api_key = os.getenv("LLM_API_KEY", "")
        base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
        # 支持自定义模型，默认使用 deepseek-chat
        model = os.getenv("LLM_MODEL", model_name)

        if not api_key:
            raise ValueError("请在 .env 文件中配置有效的 LLM_API_KEY")

        return ChatOpenAI(
            model=model,
            temperature=self.temperature,
            openai_api_key=api_key,
            openai_api_base=base_url
        )

    def answer(
        self,
        question: str,
        history: Optional[List[Dict]] = None,
        return_sources: bool = True
    ) -> Dict[str, Any]:
        """
        回答用户问题
        Args:
            question: 用户问题
            history: 多轮对话历史(2026-09-24 加),格式 [{"role":"user/assistant","content":"..."}, ...]
            return_sources: 是否返回来源
        Returns:
            {"answer": 回答内容, "sources": 来源列表, "used_fallback": 是否使用 fallback}
        """
        # 1. 检索相关文档（带分数）
        if hasattr(self.retriever, "retrieve_with_scores"):
            docs, scores = self.retriever.retrieve_with_scores(question)
            # BGE reranker 偶发 None 分数,这里防御性转换,None 视为 0.0(不影响 docs 长度对齐)
            sources_raw = [(doc, float(score) if score is not None else 0.0) for doc, score in zip(docs, scores)]
        else:
            # 兼容没有分数返回的旧版 retriever
            docs = self.retriever.retrieve(question)
            sources_raw = [(doc, None) for doc in docs]
            scores = [None] * len(docs)

        # 2. 拼接 context + history
        context = "\n\n".join([doc.page_content for doc in docs])
        history_section = format_history_for_prompt(history)

        # 3. 调用 LLM（prompt 内置 fallback 语义：LLM 自主判断参考资料是否相关，无关就用常识回答）
        if not docs:
            # 完全没有召回：直接走 fallback chain（节省 token，不带 history）
            response = self.fallback_chain.invoke({"question": question}).content
            sources = []
        else:
            response = self.chain.invoke({
                "context": context,
                "question": question,
                "history_section": history_section,
            }).content
            # 转换 sources 为 dict 列表
            sources = []
            for i, (doc, score) in enumerate(sources_raw):
                sources.append({
                    "index": i + 1,
                    "content": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                    "source": doc.metadata.get("source", "未知"),
                    "page": doc.metadata.get("page", ""),
                    "score": round(score, 4) if score is not None else None,
                })

        # 4. 清洗回答中的特殊符号
        answer = clean_answer(response.strip())

        result = {
            "answer": answer,
            "context": context,
            "used_fallback": not bool(docs),  # 当前实现：完全没有召回时才算 fallback
            "top_score": round(float(scores[0]), 4) if scores and scores[0] is not None else None,
        }

        if return_sources:
            result["sources"] = sources

        return result

    def answer_stream(self, question: str, history: Optional[List[Dict]] = None):
        """
        流式生成答案(2026-09-23 加,2026-09-24 加 history 多轮):LLM 边推理边吐 token,前端边收边渲染

        Args:
            question: 用户问题
            history: 多轮对话历史(2026-09-24 加)

        Yields:
            str: 增量 token 文本
        """
        # 1. 检索(同步,因为 retrieve_with_scores 不支持 stream)
        if hasattr(self.retriever, "retrieve_with_scores"):
            docs, scores = self.retriever.retrieve_with_scores(question)
            sources_raw = [(doc, float(score) if score is not None else 0.0) for doc, score in zip(docs, scores)]
        else:
            docs = self.retriever.retrieve(question)
            sources_raw = [(doc, None) for doc in docs]
            scores = [None] * len(docs)

        context = "\n\n".join([doc.page_content for doc in docs])
        history_section = format_history_for_prompt(history)

        # 2. 选 chain
        if not docs:
            chain = self.fallback_chain
            invoke_kwargs = {"question": question}
        else:
            chain = self.chain
            invoke_kwargs = {
                "context": context,
                "question": question,
                "history_section": history_section,
            }

        # 3. 流式生成(LCEL: chain.stream() yield AIMessageChunk)
        for chunk in chain.stream(invoke_kwargs):
            text = getattr(chunk, "content", None)
            if not text:
                continue
            text = clean_answer(text)
            if text:
                yield text

        # 4. sources 作为最后一段(前端可选择性解析)
        if docs:
            sources = []
            for i, (doc, score) in enumerate(sources_raw):
                sources.append({
                    "index": i + 1,
                    "content": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                    "source": doc.metadata.get("source", "未知"),
                    "page": doc.metadata.get("page", ""),
                    "score": round(score, 4) if score is not None else None,
                })
            yield f"\n\n__SOURCES__:{json.dumps(sources, ensure_ascii=False)}"

    def answer_simple(self, question: str) -> str:
        """
        简单回答(不带来源)
        Args:
            question: 问题
        Returns:
            回答内容
        """
        return self.answer(question, return_sources=False)["answer"]


def create_qa_system(retriever, model_name: str = "gpt-3.5-turbo") -> LLMQASystem:
    """
    便捷函数：创建问答系统
    Args:
        retriever: 检索器
        model_name: 模型名称
    Returns:
        LLMQASystem 实例
    """
    return LLMQASystem(retriever, model_name)


if __name__ == "__main__":
    # 测试
    from vector_store import VectorStoreManager
    from retriever import create_retriever

    print("加载向量库...")
    manager = VectorStoreManager(persist_directory="data/faiss_db_bge")
    vs = manager.load_vectorstore()

    print("创建检索器...")
    retriever = create_retriever(vs, top_k=3)

    print("创建问答系统...")
    qa = create_qa_system(retriever)

    print("\n测试问答...")
    result = qa.answer("电子信息工程专业需要修满多少学分？")

    print("\n=== 回答 ===")
    print(result["answer"])
    print(f"\n是否使用 fallback: {result.get('used_fallback')}")
    print(f"top-1 分数: {result.get('top_score')}")

    if "sources" in result and result["sources"]:
        print("\n=== 来源 ===")
        for src in result["sources"]:
            print(f"[{src['index']}] {src['source']} (页 {src['page']}) 分数={src.get('score')}")
