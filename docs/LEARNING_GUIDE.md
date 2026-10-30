# WXU 项目能力清单

> 写在前面：本项目涉及的能力按"必掌握→加分项"分4层。建议按顺序学习。
>
> ⚠️ **数字以 [README.md](../README.md) 为准（2026-10-30 修订）**：本文早期写"12334 chunks / answer_relevancy 0.89 / LLaVA 联动 / Function Calling 4 个工具"等说法**都与当前代码不符**。实际：**29631 文本块 / 1024 维**（2026-10-30 读 `data/faiss_db_bge/index.faiss` 的 `ntotal`）、无 LLaVA、无 Function Calling、无 Ragas 指标（项目用自写 eval_runner 跑 300 题基线，参考 README 评估表）。
> 下文保留旧表述作为开发历程参考，但**面试时不要照搬**——按 README 实测数据说。
>
> ⚠️ **本文里提到的文件路径已逐条核对**（2026-10-30）：`src/db/sqlite_db.py`、`src/crawler/notice_spider.py`、`src/crawler/scheduler.py`、`src/email_push/mail_sender.py`、`src/yolo/train.py`、`src/web_app/app.py`、`docs/adr/0001-why-faiss.md` **在当前仓库里都不存在**，下面已改成实际存在的路径或标注为"未实现"。

---

## 🔴 第一层：必掌握（不会这些无法通过面试）

### 1. RAG 全流程（⭐⭐⭐⭐⭐）

| 知识点 | 在本项目对应文件 | 必须会的内容 |
|--------|----------------|-------------|
| 文档加载 | `src/rag/data_loader.py` | PyMuPDFLoader / Docx2txtLoader / TextLoader 区别、PDF乱码处理 |
| 文本分块 | `src/rag/data_loader.py::TextChunker` | chunk_size=800 / overlap=150 的取舍、中文分块策略 |
| Embedding | `src/rag/vector_store.py` | 文本→向量的原理、1024 维的含义、余弦相似度（嵌入走 Ollama 的 bge-m3） |
| 向量检索 | `src/rag/retriever.py` | FAISS similarity_search、top_k 调参 |
| Prompt 工程 | `src/rag/llm_chain.py` | System Prompt / User Prompt、引用溯源模板 |
| LLM 生成 | `src/rag/llm_chain.py` | DeepSeek API 调用、流式 vs 非流式 |

**面试高频问题**：
- "为什么 chunk_size=800 而不是 200 或 2000？"
- "Embedding 模型选型标准？"
- "如何避免 LLM 幻觉？"
- "召回的 top_k 怎么定？"

### 2. LangChain 框架（⭐⭐⭐⭐⭐）

| 组件 | 用法 | 对应文件 |
|------|------|---------|
| Document | 统一文档抽象 | `data_loader.py` |
| TextLoader / PyMuPDFLoader / Docx2txtLoader | 多种格式加载 | `data_loader.py` |
| RecursiveCharacterTextSplitter | 文本分块 | `data_loader.py` |
| FAISS（from langchain_community） | 向量库 | `vector_store.py` |
| OllamaEmbeddings | Embedding 模型（bge-m3，1024 维） | `vector_store.py` |
| PromptTemplate | Prompt 模板 | `chat_history.py`、`llm_chain.py` |
| **手写分层记忆 + query rewriting**（**没有用** `ConversationalRetrievalChain` / `ConversationBufferMemory`） | 多轮对话 RAG：自己写的 `ChatHistoryManager`（近期完整 + 历史摘要 + 指代改写） | `chat_history.py` |
| `HybridRetriever`（BM25 + 向量 + RRF 融合） | 检索链路本体（项目自写，不是 LangChain 的 chain 类） | `retriever.py` |
| LCEL（`prompt \| llm`） | 问答链调用方式 | `llm_chain.py`、`multi_hop.py` |

### 3. Ollama 本地推理（⭐⭐⭐⭐）

```powershell
ollama list           # 查看已下载模型
ollama pull xxx       # 下载模型
ollama serve          # 启动服务（默认127.0.0.1:11434）
```

接口：`POST /api/embeddings`、`POST /api/embed`、`POST /api/generate`

### 4. 多轮对话（⭐⭐⭐⭐）

- `chat_history.py` 的 `ChatHistoryManager`（分层记忆 + 意图路由 + 手写 query rewriting）和 `SimpleChatManager`（精简版）
- Web 端的用户级记忆另存在 `src/web_app/agent/memory.py`（SQLite：最近消息 + 用户偏好，如姓名）
- 历史记录拼接策略、token 限制、上下文压缩

### 5. 异常处理 + 日志（⭐⭐⭐⭐）

- `logging` 模块的 `FileHandler` + `StreamHandler` 配置
- try-except 在每个外部调用处（PDF 解析、API 调用、embedding）

---

## 🟡 第二层：加分项（写在简历上能拉分）

### 6. 断点续跑 + 容错（⭐⭐⭐⭐⭐）

| 文件 | 关键能力 |
|------|---------|
| `vector_store.py::_read_progress` | 检测上次进度，决定是否续跑 |
| `vector_store.py::_write_progress` | 每批写 progress.json |
| `vector_store.py::create_vectorstore` | FAISS 增量保存（不重建） |
| `build_vectorstore.py` | stdout 心跳防监控误判 |

**面试故事**：构建 29631 文本块需要 2 小时，中途进程被杀 0 损失，靠什么？

### 7. 向量库性能调优（⭐⭐⭐⭐）

- batch_size 调参（50 vs 100 vs 200）
- FAISS 索引类型选择（Flat / IVF / HNSW）
- embedding 模型选型（nomic-embed-text vs mxbai-embed-large）

### 8. 评估（⭐⭐⭐⭐）

- 自研评估（主线）：`scripts/eval_runner.py` / `scripts/_eval_resumable.py` 跑 300 题，指标 recall@4 / avg_precision / pass_rate / answer_hit / banned_violations
- Ragas 指标（**可选、未作为对外口径**）：`scripts/eval_ragas.py` 可选装 `ragas`，跑 faithfulness / answer_relevancy 等
- Bad Case 收集 + 迭代记录：`tests/bad-case-report.md`

---

## 🟢 第三层：可写可不写（看时间）

### 9. 多模态 YOLO（⭐⭐⭐）
- `src/yolo/inference.py`（YOLOv8 80 类 + YOLO-World 开放词汇零样本，**没有训练脚本**）
- 零样本推理：`set_classes()` 换类别表；默认 41 个校园物品

### 10. SQLite 用户管理（⭐⭐⭐）
- `src/web_app/user_store.py`（浏览器 UUID → 用户/session 状态持久化）
- `src/curriculum/db.py`（课表按班级分桶存储）+ `src/web_app/agent/memory.py`（用户消息 / 偏好）
- 多用户隔离、对话历史持久化

### 11. 爬虫 + 定时任务（⭐⭐⭐）
- `scripts/daily_crawl.py`（20 个监控面 / 6 个子站域名，BeautifulSoup + requests 清洗入库）
- `scripts/setup_scheduled_task.ps1`（Windows 任务计划，每天 06:00，任务名 `WXU_DailyCrawl_0600`）
- 幂等靠 `crawl_state.json` 状态文件（记录每个面最新条目）

### 12. 邮件推送（⭐⭐）
- **未实现**（仓库里没有邮件模块，别在简历/面试里提）

### 13. Web UI（⭐⭐⭐）
- `src/web_app/fastapi_app.py`（FastAPI 多页：问答 / 课表 / 奖学金 / 校园生活 / 上传中心 / 拍照识图 `/look`）+ `src/web_app/pages/*.html`（Jinja2 原生前端）
- **Gradio 已于 2026-09-22 迁出、2026-10-09 彻底删除**（`src/web_app/app.py` 不存在了）

---

## 🔵 第四层：面试软实力（必须练）

### 14. 1 分钟项目介绍

模板：
> "我做的是无锡学院多模态智能体，核心是 RAG + Agent 双引擎。RAG 引擎覆盖 29631 个文档块、覆盖教务/图书馆/学工等 7 类板块校园知识。多模态用 YOLOv8 + PaddleOCR，拍照识图是独立页 /look（认物 + 识字），课表截图走 OCR 解析。Agent 用手写 ReAct 循环 + 4 个工具（校内检索 / 我的课表 / 图片 OCR / 联网兜底），由 LLM 自己决定调哪个，期间解决了 3 个核心问题：PDF 乱码清洗、多轮对话指代丢失、RAG 召回不准。"

> 注：早期版本曾写"12334 chunks / answer_relevancy 0.89 / LLaVA / Function Calling 4 个工具"，**这些都不准确**（实际见 README 评估上限表）。

### 15. Bad Case 分析报告

模板：`tests/bad-case-report.md`
```markdown
## Bad Case #01: 多轮指代丢失
**现象**: 第一轮问"图书馆开放时间"，第二轮问"里面能充电吗"，LLM 答非所问
**根因**: chat_history.py 字符串拼接，没传 chat_history 给 retriever
**优化**: 改成**手写 query rewriting**（没上 LangChain 的 ConversationalRetrievalChain：它要求 BaseRetriever，和自定义 HybridRetriever 冲突）
**结果**: 准确率 40% → 85%
```

### 16. 技术决策 ADR

真实存在的决策记录：`docs/RERANKER_DECISION.md`（重排器启用/撤销）
（`docs/adr/` 目录当前**不存在**，别引用不存在的路径）

### 17. 演示视频

1-2 分钟录屏：多轮问答→拍照识图（`/look` 独立页）→课表截图 OCR→学分计算→文档上传→通知订阅
（**聊天框不支持发图**，拍照识图要单独演示 `/look` 页面）

---

## 📋 学习顺序建议（8 周）

| 周次 | 内容 | 产出 |
|------|------|------|
| W1 | RAG 全流程 + 向量库构建 | 能跑通 V1 |
| W2 | 多轮对话 + FastAPI Web UI | V1 多轮版本 |
| W3 | YOLO 零样本 + 多模态联动 | V2 完成 |
| W4 | SQLite + 用户系统 | V3 部分 |
| W5 | 爬虫 + 定时任务 | V4 完成 |
| W6 | 断点续跑 + 异常处理 | 工程化标签 |
| W7 | 300 题评估 + Bad Case | V5 评估 |
| W8 | 文档 + 演示视频 + 简历 | 求职就绪 |

---

## 🎯 每个 .py 文件顶部都有引用注释

打开 `src/rag/__init__.py` 可见完整能力地图。

每个核心模块顶部都有 "## 重点掌握 ##" 注释块，列出该文件涉及的具体能力点。