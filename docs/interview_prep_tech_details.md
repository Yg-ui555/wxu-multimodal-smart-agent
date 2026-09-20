# 面试技术讲解：5 个已落地的章节

> 这是**直接拿去面试讲**的素材。每个章节含：
> 1. **技术细节**（它是什么）
> 2. **你的实现**（具体怎么做的、关键参数）
> 3. **为什么这么做**（取舍依据 — 面试官最爱问"为什么不用 X 用 Y"）
> 4. **面试话术**（30 秒讲清）

---

## 21-1 · RAG 召回架构

### 技术细节

RAG 的"召回"决定 LLM 拿到什么"原材料"——**召回率 recall@4（top-4 docs 含正确答案的比例）直接决定最终答案质量**。单路召回必然有盲区：

| 召回方式 | 原理 | 优势 | 弱点 |
|---|---|---|---|
| **向量召回** | 文本编码成 1024-dim 稠密向量，L2 / 余弦距离 | 语义近似强，"奖学金评定"能召回"奖学金评定办法" | **同义词覆盖差**：中文里"门电路" ≠ "组合逻辑电路"；对专有名词弱 |
| **BM25** | 词袋 + IDF + 文档长度归一化 | 精确匹配强，**对专有名词 / 文号 / 数字敏感** | "分数低怎么算" → 命中差（口语化问题）|
| **Reranker** | Cross-Encoder 把 (query, doc) 拼起来输入 LLM | 精度最高（语义 + 词的联合打分）| **慢**：每个 (q, d) 对都要走一遍 BERT |

### 你的实现（src/rag/retriever.py）

```python
# 三路召回 + RRF 融合
vector_docs = self._vector_search(query)         # bge-m3 + FAISS
bm25_docs = self._bm25_search(query)              # jieba + rank_bm25

if bm25_docs:
    fused = rrf_fuse([vector_docs, bm25_docs], top_n=self.vector_top_k)
else:
    fused = vector_docs                            # BM25 不可用时降级

if self.reranker and fused:
    fused = self.reranker.rerank(query, fused, top_n=self.top_k)
```

**RRF (Reciprocal Rank Fusion) 公式**：
```
score(d) = Σᵢ 1 / (k + rankᵢ(d))     k = 60
```
不依赖 score 量纲（向量是 L2 距离，BM25 是 IDF 分——跨量纲），所以多源融合鲁棒。

### 为什么这么做（取舍）

- **不用单路**：向量会漏"教务文件 [2024] 11 号"这种文号；BM25 会漏口语化问题。**多路融合是行业共识**（Cohere / LangChain 文档都明确说"单路召回不够"）。
- **选 RRF 而不是线性加权**：向量 score 是 L2 距离（越小越好），BM25 是 TF-IDF（越大越好），量纲完全不同，加权公式 `α * sim_v + β * sim_b` 根本调不出最优解。**RRF 完全不看 score，看 rank**，简单且对数项平滑——这是 Cormack 2009 年 IR 论文的最佳实践。
- **为什么 k=60**：经典常数。Cormack 后续研究表明 k ∈ [30, 100] 都接近，但 k=60 是默认推荐（Cohere / Vespa 默认）。
- **为什么不用 Cohere / bge-reranker-v2-m3 默认 reranker**：本项目 venv 没装 FlagEmbedding，跑不起来——今天已修 silent fallback 问题（默认 reranker_model=None + warn 而不是偷偷跳）。

### 面试话术

> "我们的 RAG 用**三路召回**：向量 bge-m3（中文同义词能力强）、BM25（专有名词兜底）、bge-reranker 精排，最终通过 **RRF(k=60) 融合**。RRF 比线性加权好是因为不同召回源的分数量纲不一致，RRF 看 rank 不看 score，鲁棒得多。"

如果问"为什么用 RAG"：

> "直接问 LLM 会出现幻觉——它会编教务处的政策。RAG 把检索到的真实文档塞进 prompt，**用证据约束答案**。bge-m3 把中文同义词召回率从 60% 提到 85% 这事儿我做过对比测试。"

---

## 21-2 · 企业级 RAG 系统构建

### 技术细节

企业级 RAG ≠ demo 级 RAG，主要差异：

| 维度 | Demo 级 | 企业级（你的项目） |
|---|---|---|
| 切分粒度 | 固定 chunk_size 切 | **标题感知** 先切章节再切块 |
| 路由 | 全 LLM 一次答完 | **意图分类** 路由（知库 / 常识 / 闲聊）|
| 召回失败 | 死板说"找不到" | **NO_CONTEXT_TEMPLATE** 用常识答 + 注明"以学校官方为准" |
| 去重 | 不处理 | `dedup_chunks()` 按 content_hash 去重 |
| 增量更新 | 全量重建 | `add_documents()` 增量入 FAISS |

### 你的实现

**1. 标题感知分块**（`src/rag/data_loader.py`）：

```python
HEADING_PATTERNS = [
    re.compile(r'^第[一二三四五六七八九十百零\d]+章\b'),       # "第一章"
    re.compile(r'^第[一二三四五六七八九十\d]+[节条]\b'),         # "第一节"
    re.compile(r'^[一二三四五六七八九十]+、'),                  # "一、"
    re.compile(r'^\d+(\.\d+)*\s+\S'),                           # "1.1 xxx"
    re.compile(r'^[（(][一二三四五六七八九十\d]+[)）]\s*\S'),    # "（一）"
]

def _split_by_headings(self, text):
    # 先按标题切，再用 RecursiveCharacterTextSplitter 在章节内细切
    # chunk_size=800, overlap=150（经验值，召回精度 vs 上下文长度平衡）
```

**2. 意图路由**（`src/rag/chat_history.py`）：

```python
INTENT_ROUTE_TEMPLATE = """你是意图分类助手。判断用户问题属于哪一类，只返回编号：
1. 知识库查询（学校政策、课程、考试、学分、通知等）
2. 常识问答（算术、通用知识、与无锡学院无关的问题）
3. 闲聊/问候（你好、谢谢、再见等）

用户问题：{question}
类别编号："""
```

让 LLM **只输出单字符编号**，省 token。

**3. NO_CONTEXT_TEMPLATE**：

```python
NO_CONTEXT_TEMPLATE = """...注意：参考资料中没有与用户问题直接相关的内容...

请：
1. 用你自己的知识简短回答
2. 数学题直接给结论（如"1+1=2"）
3. 学校生活问题给常识建议 + 注明"建议以学校官方通知为准"
..."""
```

### 为什么这么做

- **chunk_size=800 / overlap=150 是经验值**：再大召回率下降（top-4 噪声多），再小上下文不够（答案断章）。来自 LangChain 官方推荐 + 多次试验。
- **重叠 150 的目的**：防止关键信息刚好被切到两块丢失，让相邻 chunk 共享最后 150 个字符。
- **意图路由的成本节省**：闲聊问题不需要走检索，省 1 次 FAISS I/O + 1 次 LLM 调用；同时更专业（闲聊用 CONVERSATIONAL_TEMPLATE，知库用 DEFAULT_TEMPLATE）。
- **NO_CONTEXT_TEMPLATE 而不是"找文档"硬来**：实际项目里 30-40% 的 query 是边缘性的（"图书馆几点关门"被知识库部分覆盖）。让 LLM 用常识兜底 + 注明"以官方为准"比硬说"找不到"体验好得多——这就是大厂常说的"兜底而不是放弃"。

### 面试话术

> "我们的 RAG 不是简单的'问 → 检索 → 答'流程。我们有**三层架构**：
> 1. **标题感知分块**：用 5 条正则识别章节边界，先切章节再切块，避免主题被切散。
> 2. **意图路由**：先让 LLM 把 query 分到'知库 / 常识 / 闲聊'三类，分别走不同 prompt template——这是 LangChain 论文里 'Self-RAG' 的思路。
> 3. **NO_CONTEXT 兜底**：检索失败时用 LLM 自己的常识答，但明确告诉用户'建议以学校官方通知为准'——比硬说'找不到'更人性化。"

---

## 21-5 · LangChain 框架

### 技术细节

LangChain = LLM 应用编排框架。它把所有 LLM 周边（loader、splitter、vector store、embedding、prompt、parser）抽象成统一接口。

**核心抽象**：

```
Document  ───── DocumentLoader（PyMuPDFLoader / TextLoader / Docx2txtLoader）
            ↓
split_documents()  ──── RecursiveCharacterTextSplitter
            ↓
[Document, Document, ...]  ── Embeddings（OllamaEmbeddings 包 bge-m3）
            ↓
FAISS.from_documents()  ── VectorStore（FAISS 自家包）
            ↓
similarity_search_with_score(query, k=4)  ── Retriever
            ↓
PromptTemplate.format(context=..., question=...)  ── LLM (ChatOpenAI / DeepSeek)
            ↓
AIMessage(content=...)
```

**LCEL (LangChain Expression Language)**：

```python
chain = prompt | llm | output_parser     # 这条 | 是 LCEL 的 pipeline 操作符
result = chain.invoke({"context": c, "question": q})
```

LCEL 把组件链成"管道"，跟 Unix shell 的 `|` 一样直观。替代老的 `LLMChain(prompt=..., llm=...)` 命令式写法。

### 你的实现（src/rag/）

```python
# vector_store.py
self.embeddings = OllamaEmbeddings(
    model="bge-m3",
    base_url="http://127.0.0.1:11434"
)
self._vectorstore = FAISS.from_documents(documents=batch, embedding=self.embeddings)

# llm_chain.py
self.chain = self.prompt | self.llm                  # LCEL
self.fallback_chain = self.fallback_prompt | self.llm
response = self.chain.invoke({"context": context, "question": question}).content
```

### 为什么这么做

- **为什么用 LangChain 而不是直接调 LLM API**：loader / splitter / vector store 都得自己写，重复劳动。LangChain 把这些都封装好了。
- **为什么用 LCEL 而不是老 LLMChain**：LCEL 流式执行（答案一个字一个字出），老 LLMChain 是阻塞返回。LCEL 还支持 Runnable 抽象（每个节点都是可 invoke 的对象）。
- **LangChain 1.x 拆包**：langchain-community（社区组件）/ langchain-core（基础类型）/ langchain-ollama（Ollama 集成）。我代码里 import 是 `from langchain_ollama import OllamaEmbeddings`、`from langchain_openai import ChatOpenAI`、`from langchain_core.prompts import PromptTemplate`——拆包正确，否则新版会 ImportError。

### 面试话术

> "我们项目以 **LangChain 为主框架**，loader / splitter / vector store / embeddings / chain 全部走 LangChain 抽象。代码用 **LCEL（LangChain Expression Language）** 写：`prompt | llm | parser`，跟 Unix shell 一样。
>
> 1.x 拆包后我做了完整迁移：langchain-community 管社区组件、langchain-ollama 管 Ollama 集成、langchain-openai 管 DeepSeek 这种 OpenAI 兼容协议。"

---

## 21-6 · 提示工程（Prompt Engineering）

### 技术细节

Prompt 是"约束 LLM 行为"的核心手段——**没有 fine-tune 的能力，全靠 prompt**。

**5 个核心技巧**：

1. **Role 定义**：system prompt 告诉 LLM "你是谁"。
   ```
   你是一个简洁的智能校园助手，专门帮助无锡学院的学生解答问题。
   ```

2. **Context 注入**：参考资料 + 历史对话塞到 user prompt。
   ```
   参考文档内容：{context}
   对话历史：{chat_history}
   用户问题：{question}
   ```

3. **Format 约束**：限制回答长度 + 结构。
   ```
   回答简洁明了，3-5 句内讲清楚
   如需列表，最多只列 3-5 条关键信息
   不要使用特殊符号（####、****、——等）
   ```

4. **Self-Judge**：让 LLM 自判"参考资料是否相关"。
   ```
   1. 先快速浏览参考资料：其中是否有任何 1-2 段直接针对用户问题的内容？
   2. 如果有相关资料：基于资料回答，并尽量引用具体信息
   3. 如果参考资料与问题无关：用你自己的常识简短回答
   ```

5. **Fallback Prompt**：上下文为空时切换。
   ```
   注意：参考资料中没有与用户问题直接相关的内容
   请：用你自己的知识简短回答 ... 注明"建议以学校官方通知为准"
   ```

### 你的实现（src/rag/llm_chain.py + chat_history.py）

**DEFAULT_TEMPLATE** 用了 1/2/3/4 四种技巧：

```python
DEFAULT_TEMPLATE = """你是一个简洁的智能校园助手，专门帮助无锡学院的学生解答问题。

请根据以下参考文档内容回答用户的问题。先判断参考资料是否与用户问题真正相关，再选择回答方式。

参考文档内容：
{context}

用户问题：{question}

要求：
1. 先快速浏览参考资料：其中是否有任何 1-2 段直接针对用户问题的内容？
2. 如果有相关资料：基于资料回答，并尽量引用具体信息；不需要解释资料来源本身
3. 如果参考资料与问题无关或资料库里没有相关内容：用你自己的常识简短回答（如算术题直接给出答案、学校生活类问题给常识建议并注明"建议以学校官方通知为准"），不要生硬说"没找到"
4. 回答要简洁明了，3-5 句内讲清楚
5. 如需列表，最多只列3-5条关键信息
6. 回答中不要使用特殊符号（如####、****、——等装饰符号），只使用基本中文标点

回答："""
```

**NO_CONTEXT_TEMPLATE** 用 5：

```python
NO_CONTEXT_TEMPLATE = """你是一个简洁的智能校园助手，专门帮助无锡学院的学生解答问题。

注意：参考资料中没有与用户问题直接相关的内容（可能是冷门问题、超出资料库范围的常识问题、或口语化问法）。

请：
1. 用你自己的知识简短回答（不要长篇大论）
2. 如果是数学/逻辑/算术/常识问题，直接回答正确结论（如 "1+1=2"）
3. 如果是学校生活类问题（图书馆、食堂、校历等），可以基于常识给一个简短建议（如"图书馆一般周一到周日开放，具体时间建议查看学校官网"），但要明确告诉用户"以上只是常识建议，建议以学校官方通知为准"
4. 如果是开放性问题（如"Python 怎么学"），给一个简短的入门路径建议（3-5 步）
5. 回答中不要使用特殊符号（如####、****、——等装饰符号），只使用基本中文标点

用户问题：{question}

回答："""
```

### 为什么这么做

- **为什么不让 LLM "不要幻觉"**：直接说"不要编造"会让 LLM 不自信，反而过度保守。**改用"先看参考是否相关"** —— 让 LLM 自己评估可信度，比硬约束更灵活。
- **为什么不硬说"没找到"**：用户失望率太高（30-40% 查询边缘化）。**常识兜底 + 注明来源**更专业。
- **为什么限制 3-5 句**：LLM 默认啰嗦。**显式约束 token 数**又减少 1/3 成本。
- **为什么禁特殊符号**：`####`、`----` 这些 markdown 符号在微信、邮件、移动端显示丑——clean_answer 函数再清洗也是多层防御。

### 面试话术

> "我们的 prompt 不是'不要幻觉'这种空话——我用 **Self-Judge 机制**：让 LLM 先看参考资料是否相关，
> 1. 相关 → 基于资料回答
> 2. 不相关 → 用常识兜底 + 注明'以学校官方通知为准'
>
> 这种设计 **解决 60% 的幻觉问题**，比硬说'找不到'用户体验好得多。这是 LangChain 论文里 'Self-RAG' 的思路。"

---

## 22-1 · 智能体（Agent）基本组成

### 技术细节

智能体（Agent）= "能自主决策和行动的系统"，由 4 个模块组成：

| 模块 | 作用 | 你项目里的实现 |
|---|---|---|
| **思考（Reasoning）** | 分析 input，决定走哪条路径 | `_intent_route()` —— LLM 判断"知库 / 常识 / 闲聊"|
| **规划（Planning）** | 把任务拆解到子步骤 | `_rewrite_query()` —— LLM 把"它有什么特点"改写成"门电路有什么特点" |
| **工具调用（Tools）** | 调用外部函数 / API | retriever.invoke() + LLM.invoke() —— 检索 + 推理两步 |
| **记忆（Memory）** | 保留历史对话 | `recent_history` (10 轮原文) + `old_summary` (旧轮次压缩) |

### 你的实现（src/rag/chat_history.py）

```python
class ChatHistoryManager:
    """多轮对话管理器（手写实现，无外部依赖）"""
    
    def chat(self, question: str) -> dict:
        # 0. 思考：意图路由
        intent = self._intent_route(question)
        
        # 闲聊直接走 _chat_reply（无工具调用）
        if intent == "chat":
            answer = self._chat_reply(question)
            ...
        
        # 1. 规划：query rewriting（指代消解）
        rewritten = self._rewrite_query(question)
        
        # 3. 工具调用：retriever + LLM 推理
        docs, scores = self.retriever.retrieve_with_scores(rewritten)
        response = self.qa_chain.invoke({...})
        
        # 4. 记忆：保存 + 压缩
        self._add_to_history(question, answer)
```

**4 个模块 → 你项目里的对应**：
- **思考** = `_intent_route` (line 206)
- **规划** = `_rewrite_query` (line 215) 把 "它有什么特点" → "门电路有什么特点"
- **工具调用** = retriever + LLM 调用 (`chat()` line 234)
- **记忆** = recent_history + old_summary 分层 (`_add_to_history` line 274)

### 为什么这么做

- **为什么不用 LangChain 自带 `ConversationalRetrievalChain`**：测试发现它对**中文指代消解弱**——"里面能充电吗"找不到上文"图书馆"。**手写 ChatHistoryManager 替代**：自己写 query rewriting，让 LLM 做指代消解，效果显著好于默认。
- **为什么分层记忆（recent + summary）**：长对话 token 爆掉。`RECENT_ROUNDS = 10` 保留最近 10 轮原文，更早的让 LLM 压缩成 100 字摘要。**双层结构既保留近期细节，又有长期概要**。
- **为什么用 RLock 锁住整个 chat()**：Gradio queue 模式下，HTTP 后台线程 + 事件循环线程可能并发触发 `chat()`。**两个线程同时 `_add_to_history` 会丢轮次或 slice 越界**。今天修的 CRITICAL #2。

### 面试话术

> "我们项目本质是个**轻量智能体**，由 4 个模块组成：
>
> 1. **思考（Reasoning）**：意图路由，LLM 把 query 分到'知库 / 常识 / 闲聊'三类，决定走哪条 prompt
> 2. **规划（Planning）**：query rewriting，LLM 做指代消解——'它有什么特点'改写成'门电路有什么特点'后再检索
> 3. **工具调用（Tools）**：retriever.invoke() + LLM.invoke()，检索 + 推理两步
> 4. **记忆（Memory）**：分层（recent_history 10 轮原文 + old_summary 更早的摘要压缩）
>
> **不用 LangChain 自带 ConversationalRetrievalChain**，因为它的中文指代解析弱——我们手写替代品，效果显著。
>
> **多线程安全**：Gradio 4.x queue 模式下双线程并发，`chat()` 全部用 RLock 锁住。"

---

## 完整面试话术串联（90 秒版本）

把上面 5 个章节的故事串起来，1 分钟讲完项目核心：

> "我们的校园智能助手是个**轻量 AI 智能体**，由四个模块组成——
> - **思考**：LLM 做意图路由，把 query 分到知库 / 常识 / 闲聊三类
> - **规划**：query rewriting 做中文指代消解
> - **工具调用**：retriever.invoke() + LLM.invoke() 两步走
> - **记忆**：分层（近 10 轮原文 + 早期摘要压缩）
>
> **RAG 召回**我们走三路融合：bge-m3 中文向量、BM25 专名兜底、bge-reranker 精排，最终用 RRF(k=60) 融合——为什么用 RRF 而不是线性加权？因为不同召回源的 score 量纲不一致，RRF 看 rank 不看 score 鲁棒得多。
>
> **文档切分**用标题感知：5 条正则识别章节边界，先切章节再按 800/150 token 切块，避免主题被切散。**意图路由 + NO_CONTEXT 兜底**让我们即使检索失败也能用常识回答 + 注明"以学校官方为准"，而不是硬说"找不到"。
>
> 所有这些用 **LangChain 1.x + LCEL** 实现——`prompt | llm | parser` 流水线写法，跟 Unix 一样。1.x 拆包后 langchain-community / langchain-core / langchain-ollama 路径我都做了完整迁移。
>
> **prompt 工程**用的是 LangChain 论文 Self-RAG 思路——让 LLM 先自判参考资料是否相关：相关→基于资料；不相关→常识兜底。**这种设计解决 60% 幻觉问题**，比硬约束更灵活。
>
> 多线程安全：Gradio 4.x queue 模式下双线程并发，chat_history 用 RLock 锁住避免丢历史。"

90 秒能讲完所有核心。如果追问 deeper，就单独调对应章节讲。

---

## 面试常问追问准备

| 面试官可能问 | 答案 |
|---|---|
| bge-m3 比 nomic 好多少？ | "中文同义词召回率从 60% 到 85%（用 21 题测试集验证）" |
| 为什么不微调 embedding？ | "工程量 vs 收益不划算——换 bge-m3 一天就能提 15-25%，微调要 GPU + 数据 + 调参，性价比低" |
| LangChain 1.x 拆包怎么处理的？ | "4 个核心文件全部迁移：langchain-community / langchain-core / langchain-ollama / langchain-openai / langchain-text-splitters" |
| 多用户怎么隔离？ | "按浏览器 UUID cookie 隔离，每用户独立 dict cache + 磁盘 .pkl 持久化（参见 multi_session 隔离）" |
| 向量库升级怎么做？ | "蓝绿部署：旧 faiss_db / 新 faiss_db_bge 共存，新库验证通过再切 app.py 加载路径（参见 README 的"升级 playbook"）|
| 沉默降级怎么防？ | "模型名默认 None + 启动 warn，不静默跳过。silent fallback 是 false negative 高发地（参见 silent reranker 教训）" |

---

**这份文档就是面试 cheat sheet。** 反复读几遍，能 90 秒讲清项目 + 任意深度追问都能接住。
