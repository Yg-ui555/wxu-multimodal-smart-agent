# 无锡学院多模态校园智能助手

## WXU Multimodal Smart Agent

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/LangChain-RAG-green?style=flat-square" alt="LangChain">
  <img src="https://img.shields.io/badge/YOLOv8-computer vision-orange?style=flat-square" alt="YOLOv8">
  <img src="https://img.shields.io/badge/Gradio-web UI-purple?style=flat-square&logo=gradio">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square">
</p>

<p align="center">
  <a href="#-项目简介">简介</a> •
  <a href="#-核心功能">功能</a> •
  <a href="#-技术亮点">亮点</a> •
  <a href="#-技术栈">技术栈</a> •
  <a href="#-系统架构">架构</a> •
  <a href="#-项目结构">结构</a> •
  <a href="#-部署教程">部署</a> •
  <a href="#-项目创新点">创新</a> •
  <a href="#-未来拓展">拓展</a>
</p>

---

## 📌 项目简介

**无锡学院多模态校园智能助手** 是一款专为无锡学院全校学生打造的全方位校园智能问答系统。基于 RAG 检索增强生成 + 计算机视觉多模态理解技术，学生几乎所有校园问题都可以通过自然语言获得精准解答。

### 🎯 核心定位

作为校园场景下的垂直领域智能助手，本系统整合了校园生活、学业管理、政策咨询、活动通知等全维度信息源，通过向量检索与 LLM 生成能力，为学生提供 **7×24 小时、全天候、无间断** 的智能问答服务。

### 📋 功能简介

这是一个面向无锡学院在校生的全能校园智能问答系统，学生几乎所有校园问题都可以问我：

- 校园社团介绍、校园跑规则
- 各专业课程表、上课时间
- 宿舍问题、食堂攻略
- 图书馆开放时间
- 奖学金、转专业、学分毕业要求
- 竞赛通知、校园活动
- 校内通知查询、选课建议、毕业规划

---

## ✨ 核心功能

| 模块 | 功能描述 |
|------|----------|
| **🧠 智能问答** | 基于 RAG 私有知识库，支持 PDF/Word/TXT 文档上传、动态入库，覆盖校园生活、学业政策、通知公告等全领域 |
| **📷 建筑识别** | YOLOv8 多模态图像识别，拍照即可识别校园建筑，自动联动知识库介绍详细信息 |
| **💬 多轮对话** | ConversationBufferMemory 上下文记忆，理解代词指代，支持连续追问 |
| **📊 学分管理** | 自动计算已修学分与毕业要求差距，生成个性化选课建议与毕业规划报告 |
| **🕷️ 定时爬虫** | 每 12 小时自动抓取教务处、电信学院官网最新通知，实时更新知识库 |
| **📧 邮件订阅** | 关键词订阅 + SMTP 邮件推送，竞赛/奖学金等通知即时送达 |
| **📈 智能评估** | Ragas 评估框架，Answer Relevancy / Context Recall / Faithfulness 指标监控 |
| **🎨 Web 界面** | Gradio 多标签可视化界面，问答、识图、学分、订阅一体化操作 |

---

## 🔧 技术亮点

- **RAG 检索增强**：私有知识库 + 向量相似度检索 + LLM 生成，确保答案基于真实文档，拒绝幻觉
- **多模态融合**：文本问答 + 图像识别双通道，统一接入 LLM 理解层
- **动态知识更新**：用户上传文档自动分块、向量化、入库，支持增量更新
- **上下文感知**：多轮对话记忆机制，理解"它""这个"等指代关系
- **工程化完整**：异常捕获、日志记录、Bad Case 收集、持续迭代优化
- **零成本部署**：本地 Python 环境 + 免费 LLM API，无需服务器即可运行

---

## 🛠️ 技术栈

基于 **Python + LangChain RAG + Chroma 向量知识库 + YOLOv8 多模态图像识别 + SQLite 用户系统 + 定时爬虫 + 邮件订阅推送 + Gradio 前端开发** 构建。

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend                                 │
│                    Gradio Web UI                                │
├─────────────────────────────────────────────────────────────────┤
│                       Backend                                   │
│  LangChain RAG  •  LLM Chain  •  Conversation Memory          │
├─────────────────────────────────────────────────────────────────┤
│                      Vector Store                               │
│              FAISS / Chroma (可切换)                            │
├─────────────────────────────────────────────────────────────────┤
│                       Embedding                                 │
│            Ollama (nomic-embed-text)                           │
├─────────────────────────────────────────────────────────────────┤
│                       LLM Layer                                │
│              DeepSeek / OpenAI API (可切换)                     │
├─────────────────────────────────────────────────────────────────┤
│                    CV & OCR                                     │
│          YOLOv8  •  PyMuPDF  •  BeautifulSoup                │
├─────────────────────────────────────────────────────────────────┤
│                      Storage                                    │
│           SQLite  •  File System  •  Crawler                   │
└─────────────────────────────────────────────────────────────────┘
```

| 层级 | 技术选型 |
|------|----------|
| 前端 | Gradio |
| RAG 框架 | LangChain / LangChain-Community |
| 向量库 | FAISS / Chroma (可选) |
| 嵌入模型 | Ollama (nomic-embed-text) |
| LLM | DeepSeek / OpenAI (可切换) |
| 图像识别 | Ultralytics YOLOv8 |
| PDF 解析 | PyMuPDF |
| 爬虫 | Requests + BeautifulSoup |
| 数据库 | SQLite |
| 邮件 | SMTPLib |

---

## 🏗️ 系统架构

```
                              ┌─────────────────┐
                              │   Gradio Web   │
                              │     Client     │
                              └────────┬────────┘
                                       │
                                       ▼
                              ┌─────────────────┐
                              │   User Input    │
                              │  (Text / Image) │
                              └────────┬────────┘
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            │                          │                          │
            ▼                          ▼                          ▼
   ┌────────────────┐      ┌─────────────────┐       ┌────────────────┐
   │   Text Query   │      │  Image Input    │       │  Subscription  │
   │   ──────────   │      │   ──────────   │       │   ──────────   │
   │  RAG Pipeline  │      │  YOLOv8 Infer  │       │  Crawler +     │
   │  Retriever +   │      │  Building      │       │  Email Push    │
   │  LLM Generator │      │  Recognition   │       │  Scheduler     │
   └────────┬───────┘      └────────┬────────┘       └────────┬───────┘
            │                       │                          │
            └───────────────────────┼──────────────────────────┘
                                    ▼
                         ┌─────────────────────┐
                         │   Unified LLM       │
                         │   Understanding      │
                         └──────────┬──────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         │                          │                          │
         ▼                          ▼                          ▼
  ┌─────────────┐          ┌─────────────┐          ┌─────────────┐
  │   FAISS     │          │   SQLite    │          │   Crawler   │
  │  Vector DB  │          │  User Data  │          │  Web Data   │
  └─────────────┘          └─────────────┘          └─────────────┘
```

---

## 📂 项目结构

```
wxu-multimodal-smart-agent/
├── src/
│   ├── rag/
│   │   ├── data_loader.py       # PDF/TXT 加载 + 文本分块
│   │   ├── vector_store.py      # FAISS 向量库管理
│   │   ├── retriever.py         # 向量检索器
│   │   ├── llm_chain.py         # LLM 问答链
│   │   └── build_vectorstore.py # 向量库构建脚本
│   ├── yolo/
│   │   ├── train.py             # YOLOv8 训练脚本
│   │   ├── inference.py         # 推理脚本
│   │   └── weights/             # 模型权重
│   ├── crawler/
│   │   ├── notice_spider.py    # 通知爬虫
│   │   └── scheduler.py         # 定时任务
│   ├── db/
│   │   └── sqlite_db.py        # SQLite 用户管理
│   ├── credit_system/
│   │   ├── credit_calc.py      # 学分计算
│   │   └── major_rule.json     # 培养方案规则
│   ├── email_push/
│   │   └── mail_sender.py      # 邮件推送
│   └── web_app/
│       └── app.py               # Gradio 主界面
├── data/
│   ├── docs/                   # 原始文档
│   │   ├── academic/           # 学业相关
│   │   │   ├── curriculum/     # 培养方案/课程表
│   │   │   ├── training/       # 实习/实践
│   │   │   ├── scholarship/    # 奖学金
│   │   │   ├── transfer/       # 转专业
│   │   │   └── calendar/       # 校历
│   │   ├── life/               # 生活相关
│   │   │   ├── library/        # 图书馆
│   │   │   ├── canteen/        # 食堂
│   │   │   ├── dorm/           # 宿舍
│   │   │   └── campus/         # 校园环境
│   │   └── 其他/               # 竞赛/通知/政策
│   ├── faiss_db/              # 向量库 (已忽略)
│   └── sqlite/                # SQLite 数据库 (已忽略)
├── tests/
│   ├── test_cases.py          # 测试用例
│   └── bad_case_report.md    # Bad Case 分析
├── requirements.txt
├── .env                       # API 密钥 (已忽略)
└── README.md
```

---

## 🚀 部署教程

### 环境要求

- Python 3.10+
- Git
- Ollama (用于本地嵌入模型)

### 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/Yg-ui555/wxu-multimodal-smart-agent.git
cd wxu-multimodal-smart-agent

# 2. 创建虚拟环境
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / MacOS
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 下载嵌入模型
ollama pull nomic-embed-text

# 5. 配置环境变量
# 编辑 .env 文件，配置 LLM API Key
LLM_API_KEY="your-api-key"
LLM_BASE_URL="https://api.deepseek.com/v1"
LLM_MODEL="deepseek-chat"

# 6. 放入知识文档
# 将 PDF/TXT 文档放入 data/docs/ 目录

# 7. 构建向量库
python src/rag/build_vectorstore.py

# 8. 启动应用
python src/web_app/app.py
```

访问 `http://localhost:7861` 即可使用。

---

## 💡 项目创新点

### 1. 垂直领域 RAG 落地

针对校园场景深度优化知识库构建流程，支持 PDF/Word 多格式解析，文本分块策略针对课程表、通知文件等非结构化文档专项调优。

### 2. 多模态交互范式

文本问答 + 图像识别双通道设计，用户拍照即可获取建筑信息，突破传统纯文本交互局限，实现"所见即所问"。

### 3. 完整学生服务闭环

不仅是问答系统，更整合了学分管理、通知订阅、邮件推送等实用功能，真正成为学生日常校园生活的智能助手。

### 4. 工程化完整度

从数据采集（爬虫）→ 知识处理（向量化）→ 应用层（LLM）→ 用户交互（Gradio）→ 持续优化（Ragas 评估），全链路闭环，可直接作为生产级项目展示。

---

## 🎯 项目优势

| 维度 | 优势 |
|------|------|
| **技术深度** | RAG 全链路 + 多模态 + 向量检索 + LLM 微调思路 |
| **工程化** | 异常处理、日志记录、Bad Case 收集、CI/CD 可扩展 |
| **实用性** | 真实校园场景，问题解决导向，可直接上线使用 |
| **可展示性** | 功能丰富、界面美观、代码结构清晰、文档完整 |
| **可扩展性** | 模块化设计，支持更换 LLM、向量库、添加新功能 |

---

## 🔮 未来拓展

- [ ] **微信小程序集成**：对接微信生态，扫码即用
- [ ] **企业微信机器人**：校内通知自动推送
- [ ] **Agent 规划能力**：LangChain Agent 实现复杂任务编排
- [ ] **多模型切换**：支持 GPT-4、Claude、文心一言等主流 LLM
- [ ] **知识图谱增强**：Neo4j 图数据库，实体关系可视化
- [ ] **移动端适配**：PWA / React Native 移动应用
- [ ] **语音交互**：Whisper 语音识别 + TTS 语音播报

---

## 📋 适用场景

| 场景 | 说明 |
|------|------|
| **校园问答系统** | 入学指南、课程咨询、奖学金政策、社团活动等 |
| **智能客服** | 教务处、学生处、各学院综合服务窗口 |
| **知识管理** | 校内文档、通知、规章制度智能化检索 |
| **个人助理** | 学生日常学习、生活、规划智能管家 |
| **面试作品** | AI / RAG / LLM 方向求职项目展示 |

---

## 📄 License

MIT License

---

## 🤝 欢迎贡献

Issue • Pull Request • Star

---

<p align="center">
  <sub>Built with 🧠 RAG + 👁️ YOLOv8 + 💬 LLM</sub>
</p>
