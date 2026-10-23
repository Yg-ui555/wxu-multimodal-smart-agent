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

# 2. 启动 Ollama（如果用本地嵌入模型）
ollama serve &
ollama pull bge-m3
ollama pull bge-reranker-v2-m3

# 3. 启动 FastAPI
powershell -ExecutionPolicy Bypass -File start.ps1
# 或手动:
# venv311\Scripts\python.exe src\web_app\fastapi_app.py
```

## 3. Docker Compose（生产环境）

参考 `docker-compose.yml`：

```yaml
version: '3.8'

services:
  fastapi:
    build: .
    image: wxu-smart-agent:latest
    ports:
      - "7862:7862"
    environment:
      - LLM_API_KEY=${LLM_API_KEY}
      - LLM_BASE_URL=${LLM_BASE_URL}
      - LLM_MODEL=${LLM_MODEL}
      - REDIS_URL=redis://redis:6379/0
      - USE_MULTIHOP=0   # 多跳默认关,需要时改 1
    depends_on:
      - redis
      - ollama
    volumes:
      - ./data:/app/data   # 向量库 + 文档
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7862/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped
    command: redis-server --appendonly yes   # 持久化

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    restart: unless-stopped
    # 首次启动需要拉模型:
    # docker exec -it <container> ollama pull bge-m3

  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    restart: unless-stopped

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana_data:/var/lib/grafana
    restart: unless-stopped

volumes:
  redis_data:
  ollama_data:
  grafana_data:
```

## 4. Prometheus 抓取配置

`prometheus.yml`：

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'wxu-fastapi'
    static_configs:
      - targets: ['fastapi:7862']
    metrics_path: /metrics
```

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

## 9. 监控告警规则

`prometheus.yml` 加 alert rules：

```yaml
groups:
- name: wxu_alerts
  rules:
  - alert: HighP99Latency
    expr: histogram_quantile(0.99, rate(wxu_qa_latency_seconds_bucket[5m])) > 10
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "P99 延迟超过 10s"

  - alert: HighErrorRate
    expr: rate(wxu_qa_errors_total[5m]) > 0.1
    for: 2m
    labels:
      severity: critical
    annotations:
      summary: "错误率超过 10%"

  - alert: LowCacheHitRate
    expr: rate(wxu_cache_hits_total[5m]) / (rate(wxu_cache_hits_total[5m]) + rate(wxu_cache_misses_total[5m])) < 0.3
    for: 10m
    labels:
      severity: warning
    annotations:
      summary: "缓存命中率低于 30%"
```

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
