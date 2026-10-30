"""
Agent 侧的 Prometheus 埋点(软依赖包装)
=====================================

为什么单独一个文件:
    指标本身定义在 `src/web_app/monitoring.py`(和 Web 层指标放一起,
    这样 `/metrics` 一个端点就能全导出,10 个指标一处可见)。
    但 `src/agent/*` 不应该硬依赖 `prometheus_client` —— Agent 层要能
    在没装监控依赖的环境里独立跑(评测脚本、单元测试都这样)。

所以这里做一层软导入:
    - 装了就正常上报
    - 没装(或指标注册失败)就静默跳过,绝不影响 Agent 主流程

新增埋点请在这里加包装函数,不要在 src/agent/* 里直接 import prometheus_client。
"""
import logging

log = logging.getLogger("agent.metrics")

try:
    from src.web_app.monitoring import observe_tool_call as _observe_tool_call
except Exception:  # pragma: no cover - 没装 prometheus_client 时的降级路径
    _observe_tool_call = None

try:
    from src.web_app.monitoring import DOCS_RETRIEVED as _DOCS_RETRIEVED
except Exception:  # pragma: no cover
    _DOCS_RETRIEVED = None


def observe_tool_call(tool_name: str, latency_sec: float) -> None:
    """记录一次 Agent 工具调用(次数 + 延迟)。监控故障绝不能影响业务。"""
    if _observe_tool_call is None:
        return
    try:
        _observe_tool_call(tool_name, latency_sec)
    except Exception:
        log.debug("observe_tool_call 失败(忽略)", exc_info=True)


def observe_docs_retrieved(intent: str, count: int) -> None:
    """记录一次检索返回的文档条数。"""
    if _DOCS_RETRIEVED is None:
        return
    try:
        _DOCS_RETRIEVED.labels(intent=intent).observe(count)
    except Exception:
        log.debug("observe_docs_retrieved 失败(忽略)", exc_info=True)
