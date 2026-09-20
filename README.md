# 无锡学院多模态校园智能助手

> 基于 RAG + YOLO-World + PaddleOCR 的校园问答助手。支持自然语言问答、拍照识图、图片文字提取，所有嵌入与重排模型本地运行，仅答案生成调用大模型 API。

---

## ✨ 功能特性

| 模块 | 能力 |
|---|---|
| **智能问答** | 基于校内文档知识库（教务处通知、课程讲义、奖学金政策等）的自然语言问答，附原文引用 |
| **拍照识图** | 上传校园建筑照片，YOLO-World 零样本识别后自动查询相关资料 |
| **图片识字** | 截图/拍照中的文字通过 PaddleOCR 提取，转文本后送入检索 |
| **多轮对话** | 分层记忆（近期完整 + 历史摘要），支持上下文指代 |
| **多用户多会话** | 浏览器 UUID 隔离用户，豆包式左侧会话列表，每会话独立持久化 |
| **文档上传** | 支持 PDF / Word / TXT / Markdown 五种格式，自动去重并入向量库 |
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

基于 15 题人工标注测试集（校历 / 奖学金 / 课程 / 同义词 / 常识）：

| 指标 | 数值 |
|---|---|
| 通过率 pass_rate | **86.7%** |
| 召回率 recall@4 | **86.7%** |
| 答案命中率 answer_hit | 100% |
| 向量库规模 | 29376 chunks |

> 优化历程：基线 60% → prompt 优化 80% → 启用 bge-reranker 87%

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

### 5. 启动应用

```bash
python src/web_app/app.py
```

浏览器访问 `http://localhost:7861`

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
│   └── web_app/                # Web 应用
│       ├── app.py              # Gradio 主界面
│       ├── image_assistant.py  # 图片分发(YOLO/OCR)
│       ├── upload_handler.py   # 文档上传处理
│       └── user_store.py       # 多用户多 session 管理
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
5. **沉默降级告警**：reranker 加载失败时显式 warn 而非静默跳过，避免隐性质量下降

---

## ⚠️ 局限性

- **回答延迟较高**：启用重排器后中位延迟约 19 秒，待优化（减少重排数量 / 流式输出）
- **知识库覆盖有限**：仅教务处通知和部分课程讲义，奖学金/四六级等政策文档缺失
- **无自训练模型**：YOLO 用零样本方案，复杂场景识别精度有限
- **单节点部署**：未做分布式和高可用

---

## 📝 相关文档

- [操作手册](操作手册.md) — 8 周逐周开发计划
- [学习指南](docs/LEARNING_GUIDE.md) — 代码原理与模块说明
- [Bad Case 报告](tests/bad_case_report.md) — RAG 召回失败案例分析
- [面试准备](docs/interview_prep_tech_details.md) — 技术面试要点
