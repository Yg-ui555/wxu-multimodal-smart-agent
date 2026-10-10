"""
Tool 1: search_school_notices
作用:在校园通知/资料库里搜索最近 N 天的相关内容(BM25 + 向量 RRF 混合检索)
为什么叫"真 tool":不写死意图路由,由 LLM 自主决定"我要查资料"时调它
"""
import logging
from datetime import datetime, timedelta

from langchain_core.tools import tool

# 复用项目里现有的 RAG 检索器(已经在 src/rag/retriever.py 实现了混合检索)
from src.rag.retriever import HybridRetriever

log = logging.getLogger("agent.tools.search_school_notices")

# 单例 retriever(避免每次调都重新加载向量库,30 秒冷启动受不了)
_retriever: HybridRetriever | None = None


def _get_retriever() -> HybridRetriever | None:
    """懒加载 + 单例 RAG 检索器。失败时返回 None(降级到空结果)"""
    global _retriever
    if _retriever is not None:
        return _retriever
    try:
        from src.rag.vector_store import VectorStoreManager
        manager = VectorStoreManager(persist_directory="data/faiss_db_bge")
        manager.load_vectorstore()
        # 关键参数:开 BM25 + 关 reranker(你 README 决策,CPU 太慢)
        # 8/8 双路:vector 8 + BM25 8,RRF 融合
        _retriever = HybridRetriever(
            manager.vectorstore,
            documents=manager._documents,
            top_k=4,                 # 最终给 LLM 看 4 个
            use_bm25=True,
            use_reranker=False,      # 你已经在 README 评估里决定关掉
            vector_top_k=8,
            bm25_top_k=8,
        )
        log.info("RAG 检索器单例初始化成功")
        return _retriever
    except Exception as e:
        log.error(f"RAG 检索器初始化失败: {e!r}")
        return None


def _parse_loose_date(s: str) -> datetime | None:
    """宽松解析:接受 '2025-09-15' / '2025-09' / '2025' 三种格式,失败返回 None"""
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


@tool
def search_school_notices(query: str, days: int = 30) -> str:
    """在校园通知/资料库里搜索最近 N 天的相关内容。

    Args:
        query: 用户要查的关键词或问题(用用户原话即可,不用改写)
        days: 只看最近 N 天的内容。默认 30 天;想查历史性的(比如"开学时间")
              传 365;想查最新通知(比如"今天发了什么")传 7。

    Returns:
        命中的文本片段,每段前面带 [来源文件名] 标签,方便 LLM 在答案里引用。
        如果没找到,返回 "(未找到相关内容)"。
    """
    retriever = _get_retriever()
    if retriever is None:
        return "(RAG 检索器暂不可用,服务启动中或向量库缺失)"

    # 1. 召回(走项目现有的混合检索 BM25+向量+RRF)
    docs = retriever.retrieve(query)

    if not docs:
        return "(未找到相关内容)"

    # 2. 截断到 top 4(防 LLM 上下文爆炸)
    docs = docs[:4]

    # 3. 时间窗过滤(从 metadata.date 字段判断)
    #    2026-09-21 之前的旧文档过滤掉
    #    注意:不重新跑全库,只过滤已经召回的 4 个 doc,0 额外开销
    cutoff = datetime.now() - timedelta(days=days)
    filtered = []
    for d in docs:
        date_str = (d.metadata or {}).get("date", "")
        doc_date = _parse_loose_date(date_str)
        if doc_date is None or doc_date >= cutoff:
            filtered.append(d)

    if not filtered:
        return f"(最近 {days} 天内未找到相关内容,试试把 days 调大到 180 或 365)"

    # 4. 格式化输出,带 [来源] 标签方便 LLM 引用
    parts = []
    for d in filtered:
        src = d.metadata.get("source", "未知来源")
        page = d.metadata.get("page", "")
        page_tag = f" (第 {page} 页)" if page else ""
        parts.append(f"[来源: {src}{page_tag}]\n{d.page_content}")
    return "\n\n---\n\n".join(parts)
