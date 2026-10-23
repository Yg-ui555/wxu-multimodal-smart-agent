"""
Prometheus 监控指标（2026-10-12 新增）
======================================

# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. Prometheus 4 种指标类型:Counter / Gauge / Histogram / Summary
# 2. 直方图 bucket 设计:覆盖 p50/p90/p99 的延迟区间
# 3. 标签(label)使用规范:避免高基数(比如 uid 不能当 label)
# 4. 导出器集成:prometheus_client.generate_latest() 暴露 /metrics
# 5. 指标命名规范:wxu_<业务>_<指标>_<单位>
#
# 💡 面试高频问题：
#    - "QPS / p99 怎么算？" → Counter 累加 + Histogram 分位数
#    - "怎么监控 LLM 应用的健康度？" → 4 类指标:流量/延迟/错误/饱和度(USE 方法)
#    - "高基数 label 有什么坑？" → 时间序列爆炸,Prometheus 撑爆
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

设计原则:
    - 4 类核心指标(USE 方法):Utilization / Saturation / Errors / Throughput
    - 标签低基数(intent ∈ {qa, schedule, ocr} 等,~10 种)
    - 不暴露 PII(uid/sid 不当 label)
    - 与 answer_cache 联动:cache 命中率作为独立指标

暴露方式:
    from prometheus_client import generate_latest
    from src.web_app.monitoring import metrics
    @app.get("/metrics")
    def metrics_endpoint():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
"""
import os
import time
import logging
from contextlib import contextmanager
from typing import Optional, Dict, Any

from prometheus_client import (
    Counter, Gauge, Histogram, Summary,
    CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST,
    REGISTRY,
)

log = logging.getLogger("monitoring")


# ============================================================
# 1) 流量指标 (Counter)
# ============================================================
# QPS = rate(wxu_qa_requests_total[1m])
# 按意图(intent) 和 状态(status) 切分标签
REQUESTS_TOTAL = Counter(
    "wxu_qa_requests_total",
    "QA 请求总数(按意图和状态切分)",
    ["intent", "status"],  # status: success / fallback / error
)


# ============================================================
# 2) 延迟指标 (Histogram)
# ============================================================
# p50/p90/p99 = histogram_quantile(0.5/0.9/0.99, rate(wxu_qa_latency_seconds_bucket[5m]))
# bucket 设计:覆盖 0.1s ~ 30s(校园问答典型延迟范围)
LATENCY_SECONDS = Histogram(
    "wxu_qa_latency_seconds",
    "端到端请求延迟(秒)",
    ["intent"],
    buckets=(0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 15.0, 30.0),
)

# RAG 检索阶段单独监控(快速定位是检索慢还是 LLM 慢)
# 2026-10-12 收尾:暂未启用(在 retriever 内部加 stage observe),需要时取消注释
# RAG_RETRIEVE_LATENCY = Histogram(
#     "wxu_rag_retrieve_seconds",
#     "RAG 检索阶段延迟(秒)",
#     ["stage"],  # stage: bm25 / vector / rrf / llm
#     buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
# )

# Agent 工具调用延迟
TOOL_CALL_LATENCY = Histogram(
    "wxu_agent_tool_call_seconds",
    "Agent 工具调用延迟(秒)",
    ["tool_name"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0),
)


# ============================================================
# 3) 错误指标 (Counter)
# ============================================================
ERRORS_TOTAL = Counter(
    "wxu_qa_errors_total",
    "错误总数(按类型切分)",
    ["error_type"],  # error_type: llm_error / rag_error / timeout / validation
)


# ============================================================
# 4) 饱和度指标 (Gauge)
# ============================================================
# 当前在飞请求数(Gauge)→ 用来报警 "服务积压"
IN_FLIGHT = Gauge(
    "wxu_qa_in_flight",
    "当前在处理中的请求数",
)


# ============================================================
# 5) 业务指标 (Cache / Tool calls)
# ============================================================
CACHE_HITS = Counter(
    "wxu_cache_hits_total",
    "缓存命中次数",
    ["cache_type"],  # cache_type: answer / embedding
)
CACHE_MISSES = Counter(
    "wxu_cache_misses_total",
    "缓存未命中次数",
    ["cache_type"],
)
CACHE_SKIPPED = Counter(
    "wxu_cache_skipped_total",
    "缓存跳过次数(因个性化问题)",
    ["reason"],  # reason: personal_query
)

TOOL_CALLS = Counter(
    "wxu_agent_tool_calls_total",
    "Agent tool 调用次数(按工具名)",
    ["tool_name"],
)

# 文档检索条数(每次 query 召回多少 doc)
DOCS_RETRIEVED = Histogram(
    "wxu_rag_docs_retrieved",
    "单次检索返回的文档数",
    ["intent"],
    buckets=(0, 1, 2, 3, 4, 5, 8, 10),
)


# ============================================================
# 6) 装饰器 / 上下文管理器
# ============================================================
@contextmanager
def track_latency(histogram, **labels):
    """
    用法:
        with track_latency(LATENCY_SECONDS, intent="qa"):
            do_something()
    """
    start = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - start
        histogram.labels(**labels).observe(elapsed)


@contextmanager
def track_in_flight(gauge: Gauge = IN_FLIGHT):
    """在飞请求数自增/自减"""
    gauge.inc()
    try:
        yield
    finally:
        gauge.dec()


def observe_request(intent: str, status: str, latency_sec: float):
    """
    一次性记录一次请求的全部指标
    Args:
        intent: 意图标签(qa / schedule / ocr / kb / news / general)
        status: success / fallback / error
        latency_sec: 端到端延迟
    """
    REQUESTS_TOTAL.labels(intent=intent, status=status).inc()
    LATENCY_SECONDS.labels(intent=intent).observe(latency_sec)


def observe_cache(cache_type: str, hit: bool):
    """记录缓存命中/未命中"""
    if hit:
        CACHE_HITS.labels(cache_type=cache_type).inc()
    else:
        CACHE_MISSES.labels(cache_type=cache_type).inc()


def observe_tool_call(tool_name: str, latency_sec: float):
    """记录一次工具调用"""
    TOOL_CALLS.labels(tool_name=tool_name).inc()
    TOOL_CALL_LATENCY.labels(tool_name=tool_name).observe(latency_sec)


def observe_error(error_type: str):
    """记录一次错误"""
    ERRORS_TOTAL.labels(error_type=error_type).inc()


# ============================================================
# 7) /metrics 端点
# ============================================================
def render_metrics() -> bytes:
    """
    Prometheus 抓取端点
    用法:
        @app.get("/metrics")
        def metrics_endpoint():
            return Response(render_metrics(), media_type=CONTENT_TYPE_LATEST)
    """
    return generate_latest(REGISTRY)


# ============================================================
# 8) 自测
# ============================================================
if __name__ == "__main__":
    # 模拟 100 个请求,验证指标正确
    import random
    intents = ["qa", "schedule", "ocr", "kb", "news", "general"]
    statuses = ["success", "fallback", "error"]
    for _ in range(100):
        intent = random.choice(intents)
        status = random.choice(statuses)
        latency = random.uniform(0.1, 10.0)
        observe_request(intent, status, latency)

    # 模拟缓存
    for _ in range(50):
        observe_cache("answer", hit=random.random() > 0.5)

    # 模拟工具
    for _ in range(30):
        observe_tool_call("search_school_notices", random.uniform(0.5, 5.0))

    # 输出
    print(render_metrics().decode("utf-8")[:3000])
    print("\n... (truncated)")
    print(f"\n指标总数: {len(REGISTRY.collect())}")
    for family in REGISTRY.collect():
        print(f"  - {family.name}: {len(family.samples)} samples")
