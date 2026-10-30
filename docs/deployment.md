# 部署文档（2026-10-12 新增）

> 生产环境部署指南：FastAPI + Redis（可选）+ Ollama + Prometheus + Grafana

---

## 1. 架构总览

```
                      ┌────────────────┐
                      │  Nginx/Caddy   │  ← 反向代理 + HTTPS
                      └───────┬────────┘
                              │
                  ┌───────────▼──────────┐
                  │   FastAPI (Uvicorn)  │  ← 主服务
                  │   - 7862 端口         │
                  │   - 多 worker         │
                  │   - Prometheus /metrics
                  └───────┬──────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
   ┌────▼─────┐    ┌──────▼──────┐    ┌────▼────┐
   │  Redis   │    │   Ollama    │    │ FAISS   │
   │ (缓存)   │    │  (本地LLM)  │    │ (向量库)│
   │ 6379     │    │  11434      │    │  本地   │
   └──────────┘    └─────────────┘    └─────────┘
```

## 2. 一键启动（开发环境）

```bash
# 1. 启动 Redis（如果需要真 Redis 而不是 fakeredis）
docker run -d -p 6379:6379 --name wxu-redis --restart unless-stopped redis:7-alpine

# 2. 启动 Ollama（本地嵌入模型）
ollama serve &
ollama pull bge-m3
# 注意:重排(reranker)不走 Ollama。代码里的重排后端是 HuggingFace CrossEncoder
#      (BAAI/bge-reranker-v2-m3,见 src/rag/retriever.py 的 BGEReranker),
#      而且生产默认 use_reranker=False(本地 CPU 太慢,已做过评估)。
#      只有跑消融实验才需要重排,首次调用会自动从 HuggingFace 下载(约 2.2GB)。

# 3. 启动 FastAPI
powershell -ExecutionPolicy Bypass -File start.ps1
# 或手动:
# venv311\Scripts\python.exe src\web_app\fastapi_app.py
```

## 3. Docker Compose（生产环境）

仓库根目录的 **`docker-compose.yml`** 就是可直接跑的那份（不是示例片段）：

```bash
cp .env.example .env      # 填 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
docker compose up -d
docker compose logs -f fastapi
```

包含 5 个服务：

| 服务 | 作用 | 端口 |
|---|---|---|
| `fastapi` | 主服务（`Dockerfile` 构建，`build: .`） | 7862 |
| `redis` | 答案缓存（带 AOF 持久化） | 6379 |
| `ollama` | 本地嵌入 / 重排模型 | 11434 |
| `prometheus` | 抓 `/metrics` + 加载告警规则 | 9090 |
| `grafana` | 看板（默认账号密码 `admin`，生产请改 `GRAFANA_PASSWORD`） | 3000 |

> `data/`（向量库 + 文档）和 `logs/` 用 bind mount 挂进容器，**不进镜像**——
> 向量库几百 MB 且含校内资料，构建上下文里也用 `.dockerignore` 排掉了。

## 4. Prometheus 抓取配置

仓库根目录的 **`prometheus.yml`**：15 秒抓一次 `fastapi:7862/metrics`，
并通过 `rule_files` 加载 **`prometheus-alerts.yml`**（告警规则单独放，便于评审和单独改）。

## 5. Grafana Dashboard 关键指标

导入 Dashboard ID `14777`（Prometheus 官方示例），或者手动加这些 panel：

| Panel | 指标 | 用途 |
|---|---|---|
| QPS | `rate(wxu_qa_requests_total[1m])` | 每秒请求数 |
| p50 延迟 | `histogram_quantile(0.5, rate(wxu_qa_latency_seconds_bucket[5m]))` | 中位数 |
| p99 延迟 | `histogram_quantile(0.99, rate(wxu_qa_latency_seconds_bucket[5m]))` | 长尾 |
| 缓存命中率 | `rate(wxu_cache_hits_total[5m]) / (rate(wxu_cache_hits_total[5m]) + rate(wxu_cache_misses_total[5m]))` | 缓存效果 |
| 错误率 | `rate(wxu_qa_errors_total[5m])` | 异常监控 |
| 在飞请求 | `wxu_qa_in_flight` | 积压监控 |

## 6. 健康检查

```bash
# 基础健康
curl http://localhost:7862/health

# Prometheus 抓取
curl http://localhost:7862/metrics | grep wxu_

# 缓存状态
curl -s http://localhost:7862/health | python -m json.tool
```

## 7. 缓存策略

| 缓存类型 | 失效条件 | TTL |
|---|---|---|
| Answer Cache（Redis） | query 命中 + 非个性化 | 7 天（±10% 抖动）|
| Query Embedding（LRU） | query 命中 | 5 分钟 |
| BM25 索引（pickle） | 向量库更新 | 永久 |
| FAISS 向量库 | 文档变化 | 永久 |

**降级策略**：
- Redis 不可用 → 自动 fakeredis
- fakeredis 不可用 → 进程内 LRU
- **永远不会让缓存层挂掉业务**

## 8. 多跳检索开关

`.env`：
```
USE_MULTIHOP=1   # 启用,跨文档题走 query 拆解 + RRF 融合
USE_MULTIHOP=0   # 默认,单跳
```

> ⚠️ 这个开关只被 `src/rag/rag_engine.py` 读取（由它包一层 `MultiHopRetriever`）。
> **线上 Web 聊天走的是 ReAct/LangGraph Agent**，其检索工具
> `src/agent/tools/search_school_notices.py` 自建 `HybridRetriever`，**不读这个开关** —— 线上默认仍是单跳。
> 多跳的数据来自离线评测 `scripts/eval_multihop.py`。

## 9. 监控告警规则

告警规则在仓库根目录的 **`prometheus-alerts.yml`**（由 `prometheus.yml` 的 `rule_files` 加载）：

| 告警 | 触发条件 | 级别 |
|---|---|---|
| `HighP99Latency` | P99 延迟 > 10s 持续 5 分钟 | warning |
| `HighErrorRate` | 错误率 > 0.1/s 持续 2 分钟 | critical |
| `LowCacheHitRate` | 缓存命中率 < 30% 持续 10 分钟 | warning |
| `ServiceBacklog` | 在飞请求数 > 20 持续 5 分钟 | warning |

> 阈值是**拍的起步值**，生产环境要按自己的 SLO 调 —— 面试时最好这么讲，
> 而不是「这个阈值是精确算出来的」。

## 10. 压测验证

```bash
# 启动被压测服务
python src/web_app/fastapi_app.py

# 另一个终端跑 locust（30 并发 × 90s）
locust -f tests/locustfile.py --host=http://localhost:7862 -u 30 -r 5 -t 90s --headless --html tests/load_test_report.html
```

参考输出：30 并发下 RPS ~5（受 slowapi 限流 30/min 限制），p50 延迟 ~3s（缓存命中时 <100ms）。

---

## 11. 故障排查清单

| 现象 | 排查 |
|---|---|
| `/health` 返回 `rag_ready: false` | 查 `logs/app.log` 看 RAG 初始化失败原因 |
| `/metrics` 没有 wxu_ 前缀 | RAG 还没初始化完成，访问 `/api/chat` 触发 |
| 缓存命中率 0% | 检查 query 是否被识别为"个性化"（含"今天/我的"） |
| p99 延迟 > 30s | 查向量库大小、LLM API 是否限流 |
| Redis 连不上 | 进程自动降级到 fakeredis，不影响业务 |
