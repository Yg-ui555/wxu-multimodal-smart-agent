"""
多跳检索器（Multi-hop Retriever）—— 2026-10-12 新增
=====================================================

# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. 多跳问答 (Multi-hop QA)：一个 query 拆成多个子问题分别检索再合并
# 2. Query Decomposition（问题分解）：用 LLM 把复杂问题拆成独立子查询
# 3. 跨文档召回：传统单跳检索对"X 课程教室在哪"这种题无能为力
#    ——它需要先召回调课表（拿到课程名）→ 再召回教室分布（拿到位置）
# 4. RRF 融合：多路检索结果按 1/(k+rank) 合并去重
#
# 💡 面试高频问题：
#    - "RAG 怎么处理复杂问题？" → 多跳检索 + Query Decomposition
#    - "为什么不直接让 LLM 自己改写 query？" → 单纯改写检索精度提升有限，
#      拆成子问题后每跳独立检索，召回率显著高于单跳改写
#    - "多跳检索有什么坑？" → 子问题之间可能语义漂移、检索成本 N 倍
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

解决问题：
    用户问："电子信息工程专业大三上学期的课在哪里上？"
    单跳检索：召回"电子信息工程培养方案"（给了课程名）+ 召回失败（教室分布不在这篇）
    多跳检索：
        子问题1: "电子信息工程专业大三上有哪些必修课"  → 召回培养方案
        子问题2: "无锡学院教学楼教室分布"             → 召回教室分布
    RRF 合并后两篇都进 top-4，LLM 能看到完整证据

调用：
    from src.rag.multi_hop import MultiHopRetriever
    from src.rag.retriever import create_retriever
    from src.rag.llm_chain import create_qa_system

    base = create_retriever(vectorstore, documents=docs)
    qa = create_qa_system(base)
    multi = MultiHopRetriever(base_retriever=base, llm=qa.llm)
    docs = multi.retrieve("电子信息工程大三上在哪上课")
    # docs 是 RRF 融合后的 top_k 文档
"""
import json
import re
import logging
from typing import List, Optional, Dict, Any

from langchain_core.documents import Document

log = logging.getLogger("multi_hop")


# ============================================================
# 1) Query Decomposition Prompt
# ============================================================
# 设计要点：
#   - 明确"闲聊/单点"返回原 query（避免 LLM 过度拆解）
#   - 强制 JSON 输出格式（便于解析）
#   - 子问题之间要"独立可查"（不互相依赖）
#   - 保留关键实体（课程名、地点、时间等专有名词不能丢）
DECOMPOSE_PROMPT = """你是校园助手中的"问题拆解员"。用户的复杂问题往往需要查**多份文档**才能完整回答。
请把它拆成 2-3 个**互相独立、各自可查**的子问题。

## 拆解规则
1. 子问题之间不能互相依赖（每个子问题单独搜都能搜到东西）
2. 保留关键实体（课程名、地点、班级、时间、文号）
3. 闲聊/单点查询（如"你好"、"图书馆在哪"）→ 直接返回原问题，不要拆
4. 最多拆 3 个，拆不出就 1 个

## 输出格式
仅返回 JSON 数组（不要其他任何文字）：
["子问题1", "子问题2", "子问题3"]

## 示例
用户问题："电子信息工程专业大三上学期的课在哪里上？"
输出：["电子信息工程专业大三上有哪些必修课", "无锡学院教学楼教室分布"]

用户问题："奖学金和国家助学金能同时拿吗？"
输出：["奖学金兼得规则", "国家助学金申请条件"]

用户问题："你好"
输出：["你好"]

## 真实问题
用户问题：{query}
输出："""


# ============================================================
# 2) 工具函数
# ============================================================
def _extract_json_array(text: str) -> Optional[List[str]]:
    """
    从 LLM 输出里抠出 JSON 数组
    为什么要这个：
        LLM 不一定严格遵守"只输出 JSON"指令，可能包一堆解释文字。
        容错做法：用正则抠第一个 [...] 块，json.loads 解析。
        解析失败返回 None，让上层降级为单跳。
    """
    if not text:
        return None
    # 先尝试整段解析
    try:
        arr = json.loads(text)
        if isinstance(arr, list):
            return [str(x) for x in arr if x]
    except Exception:
        pass
    # 退化：抠第一个 [...] 块
    m = re.search(r"\[.*?\]", text, re.S)
    if m:
        try:
            arr = json.loads(m.group(0))
            if isinstance(arr, list):
                return [str(x) for x in arr if x]
        except Exception:
            return None
    return None


# ============================================================
# 3) MultiHopRetriever 主类
# ============================================================
class MultiHopRetriever:
    """
    多跳检索器：包装一个基础混合检索器，加一层 query decomposition + RRF 融合
    关键设计：
        - 复用现有 HybridRetriever，每一跳独立跑（不重新实现 BM25/向量）
        - 子问题之间独立检索，结果去重后按 RRF 排序
        - 任何环节失败都降级为单跳（保证可用性）
    """

    def __init__(
        self,
        base_retriever,           # HybridRetriever 实例
        llm=None,                  # 用于 query 拆解的 LLM（ChatModel）
        max_sub_queries: int = 3,  # 最多拆几个子问题
        top_k: int = 4,            # 最终返回几个文档
        rrf_k: int = 60,           # RRF 常数（一般 60）
    ):
        """
        Args:
            base_retriever: 已初始化好的 HybridRetriever（必须有 .retrieve() 方法）
            llm: 用于 query decomposition 的 LLM（必须支持 .invoke()）
                 如果传 None，会尝试从 base_retriever 上下文里找（如果有的话）
            max_sub_queries: 最多拆几个子问题（防 LLM 拆上瘾）
            top_k: 最终返回文档数
            rrf_k: RRF 融合常数 k
        """
        self.base = base_retriever
        self.llm = llm
        self.max_sub = max_sub_queries
        self.top_k = top_k
        self.rrf_k = rrf_k

        # 统计指标（评估用）
        self.stats = {
            "total_queries": 0,
            "decomposed_queries": 0,    # 真正拆成 ≥2 个子问题的
            "avg_sub_queries": 0.0,     # 平均子问题数
            "sub_query_retrieves": 0,   # 总共跑了多少跳
        }

    # ---------------- 拆解 ----------------
    def _decompose(self, query: str) -> List[str]:
        """
        用 LLM 把 query 拆成子问题列表
        Returns:
            List[str]: 子问题列表（1-N 个）
            失败/闲聊时返回 [query]（降级为单跳）
        """
        if not self.llm:
            log.debug("multi_hop: 无 LLM，降级为单跳")
            return [query]

        prompt = DECOMPOSE_PROMPT.format(query=query)
        try:
            resp = self.llm.invoke(prompt)
            # ChatModel 返回 AIMessage；用 .content 兼容
            content = (getattr(resp, "content", None) or str(resp)).strip()
            arr = _extract_json_array(content)
            if not arr:
                log.debug("multi_hop: LLM 输出无法解析为 JSON，降级: %s", content[:80])
                return [query]
            # 截断到 max_sub
            arr = arr[:self.max_sub]
            return arr if arr else [query]
        except Exception as e:
            log.warning("multi_hop: decompose 异常，降级为单跳: %r", e)
            return [query]

    # ---------------- RRF 融合 ----------------
    def _rrf_merge(
        self,
        doc_lists: List[List[Document]],
        top_n: Optional[int] = None,
    ) -> List[Document]:
        """
        Reciprocal Rank Fusion: 多路结果按 1/(k+rank) 合并去重
        关键：用 (source, content_hash) 当主键，同一篇文档只保留第一次出现的实例

        Args:
            doc_lists: 多个 Document 列表（每个已按相关性排序）
            top_n: 返回前 N 个（None 用 self.top_k）
        """
        top_n = top_n or self.top_k
        scores: Dict[str, float] = {}
        doc_map: Dict[str, Document] = {}

        for docs in doc_lists:
            for rank, d in enumerate(docs):
                # 主键：source + content_hash（同一篇文档在不同跳里能识别出来）
                key = f"{d.metadata.get('source','')}|{d.metadata.get('content_hash', id(d))}"
                if key not in doc_map:
                    doc_map[key] = d
                # RRF 公式：score = Σ 1/(k + rank + 1)
                scores[key] = scores.get(key, 0.0) + 1.0 / (self.rrf_k + rank + 1)

        # 按融合分数降序
        sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        return [doc_map[k] for k in sorted_keys[:top_n]]

    # ---------------- 公共接口 ----------------
    def retrieve(self, query: str) -> List[Document]:
        """
        多跳检索主入口
        Args:
            query: 用户原始问题
        Returns:
            融合后的 top_k 文档列表
        """
        self.stats["total_queries"] += 1

        # 1) 拆解成子问题
        sub_queries = self._decompose(query)
        n_sub = len(sub_queries)
        self.stats["sub_query_retrieves"] += n_sub
        if n_sub >= 2:
            self.stats["decomposed_queries"] += 1
        # 累计平均（移动平均：简单累加后除以总数）
        self.stats["avg_sub_queries"] = round(
            (self.stats["avg_sub_queries"] * (self.stats["total_queries"] - 1) + n_sub)
            / self.stats["total_queries"], 3
        )

        log.info("[multi-hop] '%s...' → %d 子问题", query[:30], n_sub)
        for i, sq in enumerate(sub_queries):
            log.info("[multi-hop]   hop-%d: %s", i + 1, sq)

        # 2) 每跳独立检索
        per_hop_results: List[List[Document]] = []
        for sq in sub_queries:
            try:
                docs = self.base.retrieve(sq)
                per_hop_results.append(docs)
            except Exception as e:
                log.warning("[multi-hop] hop '%s' 检索失败: %r（跳过）", sq[:30], e)
                per_hop_results.append([])

        # 3) RRF 融合（自动去重）
        merged = self._rrf_merge(per_hop_results, top_n=self.top_k)
        log.info("[multi-hop] RRF 合并: %d 跳 → %d 文档（去重后）",
                 len(sub_queries), len(merged))
        return merged

    def retrieve_with_sources(self, query: str):
        """
        兼容 HybridRetriever 接口，返回 (context_str, sources_list)
        用于 LLM 问答链直接调用
        """
        docs = self.retrieve(query)
        context = "\n\n".join(d.page_content for d in docs)
        sources = []
        for i, d in enumerate(docs):
            sources.append({
                "index": i + 1,
                "content": (d.page_content[:200] + "...") if len(d.page_content) > 200 else d.page_content,
                "source": d.metadata.get("source", "未知"),
                "page": d.metadata.get("page", ""),
                "date": d.metadata.get("date", ""),
                "category": d.metadata.get("category", ""),
                # 标记哪一跳召回的（评估用）
                "from_hop": d.metadata.get("_from_hop", 0),
            })
        return context, sources

    def get_stats(self) -> Dict[str, Any]:
        """返回运行统计（评估用）"""
        return dict(self.stats)

    def reset_stats(self):
        """重置统计"""
        self.stats = {
            "total_queries": 0,
            "decomposed_queries": 0,
            "avg_sub_queries": 0.0,
            "sub_query_retrieves": 0,
        }


# ============================================================
# 4) 自测
# ============================================================
if __name__ == "__main__":
    # 简单自测：拆解能力（不需要真实 RAG）
    import os
    from dotenv import load_dotenv
    load_dotenv()

    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        temperature=0.0,  # 自测时温度=0，结果稳定
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    )

    # 用一个假 retriever 验证拆解
    class FakeRetriever:
        def retrieve(self, q):
            return []

    multi = MultiHopRetriever(base_retriever=FakeRetriever(), llm=llm, top_k=4)
    for q in [
        "电子信息工程专业大三上学期的课在哪里上？",
        "你好",
        "奖学金和国家助学金能同时拿吗？",
    ]:
        subs = multi._decompose(q)
        print(f"\nQ: {q}")
        print(f"   → {subs}")
