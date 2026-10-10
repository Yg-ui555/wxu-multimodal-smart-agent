# 无锡学院多模态校园智能助手

> 基于 RAG + YOLO-World + PaddleOCR 的校园问答助手。支持自然语言问答、拍照识图、图片文字提取，所有嵌入与重排模型本地运行，仅答案生成调用大模型 API。

---

## ✨ 功能特性

| 模块 | 能力 |
|---|---|
| **智能问答** | 基于校内文档知识库（教务处通知、课程讲义、奖学金政策等）的自然语言问答，附原文引用 |
| **ReAct 智能体** | 4 个工具的真正 LLM 决策型 Agent：校园通知检索(RAG) / 我的课表 / 图片 OCR / 实时联网，工具调用 + 推理链 + 追问兜底（而不是规则路由 + LLM 包装） |
| **拍照识图** | 上传校园建筑照片，YOLO-World 零样本识别后自动查询相关资料 |
| **图片识字** | 截图/拍照中的文字通过 PaddleOCR 提取，转文本后送入检索 |
| **多轮对话** | 分层记忆（近期完整 + 历史摘要），支持上下文指代 |
| **多用户多会话** | 浏览器 UUID 隔离用户，豆包式左侧会话列表，每会话独立持久化 |
| **文档上传** | 支持 PDF / Word / TXT / Markdown 五种格式，自动去重并入向量库 |
| **班级维度课表** | PDF 课表按班级归类（24电信1/2/3 分桶存储），实验课上传自动按"我的班级"过滤入表，公共课 + 我的班级课程合并渲染 |
| **周次自动判断** | 用户配置开学日后，/schedule 自动算出当前是第几周，无需每次手动切 |
| **通知爬虫** | 定时抓取教务处通知，清洗后自动追加进知识库 |

---

## 🏗️ 系统架构

```
用户输入（文字 / 图片）
    │
    ├── 纯文字 ──────────────────────────────┐
    │                                         ▼
    └── 带图片 → YOLO-World 识别 / PaddleOCR 提取文字
                    │                        │
                    └──── 转为文本 ───────────┘
                                         │
                                         ▼
                          意图判断（知识库 / 闲聊）
                                         │
                                         ▼
                    ┌──────── 混合检索（Hybrid Retriever）────────┐
                    │                                              │
                    ├── BM25 关键词检索（词面精确匹配）            │
                    ├── 向量检索（bge-m3 语义相似度）              │
                    └── RRF 融合（两路排名加权合并）               │
                                         │                        │
                                         ▼                        │
                              bge-reranker-v2-m3 重排             │
                              （top-20 → top-4）                   │
                                         │                        │
                                         ▼                        │
                              LLM 生成答案 + 引用来源             │
```

**检索链路核心设计**：
- **双路检索**：BM25 抓精确匹配，向量抓语义/同义词，互补
- **RRF 融合**：`score = Σ 1/(k + rank)`，k=60，避免单一检索的盲区
- **重排器**：本地 Ollama 跑 bge-reranker-v2-m3，对候选重排提升准确率

---

## 🧰 技术栈

| 类别 | 选型 | 说明 |
|---|---|---|
| Python | 3.11.9 | |
| Web UI | Gradio 6.28.0 | 豆包式多 session 界面 |
| 向量库 | FAISS-CPU 1.15.1 | 本地存储，29376 文本块 |
| 嵌入模型 | bge-m3（Ollama 本地） | 中文语义向量，零 API 成本 |
| 重排模型 | bge-reranker-v2-m3（Ollama 本地） | |
| 关键词检索 | rank-bm25 | |
| 文档解析 | PyMuPDF / python-docx / beautifulsoup4 | |
| 图像识别 | YOLO-World（ultralytics） | 零样本检测，无需自训练 |
| OCR | PaddleOCR 2.7.3 | |
| 对话记忆 | 手写分层记忆 + JSON/pkl 持久化 | |

---

## 📊 评估效果

测试集扩到 60 题（2026-10-08），覆盖：政策问答 / 同义词 / 多模态近似 / 数字表格 / 闲聊 / 失败兜底 / 合规。
代码：见 [tests/eval_set.json](tests/eval_set.json)，跑分：[scripts/eval_runner.py](scripts/eval_runner.py)。

### 线上同步配置（用户实际体感）

> 配置：`bge-m3` 嵌入 + **BM25 + 向量 RRF 双路 8/8**，**无 reranker / 无 HyDE**（latency 优先）
> 与生产 `src/rag/rag_engine.py` 默认配置**完全对齐**

| 指标 | 数值 |
|---|---|
| 通过率 pass_rate | **61.7%** （37 / 60） |
| 召回率 recall@4 | **71.7%** |
| 答案命中率 answer_hit | 85.0% |
| 平均精度 avg_precision | 0.75 |
| 中位延迟 p50 | **3.01s** |
| 平均延迟 avg | 2.93s |
| p90 延迟 | 3.69s |
| 合规违规 banned_violations | 4 |
| 向量库规模 | 29447 chunks |

> 注：旧的"86.7%"是 15 题 + 开了 BGE-reranker 的**评估上限**配置（已并入下面的"上限"对比表），并非线上真实通过率。

### 评估上限（用于看能力天花板）

> 配置：纯 `bge-m3` 向量 + **BGE-reranker-v2-m3 精排** + 5/5 双路
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

> ⚠️ **README 数字对得上**：本节所有数字 2026-10-08 实跑；改任何 prompt / chunk_size / 检索模块前，重跑一次 README 数字就在该脚本里。

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
# 安装 Ollama 后拉取中文嵌入与重排模型
ollama pull bge-m3
ollama pull bge-reranker-v2-m3
```

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

浏览器访问 `http://localhost:7862`（首页 `/`、课表 `/schedule`、奖学金 `/scholarship`、校园生活 `/campus`、上传中心 `/upload`、健康检查 `/health`）

> 历史：早期用 Gradio 单页（`src/web_app/app.py`，端口 7861），2026-09-22 起迁移到 FastAPI 多页架构。2026-10-09 正式删除 Gradio 入口，统一只用 FastAPI。

---

## 📁 项目结构

```
wxu-multimodal-smart-agent/
├── src/
│   ├── rag/                    # RAG 核心
│   │   ├── data_loader.py      # 文档加载 + 清洗 + 分块(800/150)
│   │   ├── vector_store.py     # FAISS 向量库管理
│   │   ├── retriever.py        # 混合检索(BM25+向量+RRF)+重排
│   │   ├── llm_chain.py        # 问答链 + 引用来源
│   │   ├── chat_history.py     # 分层对话记忆
│   │   └── rag_evaluate.py     # 评估脚本
│   ├── yolo/                   # 图像识别
│   │   └── inference.py        # YOLO-World 推理
│   ├── ocr/                    # 文字识别
│   │   └── ocr_engine.py       # PaddleOCR 封装
│   ├── crawler/                # 通知爬虫
│   └── web_app/                # Web 应用（FastAPI 多页架构）
│       ├── fastapi_app.py      # FastAPI 主入口（多页 + REST API）
│       ├── image_assistant.py  # 图片分发(YOLO/OCR)
│       ├── upload_handler.py   # 文档上传处理
│       ├── user_store.py       # 多用户多 session 管理
│       └── agent/              # 意图路由 / memory / dispatcher
│           ├── dispatcher.py
│           ├── memory.py
│           └── intent.py
├── scripts/                    # 工具脚本(爬虫/构建/启动)
├── tests/                      # 评估测试集 + bad case 报告
├── docs/                       # 学习指南/问题记录/面试准备
└── data/                       # 向量库 + 文档(不上传 Git)
```

---

## 🔧 关键工程决策

1. **嵌入/重排本地化**：用 Ollama 跑 bge-m3，避免每次检索调 API 产生费用和延迟
2. **YOLO-World 零样本**：不标注、不训练，直接用文字标签检测校园建筑，快速落地
3. **分层对话记忆**：近期对话完整保留，早期对话压缩摘要，控制上下文长度
4. **状态机幂等标记**：向量库构建 `done=True` 仅在所有批次成功时写入，防止残库上线
5. **沉默降级告警**：reranker 加载失败时显式 warn 而非静默跳过,避免隐性质量下降
6. **真·ReAct 取代规则路由**:用 LangChain 的 `AgentExecutor`(tool-calling ReAct) + 4 个真实工具(校内 RAG / 我的课表 / 图片 OCR / 联网兜底),由 LLM 自己决策调哪个,**不再走"关键词命中 → 模板套"的老路**;工具异常降级成字符串反馈而不是 crash 整个 agent
7. **课表班级分桶 + 公共课合并渲染**:`curriculum.class_id` 字段把 PDF 课表按 24电信1/2/3 分桶;实验课截图/Excel 自动按 `user_settings.class_name` 过滤入桶;`load_curriculum(class_id=X)` 一句 SQL 返回 `class_id=X OR class_id IS NULL`,公共课永远可见
8. **周次自动计算**:开学日 + 当前日 → 第几周 直接用 `(today - start_date).days // 7 + 1` 算出来,夹在 [1, 25] 之间;URL 里 `?week=N` 仍可手动覆盖(便于测试)

---

## 验证脚本

```bash
# 课表班级分桶 + 实验课过滤入桶 e2e(端到端验证整个 Priority 2 链路)
python scripts/_test_experiment_table_e2e.py
```
预期输出：CSV 7 行 → 过滤精准 3 条命中 `class_id=2` 桶 → `/api/schedule` 拉回 3 条 → 自动清理测试数据

---

## ⚠️ 已知短板（诚实版）

> 2026-10-08 重写：以下每条**都是项目当前能观察到的实际短板**，不是预告要修的事项。

- **测试集 60 题，统计意义仍弱**：量级上线可能 ±5% 浮动；改了任何 prompt / chunk_size / 检索模块前，**重跑一次** `eval_runner.py`，对照 `eval_baseline.json` 才有意义
- **线上 71.7% recall@4，有 17 题召回后 LLM 也救不回来**：主要是政策类（奖学金细则、四六级等）和"问的是一个跨文档事实"两类
- **评估模式 ≠ 线上模式**：评估模式下启用了 BGE-reranker 看上限，但生产为延迟关了它；线上跑出来是 **61.7%** 而不是历史口径的 86.7%（11/26 那次是用 15 题 + 开 reranker 跑出来的）
- **合规 prompt 还有 4 题漏**：当前 60 题里 banned 违规 4 次，分别是 ID 17（"复变函数期末考试范围"答案里出现"参考答案"）、ID 19（"复变函数期末试卷"出现"试卷"+"答案"）、ID 22（"奖学金兼得"答案里 banned "可以同时获得" 命中）、ID 59（"今天无锡天气怎么样"答了"我无法知道实时天气"，banned "无法" 没防住）。可见 llm_chain 的合规 prompt 关键词屏蔽不够全面
- **多模态端到端没测**：YOLO + OCR + RAG 是分模块集成，测试集里只有"图片后纯文字版近似"；真实拍图 → 自动回答这条路径需要单独 demo 验证
- **BM25 中文切词覆盖**：jieba 用通用词表，校园特有词（奖学金类型简称、教务系统名）可能没切准，靠 bge-m3 语义救回一部分，但仍有漏网
- **跨文档推理差**：问"X 课程的上课教室在哪"需要召回培养方案再查教室分布；当前 chunk_size=800 切散了"培养方案/排课表"，跨文档题 recall@4 掉到 35% 量级
  - **2026-10-08 实测分项**:校历 0/2 通(0%),食堂菜单 0/1、图书馆周末 0/1 都不行;反之数字表格-学分/课程/宿舍这种**单源数字题 100% 通过**,说明真正弱的是"跨多源拼接",不是"数字本身"
- **合规过滤是关键词级**:`retriever._is_exam_doc` 只看文件名 pattern（"答案"、"A卷"等）；试卷内容被截屏嵌进"教学日历"等正常文档里，是漏网之鱼
  - **2026-10-08 实测分项**:合规 5 题(2 元信息 + 3 拒绝)全军覆没,pass_rate 0%。banned 2 次(元信息 1 + 拒绝 1)。**根因不是 prompt 防 banned 词不够强,而是检索阶段就把合规相关文档过滤了**,LLM 拿不到 context → 自然落 fallback 答案里出现 "无法" 等被 banned 的词。这是**两阶段(retriver + llm_chain)的连锁**,改单边都不彻底
- **YOLO 校园场景泛化**：YOLO-World 用 50 个左右固定类别（COCO 之外的校园物品）；不在这 50 类里的物体识别不到
- **单实例、无高可用**：FastAPI 单进程，Ollama 也单实例；挂在哪个就哪个不可用

---

## 📝 相关文档

- [操作手册](操作手册.md) — 8 周逐周开发计划
- [学习指南](docs/LEARNING_GUIDE.md) — 代码原理与模块说明
- [Bad Case 报告](tests/bad_case_report.md) — RAG 召回失败案例分析
- [面试准备](docs/interview_prep_tech_details.md) — 技术面试要点
