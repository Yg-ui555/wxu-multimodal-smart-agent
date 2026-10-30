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

# Prometheus 埋点(软依赖)
from src.agent.metrics import observe_docs_retrieved

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


def _parse_date_range(s: str) -> tuple[datetime | None, datetime | None]:
    """把松散日期解析成 (起, 止) 区间,**保留原有粒度**。

    2026-10-30 修复:原来只用 strptime 解析成"某个瞬间",粒度信息全丢了 ——
    '2026' 被当成 2026-01-01、'2026-09' 被当成 2026-09-01。
    于是 30 天窗口会把 2026 全年、2026-09 的文档统统误判成"过期",
    哪怕文档实际是 2026-09-28 发的。现在按粒度还原成区间:

        '2026-09-21' -> (2026-09-21, 2026-09-21)
        '2026-09'    -> (2026-09-01, 2026-09-30)
        '2026'       -> (2026-01-01, 2026-12-31)
        '' / 非法     -> (None, None)  # 未知日期

    抓取器抽出来的 date 有大量垃圾值('2050'、'2018-20' 这种),
    所以解析必须容错,不能抛异常。
    """
    s = (s or "").strip()
    if not s:
        return (None, None)
    try:
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            d = datetime.strptime(s, "%Y-%m-%d")
            return (d, d)
        if len(s) == 7 and s[4] == "-":
            d = datetime.strptime(s, "%Y-%m")
            # 当月最后一天
            if d.month == 12:
                last = d.replace(day=31)
            else:
                last = d.replace(month=d.month + 1, day=1) - timedelta(days=1)
            return (d, last)
        if len(s) == 4 and s.isdigit():
            y = int(s)
            d = datetime(y, 1, 1)
            return (d, datetime(y, 12, 31, 23, 59, 59))
    except (ValueError, OverflowError):
        return (None, None)
    return (None, None)


def _within_window(date_str: str, cutoff: datetime, now: datetime) -> bool:
    """文档时间区间跟窗口 [cutoff, now] 是否重叠。

    - 未知日期(抓取器没抽到 / 抽到垃圾)→ 一律保留,别把内容误杀
    - 起始时间远在未来(>1 年)→ 判定为脏数据,按"未知"处理
    - 正常情况:区间末端落在 cutoff 之后即算命中(粒度是月/年时尤其重要)
    """
    start, end = _parse_date_range(date_str)
    if start is None or end is None:
        return True
    if start > now + timedelta(days=365):
        # '2098' 这种未来年份是抓取噪声,当未知处理
        return True
    return end >= cutoff


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
        注意:时间窗是"软过滤" —— 如果窗口内一条都没有,会自动放宽并注明,
        避免把高相关度的历史文档全丢掉(用户问"开学时间"这类问题很常见)。
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
    #    注意:不重新跑全库,只过滤已经召回的 4 个 doc,0 额外开销
    #
    #    2026-10-30 修复:过滤后如果一个都不剩,原来直接返回"未找到"。
    #    但用户问"开学时间""往年通知"这种历史性问题时,召回的 4 条几乎
    #    都是老文档,时间窗一卡就全被丢掉 → 工具明明检索到了却说没找到。
    #    现在:全被过滤掉时**放宽窗口**,退回未过滤的结果并注明。
    now = datetime.now()
    cutoff = now - timedelta(days=days)
    filtered = [d for d in docs
                if _within_window((d.metadata or {}).get("date", ""), cutoff, now)]
    relaxed = False
    if not filtered:
        filtered = docs
        relaxed = True

    # 4. 格式化输出,带 [来源] 标签方便 LLM 引用
    parts = []
    for d in filtered:
        src = (d.metadata or {}).get("source", "未知来源")
        page = (d.metadata or {}).get("page", "")
        page_tag = f" (第 {page} 页)" if page else ""
        parts.append(f"[来源: {src}{page_tag}]\n{d.page_content}")
    body = "\n\n---\n\n".join(parts)
    if relaxed:
        body = (f"(以下内容的时间不在最近 {days} 天内,已放宽限制)\n\n" + body)

    # Prometheus:本次最终交给 LLM 的文档条数
    observe_docs_retrieved("kb", len(filtered))
    return body
