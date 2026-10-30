# WXU 项目能力清单

> 写在前面：本项目涉及的能力按"必掌握→加分项"分4层。建议按顺序学习。
>
> ⚠️ **数字以 [README.md](../README.md) 为准（2026-10-09 修订）**：本文早期写"12334 chunks / answer_relevancy 0.89 / LLaVA 联动 / Function Calling 4 个工具"等说法**都与当前代码不符**。实际：29447 文本块、无 LLaVA、无 Function Calling、无 Ragas 指标（项目用自写 eval_runner 跑 15 题基线，参考 README 评估上限表）。
> 下文保留旧表述作为开发历程参考，但**面试时不要照搬**——按 README 实测数据说。

---

## 🔴 第一层：必掌握（不会这些无法通过面试）

### 1. RAG 全流程（⭐⭐⭐⭐⭐）

| 知识点 | 在本项目对应文件 | 必须会的内容 |
|--------|----------------|-------------|
| 文档加载 | `src/rag/data_loader.py` | PyMuPDFLoader / Docx2txtLoader / TextLoader 区别、PDF乱码处理 |
| 文本分块 | `src/rag/data_loader.py::TextChunker` | chunk_size=800 / overlap=150 的取舍、中文分块策略 |
| Embedding | `src/rag/vector_store.py` | 文本→向量的原理、768 维含义、余弦相似度 |
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
| OllamaEmbeddings | Embedding 模型 | `vector_store.py` |
| ConversationalRetrievalChain | 多轮对话 RAG | `chat_history.py` |
| ConversationBufferMemory | 会话记忆 | `chat_history.py` |
| PromptTemplate | Prompt 模板 | `chat_history.py`、`llm_chain.py` |

### 3. Ollama 本地推理（⭐⭐⭐⭐）

```powershell
ollama list           # 查看已下载模型
ollama pull xxx       # 下载模型
ollama serve          # 启动服务（默认127.0.0.1:11434）
```

接口：`POST /api/embeddings`、`POST /api/embed`、`POST /api/generate`

### 4. 多轮对话（⭐⭐⭐⭐）

- `chat_history.py` 的 `SimpleChatManager` 和 `ChatHistoryManager` 实现
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

**面试故事**：构建 29447 文本块需要 2 小时，中途进程被杀 0 损失，靠什么？

### 7. 向量库性能调优（⭐⭐⭐⭐）

- batch_size 调参（50 vs 100 vs 200）
- FAISS 索引类型选择（Flat / IVF / HNSW）
- embedding 模型选型（nomic-embed-text vs mxbai-embed-large）

### 8. 评估（⭐⭐⭐⭐）

- Ragas 指标：faithfulness / answer_relevancy / context_recall / context_precision
- Bad Case 收集 + 迭代记录

---

## 🟢 第三层：可写可不写（看时间）

### 9. 多模态 YOLOv8（⭐⭐⭐）
- `src/yolo/train.py`、`inference.py`
- 数据集标注、训练、推理

### 10. SQLite 用户管理（⭐⭐⭐）
- `src/db/sqlite_db.py`（目前为空，待实现）
- 多用户隔离、对话历史持久化

### 11. 爬虫 + 定时任务（⭐⭐⭐）
- `src/crawler/notice_spider.py`、`scheduler.py`
- BeautifulSoup 解析、APScheduler 定时

### 12. 邮件推送（⭐⭐）
- `src/email_push/mail_sender.py`
- SMTP、关键词订阅

### 13. Gradio UI（⭐⭐⭐）
- `src/web_app/app.py`
- Blocks / Tabs / Chatbot 组件

---

## 🔵 第四层：面试软实力（必须练）

### 14. 1 分钟项目介绍

模板：
> "我做的是无锡学院多模态智能体，核心是 RAG + Agent 双引擎。RAG 引擎覆盖 29447 个文档块、12 类校园知识。多模态用 YOLOv8 + PaddleOCR 联动，拍照即查。Agent 用意图分类 + 路由表调度（schedule / scholarship / kb / news / general），期间解决了 3 个核心问题：PDF 乱码清洗、多轮对话指代丢失、RAG 召回不准。"

> 注：早期版本曾写"12334 chunks / answer_relevancy 0.89 / LLaVA / Function Calling 4 个工具"，**这些都不准确**（实际见 README 评估上限表）。

### 15. Bad Case 分析报告

模板：`tests/bad-case-report.md`
```markdown
## Bad Case #01: 多轮指代丢失
**现象**: 第一轮问"图书馆开放时间"，第二轮问"里面能充电吗"，LLM 答非所问
**根因**: chat_history.py 字符串拼接，没传 chat_history 给 retriever
**优化**: 改为 ConversationalRetrievalChain
**结果**: 准确率 40% → 85%
```

### 16. 技术决策 ADR

模板：`docs/adr/0001-why-faiss.md`

### 17. 演示视频

1-2 分钟录屏：多轮问答→拍照识图→学分计算→文档上传→通知订阅

---

## 📋 学习顺序建议（8 周）

| 周次 | 内容 | 产出 |
|------|------|------|
| W1 | RAG 全流程 + 向量库构建 | 能跑通 V1 |
| W2 | 多轮对话 + Gradio UI | V1 多轮版本 |
| W3 | YOLO 训练 + 多模态联动 | V2 完成 |
| W4 | SQLite + 用户系统 | V3 部分 |
| W5 | 爬虫 + 邮件 + 定时 | V4 完成 |
| W6 | 断点续跑 + 异常处理 | 工程化标签 |
| W7 | Ragas 评估 + Bad Case | V5 评估 |
| W8 | 文档 + 演示视频 + 简历 | 求职就绪 |

---

## 🎯 每个 .py 文件顶部都有引用注释

打开 `src/rag/__init__.py` 可见完整能力地图。

每个核心模块顶部都有 "## 重点掌握 ##" 注释块，列出该文件涉及的具体能力点。