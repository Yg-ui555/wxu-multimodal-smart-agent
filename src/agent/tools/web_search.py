"""
Tool 4: web_search
作用:联网搜索(兜底 RAG 知识库没覆盖的常识,比如"今天无锡天气")
关键设计:支持 DuckDuckGo 免 key(默认)和 Serper(需 API key,质量更好)
        没装 duckduckgo-search 时优雅降级,不阻塞 Agent 启动
"""
import logging
import os

from langchain_core.tools import tool

log = logging.getLogger("agent.tools.web_search")

# ===== 选项 A:DuckDuckGo 免 key(默认) =====
# 优先用新版 duckduckgo-search(>= 4.0),它有 DDGS().text() API
try:
    from duckduckgo_search import DDGS  # noqa
    _HAS_DDG = True
except ImportError:
    _HAS_DDG = False
    log.warning("duckduckgo-search 未安装,web_search 不可用,"
                "跑: pip install duckduckgo-search")

# ===== 选项 B:Serper(需 SERPER_API_KEY,质量更好) =====
# 免费 100 次/月,生产推荐。代码先备好,需要时切换。
SERPER_KEY = os.getenv("SERPER_API_KEY", "")


def _search_ddg(query: str, top_k: int) -> str:
    """用 DuckDuckGo 搜索(免 key)"""
    if not _HAS_DDG:
        return "(web_search 不可用:未安装 duckduckgo-search。\n" \
               "安装方法: pip install duckduckgo-search\n" \
               "或者改用 Serper: 在 .env 里加 SERPER_API_KEY=xxx)"

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=top_k))
    except Exception as e:
        log.exception("DuckDuckGo 搜索失败")
        return f"(DuckDuckGo 搜索失败: {e})"

    if not results:
        return f"(没搜到 '{query}' 的相关结果)"

    parts = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        body = r.get("body", "")[:300]   # 摘要截断
        href = r.get("href", "")
        parts.append(f"[{i}] {title}\n{body}\n链接: {href}")
    return "\n\n".join(parts)


def _search_serper(query: str, top_k: int) -> str:
    """用 Serper(Google 结果)搜,需 SERPER_API_KEY"""
    if not SERPER_KEY:
        return "(Serper 未配置 SERPER_API_KEY,回退到 DuckDuckGo)"
    try:
        import requests
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": top_k},
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        return f"(Serper 搜索失败: {e})"

    results = data.get("organic", [])
    if not results:
        return f"(没搜到 '{query}' 的相关结果)"

    parts = []
    for i, r in enumerate(results[:top_k], 1):
        title = r.get("title", "")
        snippet = r.get("snippet", "")[:300]
        link = r.get("link", "")
        parts.append(f"[{i}] {title}\n{snippet}\n链接: {link}")
    return "\n\n".join(parts)


@tool
def web_search(query: str, top_k: int = 5) -> str:
    """搜索互联网(用于 RAG 知识库没覆盖到的常识)。

    适用场景:
        - 实时信息(今天天气 / 最新新闻)
        - 通识问题(Python 怎么学 / 高考怎么报名)
        - RAG 检索没命中时的兜底

    Args:
        query: 搜索关键词(简洁的查询串,不要写完整问句)
        top_k: 返回前几条结果(默认 5)

    Returns:
        拼接好的搜索结果(标题 + 摘要 + 链接),用 [1] [2] 编号,LLM 引用时直接说"根据 [1]..."。
    """
    # 优先用 Serper(质量更好),失败/没配回退到 DuckDuckGo
    if SERPER_KEY:
        result = _search_serper(query, top_k)
        if not result.startswith("("):  # 成功
            return result
        log.warning(f"Serper 失败,回退 DuckDuckGo: {result}")

    return _search_ddg(query, top_k)
