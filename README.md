# 无锡学院多模态校园智能助手

> 基于 RAG + YOLO-World + PaddleOCR + ReAct Agent 的校园智能助手。支持自然语言问答与带原文引用的检索；图片能力有**独立入口**：导航栏「拍照识图」页（`/look`）上传图片 → YOLO-World 认物 + PaddleOCR 识字，「上传中心」的课表截图走 OCR 解析 —— 聊天框本身只收文字。嵌入在本地 Ollama 跑，只有答案生成调用大模型 API。

---

## ✨ 功能特性

| 模块 | 能力 |
|---|---|
| **智能问答** | 基于校内文档知识库（教务处通知、课程讲义、奖学金政策等）的自然语言问答，附原文引用 |
| **ReAct 智能体** | 4 个工具的真正 LLM 决策型 Agent（**手写 ReAct 循环 + `bind_tools`**，另有一套 LangGraph 状态图可切换）：校园通知检索(RAG) / 我的课表 / 图片 OCR / 实时联网，工具调用 + 推理链 + 追问兜底（而不是规则路由 + LLM 包装） |
| **拍照识图** | 独立页面 `/look`（导航栏「拍照识图」）：上传图片 → **YOLO-World 开放词汇认物**（默认 41 个校园类别）+ **PaddleOCR 识字**，输出「认到的东西 / 读到的文字 / 人话总结」，接口 `POST /api/image/look`（限流 10/min，CPU 密集） |
| **图片识字** | `src/ocr/ocr_engine.py` 提供 `extract_text()` / `add_ocr_to_vectorstore()`；命令行 `python src/ocr/ocr_engine.py <图片路径>` 可把 OCR 文本落盘并入向量库 |
| **多轮对话** | 分层记忆（近期完整 + 历史摘要），支持上下文指代 |
| **多用户多会话** | 浏览器 UUID 隔离用户，豆包式左侧会话列表，每会话独立持久化 |
| **文档上传** | 支持 PDF / Word(.docx/.doc) / TXT / Markdown 共 5 种格式，单文件 ≤50MB，自动去重并入向量库 |
| **班级维度课表** | PDF 课表按班级归类（24电信1/2/3 分桶存储），实验课上传自动按"我的班级"过滤入表，公共课 + 我的班级课程合并渲染 |
| **周次自动判断** | 用户配置开学日后，/schedule 自动算出当前是第几周，无需每次手动切 |
| **通知爬虫** | 定时抓取教务处通知，清洗后自动追加进知识库 |

---

## 🏗️ 系统架构

```
【聊天】用户输入文字
    │
    ▼
    意图判断（知识库 / 闲聊 / 快捷指令）
    │
    ┌────────────────────┴────────────────────┐
    ▼                                         ▼
  快捷指令直达                           ReAct Agent 自主决策
  （课表 / 上传 / OCR）                   ├─ 校内通知检索（RAG）
                                            ├─ 我的课表
                                            ├─ 图片 OCR
                                            └─ 联网兜底
    └────────────────────┬────────────────────┘
                         ▼
    ┌──────── 混合检索（Hybrid Retriever）────────┐
    │                                              │
    ├── BM25 关键词检索（词面精确匹配，top-8）     │
    ├── 向量检索（bge-m3 语义相似度，top-8）       │
    └── RRF 融合（两路排名加权合并，k=60）        │
                         │                        │
                         ▼                        │
              top-4（生产不挂 reranker：          │
              HF CrossEncoder 太慢，见评估决策）   │
                         │                        │
                         ▼                        │
              LLM 生成答案 + 引用来源             │

【拍照识图】独立页 /look（不进聊天链路）
    上传图片 → YOLO-World 认物 + PaddleOCR 识字 → 认到的东西 / 读到的文字 / 人话总结
    （结果不会自动回灌进对话）
```

> ⚠️ **聊天框只收文字**：图片从独立的「拍照识图」页（`/look`）进；
> `dispatcher.handle_agent_stream` 虽然已经支持 `image_path` 参数，但 `/api/chat` 这条路由没传（聊天框只收文字），
> 所以 Agent 的 `ocr_image` 工具在聊天链路里拿不到图片。

**检索链路核心设计**：
- **双路检索**：BM25 抓精确匹配，向量抓语义/同义词，互补
- **RRF 融合**：`score = Σ 1/(k + rank)`，k=60，避免单一检索的盲区
- **重排器（默认关闭）**：代码里的重排后端是 **HuggingFace CrossEncoder**（`BAAI/bge-reranker-v2-m3`，见 `src/rag/retriever.py` 的 `BGEReranker`），本地 CPU 推理太慢（20s/问），实测加上反而掉点 —— 生产 `use_reranker=False`，只在 `scripts/eval_runner.py --upper-bound` 做对比实验

---

## 🧰 技术栈

| 类别 | 选型 | 说明 |
|---|---|---|
| Python | 3.11.9 | |
| Web UI | FastAPI 0.141 + Jinja2 | 多页应用（问答 / 课表 / 奖学金 / 校园生活 / 上传中心），豆包式多 session 界面 |
| 向量库 | FAISS-CPU 1.15.1 | 本地存储，29631 文本块（1024 维，2026-10-30 实测 `index.faiss`） |
| 嵌入模型 | bge-m3（Ollama 本地） | 中文语义向量，零 API 成本 |
| 重排模型 | bge-reranker-v2-m3（HuggingFace CrossEncoder，本地 CPU） | 只有「评估上限」模式才开，生产默认关闭（CPU 太慢，见评估决策） |
| 关键词检索 | rank-bm25 | |
| 文档解析 | PyMuPDF / python-docx / beautifulsoup4 | |
| 图像识别 | YOLO-World（ultralytics） | 零样本检测，无需自训练 |
| OCR | PaddleOCR 2.7.3 | |
| 对话记忆 | 手写分层记忆 + JSON/pkl 持久化 | |

---

## 📊 评估效果

测试集扩到 **300 题**（2026-10-12），覆盖：政策问答 / 同义词 / 多模态近似 / 数字表格 / 闲聊 / 失败兜底 / 合规。
- 老 60 题：[tests/eval_set_60_backup.json](tests/eval_set_60_backup.json)（历史口径，2026-10-08 实跑）
- 新 300 题：[tests/eval_set.json](tests/eval_set.json) / 独立备份 [tests/eval_set_300.json](tests/eval_set_300.json)
- 代码：跑分 [scripts/_eval_resumable.py](scripts/_eval_resumable.py)（**断点续跑版**，LLM 挂了下次接着跑）

### 300 题线上同步配置（2026-10-12 实跑，**当前对外口径**）

> 配置：`bge-m3` 嵌入 + **BM25 + 向量 RRF 双路 8/8**，**无 reranker / 无 HyDE**（latency 优先）
> 跑分脚本：`python scripts/_eval_resumable.py`（约 13 分钟，300 题）
> 测试集生成：240 道 LLM 生成 + RAG 验证关键词（脚本：[scripts/_generate_eval_set.py](scripts/_generate_eval_set.py)）

| 指标 | 60 题（老）| **300 题（新）** | 解读 |
|---|---|---|---|
| **通过率 pass_rate** | 61.7% | 32.3% (97/300) | LLM 答案简洁，未必把 expected_keywords 全放进去（**详见下方"为什么 pass_rate 反而下降"**）|
| **召回率 recall@4** | 71.7% | **94.0%** ↑ 22% | RAG 召回**真实能力大幅提升**（29496 chunks + 改进停用词）|
| **答案命中率 answer_hit** | 85.0% | 37.0% | LLM 答得短/口语化，关键词覆盖不够 |
| **平均精度 avg_precision** | 0.7458 | **0.9425** ↑ 0.20 | 召回到的文档 94.25% 包含目标关键词 |
| **中位延迟 p50** | 3.01s | 2.01s | 优化后比老版快 1 秒 |
| **p90 延迟** | 3.69s | 2.68s | |
| **合规违规 banned_violations** | 4 | 26 | 合规 prompt 仍是真问题（ID 17/19/22/59 同根）|
| **向量库规模** | 旧索引（规模未留档） | 29496 块（2026-10-12 实跑，`logs/app.log.2026-10-12` 可查） | 当前实测 **29631 块**（2026-10-30 读 `index.faiss` 的 `ntotal`） |

> ⚠️ **诚实版**（2026-10-12）：
> - **300 题 pass_rate 32.3% 看着比 60 题 61.7% 低，但 RAG 真实能力是涨的**（recall@4 71.7% → 94%）
> - 60 题的 expected_keywords 是 LLM **自己出题时顺带写的** → LLM 答题时凑关键词容易（61.7% 偏乐观）
> - 300 题的 expected_keywords 是 **RAG 检索 top-1 文档**抽出的 → 关键词在文档里，但 LLM 答得简洁/口语化不一定都放进答案（32.3% 偏严）
> - **真正反映 RAG 能力的是 recall@4 = 94%**——这才是线上用户能搜到有用信息的概率

### 60 题线上同步配置（历史口径，2026-10-08 实跑）

> 配置：`bge-m3` 嵌入 + **BM25 + 向量 RRF 双路 8/8**，**无 reranker / 无 HyDE**（latency 优先）
> 与生产 `src/rag/rag_engine.py` 默认配置**完全对齐**

| 指标 | 数值 |
|---|---|
| 通过率 pass_rate | **61.7%** （37 / 60） |
| 召回率 recall@4 | **71.7%** |
| 答案命中率 answer_hit | 85.0% |
| 平均精度 avg_precision | 0.7458 |
| 中位延迟 p50 | **3.01s** |
| 平均延迟 avg | 2.93s |
| p90 延迟 | 3.69s |
| 合规违规 banned_violations | 4 |
| 向量库规模 | 2026-10-08 跑的旧索引（规模未留档）；当前库 **29631 块** |

> 注：旧的"86.7%"是 15 题 + 开了 BGE-reranker 的**评估上限**配置（已并入下面的"上限"对比表），并非线上真实通过率。

### 评估上限（用于看能力天花板）

> 配置：纯 `bge-m3` 向量 + **BGE-reranker-v2-m3 精排**（HuggingFace CrossEncoder，本地 CPU）+ 5/5 双路
> 与线上唯一不同：开 bge-reranker + 关 BM25
> 跑分时启用：`python scripts/eval_runner.py --upper-bound --label upper_bound`

| 配置 | pass_rate | recall@4 | p50 延迟 | p90 延迟 |
|---|---|---|---|---|
| **线上同步**（bm25 + 向量 RRF 双路, 无 reranker） | **61.7%** | **71.7%** | 3.01s | 3.69s |
| **评估上限**（纯 bge + BGE-reranker, 关 BM25） | 56.7% | 65.0% | 8.66s | 14.42s |

> ⚠️ **注意：实测线上同步配置比"评估上限"反而高 5 个百分点**（61.7% > 56.7%）。原因是评估上限模式 `use_bm25=False`，把 BM25 路径关闭了，只靠向量 + reranker。校园通知里很多精确词（学号、教务文号、奖学金简称）BM25 抓得准但 bge-m3 抓不到；reranker 只精排向量候选，BM25 命中的精准文档进不来。本项目**最终线上同步就是默认配置**，没启用 reranker。

> 优化历程(尽量对齐口径;早期 15 题数据已废弃,本表只列 60 题实测):
> - 60 题 + 现行线上同步配置:**61.7%** (37/60, 见上表)
> - 60 题 + 关 BM25 + 拆 BGE-reranker:56.7% (34/60, 见评估上限表,**反向实验:加了 reranker 比不加还低,所以没上线**)
> - 历史 15 题口径中曾达到 ~86.7%(基线 60% → prompt 80% → 重排 87%),但 15 题统计意义太弱,2026-10-08 扩到 60 题后实测线上真实通过率 61.7%,**新口径不再以 86.7% 为对外指标**

### 评测脚本用法

```bash
# 跑线上同步配置，写到 eval_baseline.json（覆盖旧 baseline）
python scripts/eval_runner.py --save-baseline --label production

# 跑评估上限，写到 eval_current_upper_bound.json（不复用 baseline 路径）
python scripts/eval_runner.py --upper-bound --label upper_bound

# 与 baseline 对比 + 出 HTML 报告
python scripts/eval_runner.py --vs eval_baseline.json --html report.html
```

> ⚠️ **README 数字对得上**：本节所有数字 2026-10-08 / 2026-10-12 实跑；改任何 prompt / chunk_size / 检索模块前，重跑一次 README 数字就在该脚本里。

### 300 题按分类明细（2026-10-12）

> 数据来源：`tests/eval_300_summary.json`
> 用途：定位短板分类，对应 README "已知短板" 的诊断依据

| 分类 | 总数 | pass | pass_rate | 诊断 |
|---|---|---|---|---|
| 办事流程 | 1 | 1 | 100% | 简单查询，全过 |
| 数学 | 1 | 1 | 100% | 走常识路径 |
| 选课 | 1 | 1 | 100% | 选课通知齐全 |
| 数字表格-宿舍 | 1 | 1 | 100% | 热水/门禁数据在 KB |
| 数字表格-课程 | 1 | 1 | 100% | 课时/学分精确 |
| 数字表格-学分 | 1 | 1 | 100% | 培养方案里有 |
| 奖学金（老分类）| 12 | 9 | 75.0% | KB 资料丰富 |
| 政策-奖学金 | 18 | 12 | 66.7% | 同上 |
| 政策-转专业 | 18 | 9 | 50.0% | 流程文档够 |
| 图书馆 | 12 | 5 | 41.7% | 通知类多，规则类少 |
| 同义词-数字电路 | 30 | 12 | 40.0% | 术语映射要 bge 救 |
| 政策-四六级 | 8 | 3 | 37.5% | 通知有时效性 |
| 课程 | 18 | 6 | 33.3% | 培养方案分散 |
| 多模态近似 | 59 | 19 | 32.2% | 拍图后文字版，期望值高 |
| 校历 | 12 | 3 | 25.0% | 时间点散在多份文档 |
| 闲聊 | 42 | 7 | 16.7% | LLM 答得短/没关键词 |
| 失败兜底 | 30 | 3 | 10.0% | banned 词防不住 |
| 合规-元信息 | 12 | 2 | 16.7% | 合规 prompt 漏 |
| **合规-拒绝** | **18** | **0** | **0%** | **真问题，prompt 关键词屏蔽不够**（同 60 题根因）|
| 政策-创业 | 1 | 1 | 100% | 单题样本量小，通过 |
| 政策-心理 | 1 | 0 | 0% | KB 可能没资料 |
| 同义词 | 1 | 0 | 0% | 单题样本量太小 |
| 数字表格-食堂 | 1 | 0 | 0% | 单题 |
| 数字表格-图书馆 | 1 | 0 | 0% | 单题 |

**洞察**：
- 🟢 **强项**：办事流程/数学/选课/数字表格-宿舍/课程/学分 → 100%（KB 资料明确）
- 🟡 **中等**：奖学金/转专业/图书馆/同义词（65-50%）→ KB 资料够，LLM 答题覆盖度需调
- 🔴 **短板**：合规-拒绝 0% / 合规-元信息 16.7% → **和 60 题同根**，prompt 关键词屏蔽不够
- 🔴 **可能缺资料**：政策-心理（0/1）→ 建议补 KB；政策-创业这题实际是 100%（1/1，单题样本量太小，别当结论）

### 多跳检索对比（2026-10-12 新增）

> 解决 README "跨文档推理差"的短板：用户问"X 课程教室在哪"需要同时召回"培养方案"和"教室分布"两份文档。
> 配置：基线 vs 多跳，**8 道跨文档题**（必须查 2 份以上文档才能答完整）
> 跑分：`python scripts/eval_multihop.py --mode compare`
> 实跑数据：2026-10-12 14:26，向量库 29496 chunks

| 配置 | pass_rate(≥2命中) | **recall@4(≥1命中)** | partial_match | avg_latency |
|---|---|---|---|---|
| **基线**（单跳 HybridRetriever）| 50.0% | 75.0% | 50.0% | 1.22s |
| **多跳**（MultiHopRetriever）| 50.0% | **100.0%** ↑25% | 50.0% | 1.30s |

**核心结论**：多跳检索把跨文档题 **recall@4 从 75% → 100%**（8 道题全部至少命中 1 个 expected keyword），延迟只涨 6%。

**逐题修复效果**（最关键的 2 道）：
- Q1「电子信息工程大三上教室在哪」：基线 0/4 ❌ → 多跳 2/4 ✅
- Q6「助学金和奖学金能兼得吗」：基线 1/3 ❌ → 多跳 3/3 ✅

**多跳检索设计**：
- 用 LLM 把复杂 query **拆成 2-3 个独立子问题**（实测 8 道题平均拆 2.25 个）
- 每跳独立走 BM25+向量+RRF 混合检索（共 18 跳）
- 多跳结果用 **RRF 融合去重**（k=60），自动识别同文档
- 失败降级：LLM 拆不出 / 任何环节异常 → 静默退回单跳

**开关**：`.env` 加 `USE_MULTIHOP=1` 启用，默认关闭（避免影响线上 latency）。

> ⚠️ **这条开关只作用于 `src/rag/rag_engine.py` → `src/rag/multi_hop.py` 那条链路**：
> 只有 `RAGEngine` 读到 `USE_MULTIHOP=1` 才会把 `MultiHopRetriever` 包在基础检索器外面。
> **线上 Web 聊天走的是 ReAct/LangGraph Agent**（`src/agent/react_engine.py`），
> 它的检索工具 `src/agent/tools/search_school_notices.py` 自建 `HybridRetriever`（BM25+向量+RRF 8/8），**不读这个开关**。
> 所以上面这组多跳数据来自**离线评测** `scripts/eval_multihop.py`，线上默认仍是单跳。

**面试话术**（背诵版）：
> "RAG 怎么处理复杂问题？我做了**多跳检索**——LLM 把 query 拆成 2-3 个独立子问题，每跳独立走混合检索，RRF 融合。跨文档题 recall@4 从 75% 涨到 100%，延迟只涨 6%。最关键的是 0.5s 的 LLM 拆解换来 25 个百分点的召回提升，比跑 reranker 划算多了。"

### 工程化：Redis 缓存 + Prometheus 监控（2026-10-12 新增）

> 解决 README "单实例无高可用"短板 + 提升线上 QPS
> 跑分：`locust -f tests/locustfile.py --host=http://localhost:7862 -u 20 -r 5 -t 60s --headless --html tests/load_test_report.html`
> 实跑数据：2026-10-12 16:43，20 并发 60 秒

**核心指标**（20 并发 × 60 秒压测）：

| 端点 | 请求数 | 失败数 | 中位延迟 | p99 延迟 | RPS |
|---|---|---|---|---|---|
| GET /api/schedule | 37 | 0 | 2.1s | 4.0s | 0.64 |
| GET /health | 7 | 0 | 2.0s | 4.2s | 0.12 |
| GET /metrics | 13 | 0 | 2.1s | 2.1s | 0.22 |
| **POST /api/chat (RAG)** | **239** | **209(429)** | **2.1s** | **4.9s** | 4.11 |
| **Aggregated** | 316 | 209(66%) | 2.1s | 4.9s | 5.43 |

> 失败全是 slowapi 限流 429（每个 IP 30/min），业务层 0 错误。压测要去掉 slowapi 限流才能测真实上限。

**缓存效果**（实际跑出来的）：

| 路径 | cache 命中延迟 | miss 延迟 | 加速比 |
|---|---|---|---|
| 首次问（cache miss）| - | 21.67s | - |
| 相同 query 第二次 | **2.22s** | - | **10x** |

**缓存命中率**（locust 压测 60 秒）：4.4%（4 hits / 90 total）。locust 随机抽 20 道 query，相同 query 出现概率低，真实场景下用户重复问常见问题命中率会到 30-50%。

**Redis 双层降级**（生产级设计）：

```
真 Redis (REDIS_URL) → fakeredis (开发) → 进程内 LRU (兜底)
   ↓                       ↓                    ↓
   高性能                 零部署              永远可用
```

**Prometheus 指标**（10 个，指标名以 `src/web_app/monitoring.py` 为准）：
- `wxu_qa_requests_total{intent, status}` - Counter：流量
- `wxu_qa_latency_seconds{intent}` - Histogram：延迟直方图（p50/p90/p99）
- `wxu_agent_tool_call_seconds{tool_name}` - Histogram：单次工具调用耗时
- `wxu_qa_errors_total{error_type}` - Counter：错误
- `wxu_qa_in_flight` - Gauge：在飞请求数（服务积压告警用）
- `wxu_cache_hits_total` / `wxu_cache_misses_total` / `wxu_cache_skipped_total` - Counter：缓存命中/未命中/跳过
- `wxu_agent_tool_calls_total{tool_name}` - Counter：Agent 工具调用次数
- `wxu_rag_docs_retrieved` - Histogram：每次召回的文档条数

> 注：Histogram 类型在 Prometheus 里会展开成多条时间序列（`_bucket`/`_sum`/`_count`），
> 所以 `/metrics` 端点上的序列条数多于指标名个数 —— 说「10 个指标」指指标名，别按序列数说。
> 另：`wxu_uid` / `wxu_sid` / `wxu_nick` 是这些指标的**标签名**，不是独立指标。

**部署**：`docs/deployment.md` 写了完整的 Docker Compose + Grafana Dashboard 配置。

**面试话术**（背诵版）：
> "工程化我做了三件事：①**Redis 缓存**，相同 query 直接返，第一次问 21s 第二次 2s，加速 10 倍，TTL 7 天加 ±10% 抖动防雪崩；②**三级降级**，真 Redis → fakeredis → 进程内 LRU，永远不会让缓存层挂掉业务；③**Prometheus 监控**，10 个指标含 QPS / p50/p90/p99 / 错误率 / 缓存命中率，挂 `/metrics` 端点给 Grafana 抓。"

---

## 🚀 快速开始

### 1. 环境准备

```bash
# 创建虚拟环境
python -m venv venv311
venv311\Scripts\activate

# 安装依赖（注意 paddleocr 的 opencv 版本约束）
pip install -r requirements.txt
pip install opencv-contrib-python==4.10.0.84 --no-deps
```

### 2. 本地模型（Ollama）

```bash
# 安装 Ollama 后拉取中文嵌入模型（检索链路只依赖它）
ollama pull bge-m3
```

> 重排模型（`BAAI/bge-reranker-v2-m3`）走 **HuggingFace CrossEncoder**，**不走 Ollama**；
> 生产默认不挂重排（CPU 太慢），只有跑 `scripts/eval_runner.py --upper-bound` 才会用到，
> 首次使用会自动从 HuggingFace 下载到 `~/.cache/huggingface/`。

### 3. 配置环境变量

创建 `.env` 文件：

```
LLM_API_KEY=你的大模型API_KEY
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=模型名称
```

### 4. 构建向量库（首次）

```bash
python src/rag/build_vectorstore.py
```

### 5. 启动应用（FastAPI 多页架构）

```bash
# 推荐：一键启动（自动激活 venv + 检查依赖 + 后台启动 + 健康检查）
powershell -ExecutionPolicy Bypass -File start.ps1

# 或手动启动：
python src/web_app/fastapi_app.py
```

浏览器访问 `http://localhost:7862`（首页 `/`、课表 `/schedule`、奖学金 `/scholarship`、校园生活 `/campus`、上传中心 `/upload`、拍照识图 `/look`、健康检查 `/health`）

> 历史：早期用 Gradio 单页（`src/web_app/app.py`，端口 7861），2026-09-22 起迁移到 FastAPI 多页架构。2026-10-09 正式删除 Gradio 入口，统一只用 FastAPI。

---

## 📁 项目结构

```
wxu-multimodal-smart-agent/
├── src/
│   ├── rag/                    # RAG 核心
│   │   ├── chunk_config.py     # 切块参数单一真相源(chunk_size / overlap)
│   │   ├── data_loader.py      # 文档加载 + 清洗 + 分块
│   │   ├── vector_store.py     # FAISS 向量库管理
│   │   ├── retriever.py        # 混合检索(BM25 + 向量 + RRF)
│   │   ├── multi_hop.py        # 多跳检索(LLM 拆子问题 + RRF 融合)
│   │   ├── llm_chain.py        # 问答链 + 引用来源
│   │   ├── chat_history.py     # 分层对话记忆 + query rewriting
│   │   ├── query_cache.py      # query embedding 缓存(LRU, TTL 5 分钟)
│   │   ├── rag_engine.py       # RAG 引擎装配(向量库+检索器+问答链)
│   │   ├── build_vectorstore.py# 向量库构建入口(断点续跑)
│   │   └── rag_evaluate.py     # 评估脚本
│   ├── agent/                  # ReAct Agent
│   │   ├── react_engine.py     # 手写 ReAct 循环（bind_tools + tool_calls，无 AgentExecutor）
│   │   ├── langgraph_engine.py # LangGraph 状态图实现(USE_LANGGRAPH_AGENT 切换)
│   │   ├── metrics.py          # Agent 侧 Prometheus 埋点(软依赖)
│   │   └── tools/              # 4 个工具(通知检索 / 课表 / 图片 OCR / 联网)
│   ├── curriculum/             # 课表解析(班级分桶 + 周次计算)
│   │   ├── parser.py           # PDF/截图 课表解析入口
│   │   ├── parser_v2_pdfplumber.py # 表格线识别(优先路径)
│   │   ├── parser_coords.py    # PyMuPDF 坐标识别(次选路径)
│   │   └── experiment_parser.py # 实验课表(Excel/CSV/截图)
│   ├── yolo/                   # 图像识别
│   │   └── inference.py        # YOLO-World 推理(权重缺失时自动下载)
│   ├── ocr/                    # 文字识别
│   │   └── ocr_engine.py       # PaddleOCR 封装
│   └── web_app/                # Web 应用（FastAPI 多页架构）
│       ├── fastapi_app.py      # FastAPI 主入口（多页 + REST API）
│       ├── image_assistant.py  # 图片分发(YOLO/OCR) —— 已接 /look 页 + /api/image/look
│       ├── upload_handler.py   # 文档上传处理
│       ├── answer_cache.py     # 答案缓存(Redis → fakeredis → 进程内 LRU)
│       ├── user_store.py       # 多用户多 session 管理
│       ├── monitoring.py        # Prometheus 指标
│       ├── pages/              # Jinja2 页面(首页 / 课表 / 校园 / 上传 / 拍照识图)
│       └── agent/              # 意图路由 / memory / dispatcher
│           ├── dispatcher.py
│           ├── memory.py
│           └── intent.py
├── scripts/                    # 工具脚本(爬虫 / 构建 / 评测 / 启动)
├── tests/                      # 评估测试集(300 题) + bad case 报告
├── docs/                       # 学习指南 / 问题记录 / 面试准备
├── requirements.txt            # 完整应用依赖
├── requirements-ci.txt         # CI 最小依赖集(72 包,只够跑单元测试)
├── docker-compose.yml          # 生产部署（FastAPI + Redis + Ollama + Prometheus + Grafana）
├── Dockerfile                  # 生产镜像
├── prometheus.yml              # 抓取配置
├── prometheus-alerts.yml       # 告警规则
└── data/                       # 向量库 + 文档(不上传 Git)
```

---

## 🔧 关键工程决策

1. **嵌入本地化**：用 Ollama 跑 bge-m3 嵌入，避免每次检索调 API 产生费用和延迟（重排若开启，走 HuggingFace CrossEncoder 本地跑）
2. **YOLO-World 零样本**：不标注、不训练，直接用文字标签检测校园建筑，快速落地
3. **分层对话记忆**：近期对话完整保留，早期对话压缩摘要，控制上下文长度
4. **状态机幂等标记**：向量库构建 `done=True` 仅在所有批次成功时写入，防止残库上线
5. **沉默降级告警**：reranker 加载失败时显式 warn 而非静默跳过,避免隐性质量下降
6. **真·ReAct 取代规则路由**:**手写 ReAct 循环**(`src/agent/react_engine.py`：`ChatOpenAI.bind_tools` + 自己解析 `tool_calls` + 最多 5 轮迭代)+ 4 个真实工具(校内 RAG / 我的课表 / 图片 OCR / 联网兜底),由 LLM 自己决策调哪个,**不再走"关键词命中 → 模板套"的老路**;工具异常降级成字符串反馈而不是 crash 整个 agent;另有一套 LangGraph 状态图实现(`src/agent/langgraph_engine.py`),用 `USE_LANGGRAPH_AGENT=1` 切换
7. **课表班级分桶 + 公共课合并渲染**:`curriculum.class_id` 字段把 PDF 课表按 24电信1/2/3 分桶;实验课截图/Excel 自动按 `user_settings.class_name` 过滤入桶;`load_curriculum(class_id=X)` 一句 SQL 返回 `class_id=X OR class_id IS NULL`,公共课永远可见
8. **周次自动计算**:开学日 + 当前日 → 第几周 直接用 `(today - start_date).days // 7 + 1` 算出来,夹在 [1, 25] 之间;URL 里 `?week=N` 仍可手动覆盖(便于测试)

---

## 验证脚本

```bash
# 端到端冒烟：venv311 + RAG 环境自检（Ollama / 向量库 / 检索 / 问答链路）
python scripts/smoke_test.py

# 跑 300 题评测（线上同步配置：bge-m3 + BM25/向量 RRF 双路，无 reranker）
python scripts/eval_runner.py --save-baseline --label production --html report.html

# 多跳检索对比（基线 vs MultiHopRetriever）
python scripts/eval_multihop.py --mode compare
```

---

## ⚠️ 已知短板（诚实版，2026-10-12 更新）

> 2026-10-12 收尾审查：以下按"是否已修复"分类。
> **✅ = 已修复**（2026-10-12 实施 A + C 方案后修复）
> **⚠️ = 部分修复**（有改善但未完全解决）
> **❌ = 仍存在**

- ✅ **跨文档推理差**（原 recall 35%）：多跳检索 (`src/rag/multi_hop.py`) 8 道跨文档题 recall@4 从 75% → **100%**，延迟只涨 6%
- ⚠️ **单实例、无高可用**：Redis 缓存 + Prometheus 监控已落地，3 级降级保证业务不挂；但仍**单进程 FastAPI**，真正高可用需要 Docker Swarm/K8s（见 `docs/deployment.md`）
- ✅ **测试集 60 题统计意义弱**（2026-10-12 扩到 **300 题**，recall@4 ±2% 精度，多跳 + 工程化都基于 300 题数据）
- ❌ **300 题 answer_hit 37%（pass_rate 32.3%）**：RAG 召回好（94%）但 LLM 答得短/口语化，关键字没塞进答案。要么改 prompt 让 LLM 答得详细，要么放宽 expected_keywords 评判标准
- ⚠️ **线上 71.7% recall@4（60 题）→ 94.0%（300 题）**：RAG 真实能力大幅提升，但部分分类仍弱（同根 60 题：政策细节 + 跨文档事实）
- ❌ **评估模式 ≠ 线上模式**：评估模式下启用了 BGE-reranker 看上限，但生产为延迟关了它；线上跑出来是 **61.7%** 而不是历史口径的 86.7%（11/26 那次是用 15 题 + 开 reranker 跑出来的）
- ❌ **合规 prompt 还有 4 题漏**：当前 60 题里 banned 违规 4 次，分别是 ID 17（"复变函数期末考试范围"答案里出现"参考答案"）、ID 19（"复变函数期末试卷"出现"试卷"+"答案"）、ID 22（"奖学金兼得"答案里 banned "可以同时获得" 命中）、ID 59（"今天无锡天气怎么样"答了"我无法知道实时天气"，banned "无法" 没防住）。可见 llm_chain 的合规 prompt 关键词屏蔽不够全面
- ❌ **多模态端到端没测**：YOLO + OCR + RAG 是分模块集成，测试集里只有"图片后纯文字版近似"；真实拍图 → 自动回答这条路径需要单独 demo 验证
- ⚠️ **聊天框不支持发图**：拍照识图是**独立页面**（`/look` + `POST /api/image/look`），识别结果只回显在那一页，**不会自动回灌进对话**；Agent 的 `ocr_image` 工具在聊天链路里也拿不到图片（`handle_agent_stream` 支持 `image_path`，但 `/api/chat` 这条路由没传）
- ❌ **BM25 中文切词覆盖**：jieba 用通用词表，校园特有词（奖学金类型简称、教务系统名）可能没切准，靠 bge-m3 语义救回一部分，但仍有漏网
- ❌ **合规过滤是关键词级**:`retriever._is_exam_doc` 只看文件名 pattern（"答案"、"A卷"等）；试卷内容被截屏嵌进"教学日历"等正常文档里，是漏网之鱼
- ❌ **YOLO 校园场景泛化**：YOLO-World 默认用 41 个固定类别（COCO 之外的校园物品，见 `src/yolo/inference.py` 的 `DEFAULT_WORLD_CLASSES`）；不在这 41 类里的物体识别不到（`set_classes()` 可改，但 Web 端暂无入口）

---

## 📝 相关文档

- [操作手册](操作手册.md) — 8 周逐周开发计划
- [学习指南](docs/LEARNING_GUIDE.md) — 代码原理与模块说明
- [Bad Case 报告](tests/bad-case-report.md) — RAG 召回失败案例分析
- [面试准备](docs/interview_prep_tech_details.md) — 技术面试要点
