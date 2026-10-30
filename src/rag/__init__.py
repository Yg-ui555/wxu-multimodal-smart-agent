# ============================================================
# 📚 WXU 项目 RAG 核心模块 - 能力地图
# ============================================================
#
# 🎯 这个项目需要你重点掌握的能力（按重要性排序）：
#
# ┌─────────────────────────────────────────────────────────┐
# │ 🔴 第一层：必掌握（不会无法通过面试）                  │
# ├─────────────────────────────────────────────────────────┤
# │ 1. RAG 全流程                                         │
# │    ├─ 文档加载（PyMuPDFLoader / Docx2txtLoader）       │
# │    ├─ 文本分块（chunk_size=800 / overlap=150）         │
# │    ├─ Embedding（Ollama bge-m3 → 1024 维，中文向量）    │
# │    ├─ 向量检索（FAISS similarity_search / top_k）       │
# │    ├─ Prompt 工程（引用溯源模板）                      │
# │    └─ LLM 调用（OpenAI 兼容 API，默认 DeepSeek）        │
# │                                                         │
# │ 2. LangChain 框架（用到的是哪几个要说准）             │
# │    ├─ Document / TextLoader / PyMuPDFLoader            │
# │    ├─ RecursiveCharacterTextSplitter                  │
# │    ├─ FAISS（langchain_community）/ OllamaEmbeddings   │
# │    ├─ PromptTemplate + LCEL（prompt | llm）            │
# │    └─ ❌ 没用 ConversationalRetrievalChain / LLMChain   │
# │       （多轮是手写分层记忆，见 chat_history.py）        │
# │                                                         │
# │ 3. 多轮对话（指代理解、上下文压缩）                     │
# │    └─ src/rag/chat_history.py + src/web_app/agent/memory.py │
# │ 4. Ollama 本地推理（pull / serve / API 调用）           │
# │ 5. 异常处理 + logging 日志体系                          │
# └─────────────────────────────────────────────────────────┘
#
# ┌─────────────────────────────────────────────────────────┐
# │ 🟡 第二层：加分项（写在简历上能拉分）                  │
# ├─────────────────────────────────────────────────────────┤
# │ 6. 断点续跑（progress.json + 每批保存 + stdout 心跳）   │
# │    → 三万块 embedding 中途被杀不丢任何进度             │
# │ 7. FAISS 增量保存（add_documents + save_local）         │
# │ 8. 混合检索（BM25 + 向量 + RRF 融合）                  │
# │    └─ 重排器是 HuggingFace CrossEncoder（BAAI/bge-reranker-v2-m3），
# │       本地 CPU 太慢 → 生产 use_reranker=False，只做消融实验 │
# │ 9. 自研评估（recall@k / 引用命中率 / 幻觉率）           │
# │10. Bad Case 分析报告（面试杀手锏）                      │
# │11. Function Calling（Agent 自主调度工具）               │
# │12. LangGraph 状态机（src/agent/langgraph_engine.py）    │
# │13. 多跳检索（src/rag/multi_hop.py，线上默认关）          │
# └─────────────────────────────────────────────────────────┘
#
# ┌─────────────────────────────────────────────────────────┐
# │ 🟢 第三层：可写可不写（看时间）                        │
# ├─────────────────────────────────────────────────────────┤
# │14. 多模态 YOLO-World（零样本检测 + 联动 RAG）          │
# │15. SQLite 用户管理（多用户隔离、对话持久化）            │
# │16. 爬虫 + 定时任务（Windows 任务计划，每天 06:00）      │
# │17. FastAPI Web（多页 + Jinja2 原生前端，已弃 Gradio）   │
# │18. Docker 部署                                          │
# └─────────────────────────────────────────────────────────┘
#
# ┌─────────────────────────────────────────────────────────┐
# │ 🔵 第四层：面试软实力（必须练）                        │
# ├─────────────────────────────────────────────────────────┤
# │19. 1 分钟项目介绍（项目背景 + 技术亮点 + 量化结果）     │
# │20. Bad Case 报告（5-10 个真实案例 + 优化思路）          │
# │21. 技术决策 ADR（为什么选 FAISS / DeepSeek / LangChain）│
# │22. 演示视频（1-2 分钟录屏）                             │
# └─────────────────────────────────────────────────────────┘
#
# ============================================================
# 📁 模块文件 → 对应能力对照表
# （2026-10-30 逐个对过 src/rag/ 目录实际文件，一一对应）
# ============================================================
#
# chunk_config.py    → 切块参数单一真相源（800 / 150）（能力 1）
# data_loader.py     → 文档加载 + 标题感知分块 + 去重（能力 1）
# vector_store.py    → Embedding(bge-m3) + FAISS + 断点续跑（能力 1, 6, 7）
# retriever.py       → BM25+向量混合检索 + RRF + 可选 reranker（能力 1, 8）
# llm_chain.py       → Prompt 工程 + LLM 调用（能力 1, 2）
# chat_history.py    → 分层记忆 + 意图路由 + query rewriting（能力 2, 3）
# query_cache.py     → query embedding 缓存（LRU / TTL 5 分钟）（能力 1）
# multi_hop.py       → 多跳检索（LLM 拆子问题 + RRF 融合）（能力 13）
# rag_engine.py      → RAG 引擎装配：向量库 + 检索器 + 问答链（能力 1, 13）
# answer_cache.py    → ⚠️ 不在本目录，答案缓存实际在 src/web_app/answer_cache.py
# build_vectorstore.py → 进度显示 + 异常处理 + 日志（能力 5, 6）
# rag_evaluate.py    → 自研 recall@k/引用命中率/幻觉率（能力 9）
#
# ============================================================
# 🎤 面试高频问题（每个都要会答）
# ============================================================
#
# Q1: "你这个项目的 RAG 全流程是什么？"
# A1: 文档 → PyMuPDFLoader 加载 → 标题感知分块(800/150)
#     → Ollama bge-m3 转 1024 维向量 → FAISS 存向量
#     → 用户提问 → 意图路由(知识库/常识/闲聊)
#     → BM25+向量混合检索 → RRF 融合 → top-4
#     → 拼 Prompt + 检索片段 → DeepSeek 生成答案
#     （线上没有精排这一步：reranker 用 HF CrossEncoder，CPU 太慢已关闭）
#
# Q2: "为什么用 FAISS 而不是 Chroma / Pinecone？"
# A2: FAISS 完全本地、零成本、单机能跑 10w+ chunks、LangChain
#     集成成熟。Chroma 也不错但性能弱一些，Pinecone 要付费。
#     （决策记录见 docs/RERANKER_DECISION.md；docs/adr/ 目录当前不存在）
#
# Q3: "Embedding 模型选型标准？"
# A3: 维度、中文能力、模型大小。nomic-embed-text 是英文向导模型，
#     中文同义词召回差（"门电路↔组合逻辑电路"召回失败）。
#     改用 bge-m3：1024 维、中文语义强、多语言支持，Ollama 本地跑。
#     旧库 768 维不兼容，全量重建；当前库 29631 块（2026-10-30 实测 index.faiss）。
#
# Q4: "如何避免 LLM 幻觉？"
# A4: 三层防护：
#     ① prompt 强制"仅基于参考资料回答，没找到就说不知道"
#     ② 自研幻觉率指标量化（回答关键词与检索文档重叠率）
#     ③ Bad Case 收集 → 针对性优化
#
# Q5: "你这个项目最难解决的问题是什么？"
# A5: (挑一个你有把握的真实案例，例如多轮对话指代丢失、PDF 乱码、
#      RAG 召回不准等)——讲清楚问题、根因、优化、量化结果
#
# Q6: "如果数据量从 1 万涨到 100 万 chunk 怎么办？"
# A6: FAISS 换 IVF/HNSW 索引（O(log n) 替代 O(n)）；
#     加 reranker（代码里的后端是 HuggingFace CrossEncoder：
#     BAAI/bge-reranker-v2-m3；有 GPU 时可上 GPU 提速）；
#     分布式（多机 FAISS / Milvus）。
#
# Q7: "为什么不用微调？"
# A7: RAG + Agent 已经能解决 90% 校园问题，成本低、可解释、易更新。
#     微调成本高、需要大量 GPU、领域数据有限时不划算。
#     （"我们选择 RAG 方案而不是微调"的决策理由）
#
# ============================================================
# 📖 完整能力地图与学习顺序：docs/LEARNING_GUIDE.md
# ============================================================

# 当前 __init__.py 为空，所有 RAG 模块通过显式 import 加载
# 详见上方注释和 docs/LEARNING_GUIDE.md
