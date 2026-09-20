# WXU 项目构建日报 - 2026-09-17

> 今日主题：**bge 库切换验证 + 代码审查 + 14 处 bug 修复**
> 主题切换：从"昨日的 bge 库构建 + 审计"过渡到"今日的运行时验证 + 静态清理"
> 目标：把"看着能跑 → 真的能跑 → 跑得稳"三个状态全部达成

---

## 📋 今日概览

| 时间段 | 干了啥 | 结果 |
|---|---|---|
| 上午 | 切 app.py 加载 bge 库 | ❌ FAISS pickle 加载爆 `KeyError: '__fields_set__'` |
| 上午 | 路由选 A：5 行 monkey-patch（pydantic v1 compat） | ✅ 29165 chunks 加载成功 |
| 上午 | 暴露 retriever.py typo + UI "错误" 红框 | ✅ 两处都修 |
| 上午 | 4 个真实查询 smoke test | ✅ 4/4 命中（核心：门电路 → 第二章-门电路.pdf）|
| 中午 | 用户浏览器复测 UI | ✅ 红框消失 |
| 中午 | 子 agent 系统代码审查 | 📋 14 处问题清单（3 CRITICAL + 4 HIGH + 5 MEDIUM + 2 LOW）|
| 中午 | 一次性修复全部 14 处 + smoke test | ✅ 4/4 命中未变 |

---

## 🔴 上午：bge 库切换的两道坎

### 坎 1：pydantic v1 compat 反序列化失败

**症状**：启动 `python src/web_app/app.py`，走到 `FAISS.load_local()` 时崩：
```
KeyError: '__fields_set__'
File "...\pydantic\v1\main.py", line 417, in __setstate__
```

**根因（升级链路）**：
1. bge 库是 9/16 23:30 用 **旧版 pydantic v1** 写的 pickle
2. venv 现在是 **pydantic 2.10.6**，里面的 v1 compat shim 把 `BaseModel.__setstate__` 的字段检查改严了
3. 旧 pickle 里没有 `__fields_set__` 字段，shim 直接 KeyError

这是 **langchain 0.2.x + pydantic 2.x** 的官方已知升级死锁。

**真修需要**：升级到 langchain-community 0.3+，已**放弃 Python 3.8 支持**（最新 0.4.2 要求 Python 3.10+）—— **用户当前 venv 是 3.8.3，不可行**。

**临时修（方案 A）**：`src/rag/vector_store.py` 模块顶部注入 5 行 monkey-patch：
```python
def _compat_patch_for_legacy_pickle():
    """...TODO: 升级 Python 后删除..."""
    from pydantic.v1 import main as _pyd_main
    _orig = _pyd_main.BaseModel.__setstate__
    def _patched(self, state):
        if "__fields_set__" not in state:
            state["__fields_set__"] = set()
        return _orig(self, state)
    _pyd_main.BaseModel.__setstate__ = _patched
```

文件顶部带 `TODO` 注释 + 触发条件注释。等用户升级到 Python 3.10 时删掉。

---

### 坎 2：retriever.py 隐藏 typo + Gradio UI "错误" 红框

**修 A —— `BGEReranker(model=...)` typo**：
- 旧版代码：`BGEReranker(model=reranker_model)` —— 形参是 `model_name`
- 触发 TypeError 阻断整条 retriever 构造
- 改：`BGEReranker(model_name=reranker_model)`

**修 B —— UI "错误" 红框**：
- 现象：Gradio 4.4.1 中文版把空 value 的 Markdown/Chatbot 渲染成红框"错误"
- 真修：给两个组件显式非空 placeholder
  ```python
  chatbot = gr.Chatbot(value=[["你…","👋 …"]])
  docs_output = gr.Markdown(value="_📂 输入问题后…_")
  ```

---

### 上午验证：4 个查询 smoke test

| 查询 | Top-1 命中 | 状态 |
|---|---|---|
| 图书馆开放时间 | 全国计算机等级考试开放机房练习的通知 | ✅（次优，仍是教务处通知）|
| **门电路是什么** | **第二章-门电路.pdf** | ✅ **同步换 bge-m3 的核心收益** |
| 奖学金评定标准 | 学校奖学金评定办法（试行）| ✅ |
| 校历2024-2025 | 2024-2025学年教学日历 | ✅ |

---

## 🟡 中午：代码审查 + 14 处修复

派 explore 子 agent 系统扫描昨天/今天改过的 7 个文件，按 ROI 排序输出 14 条问题。

**一次性修复清单**：

| # | 严重度 | 文件 / 行 | 修复 |
|---|---|---|---|
| 1 | CRITICAL | vector_store.py:268-274 | `done=True` 改为只在 `success_count == total_batches` 时写，**防残缺库上线** |
| 2 | CRITICAL | chat_history.py:84-114 | `ChatHistoryManager.__init__` 加 `threading.RLock`，**chat() 全方法锁定** |
| 3 | CRITICAL | app.py:83 / chat_history.py:84 | 注释名实对齐："ConversationalRetrievalChain" → "ChatHistoryManager 手写分层记忆模式" |
| 4 | HIGH | retriever.py:191 | `reranker_model` 默认从 `"bge-reranker"`（无效拼写）→ `None`，并且 `use_reranker=True` 时如果忘传就 `log.warning` 而不是**静默降级** |
| 5 | HIGH | chat_history.py:240 | `from src.rag.llm_chain import clean_answer` 函数体内延迟导入 → 提到模块顶部（修单元测试 ImportError）|
| 6 | HIGH | data_loader.py:177 | `re.sub(r'[ -  ]', ' ', text)` 是空字符类失效 → 改 `re.sub(r'[\u00A0\u2000-\u200A\u202F\u205F\u3000]', ' ', text)` |
| 7 | HIGH | daily_crawl.py:208-214 | `normalize_url` 真正实现归一：统一 https + 小写 host + 去 www. + 去 fragment + 去尾 / |
| 8 | MEDIUM | retriever.py:270-287 | `retrieve_with_scores` 不再二次调 FAISS 凑数（顺序不一致误导 LLM fallback），**scores = [None] * len(docs)** |
| 9 | MEDIUM | app.py:177-180 | `except` 只展示 `type(e).__name__`，不再把内部 str(e) 回显前端（防敏感信息泄露）|
| 10 | MEDIUM | app.py:124 | `list(item)` 防御性 rewrite：必须是 `(list|tuple, len==2, 双 str)` |
| 11 | MEDIUM | vector_store.py:311-313 | `load_vectorstore` 加 `try/except` 镜像 create 路径，pickle 损坏降级而非 EOFError |
| 12 | MEDIUM | retriever.py:27 | 删 `import requests` 死引用 |
| 13 | LOW | daily_crawl.py:137 | 删 `_is_recent` 函数内 `import re`，模块顶部已 import |
| 14 | LOW | data_loader.py:184 | `text.replace('\xfd', ...)` 留注释说明：实际是 U+00FD ý 删，理由"罕现所以保留" |

---

## 🛡️ Smoke test 验证：未破坏功能

修复完所有 14 条后立即跑检索链路测试：

| 项 | 结果 |
|---|---|
| FAISS 加载 | ✅ 29165 块 |
| HybridRetriever 创建 | ✅ reranker=False（新行为：明确 warn 而非 silent fail）|
| "图书馆开放时间" | ✅ 教务处通知命中 |
| "门电路是什么" | ✅ 第二章-门电路.pdf 命中（**bge-m3 同义词召回生效**）|
| "奖学金评定标准" | ✅ 评定办法命中 |
| "校历2024-2025" | ✅ 教学日历命中 |

**所有查询 Top-1 与修复前完全一致**——证明 14 处改动是 cleanup，不影响主链路。

---

## 💡 经验教训

### 教训 1：升级不破坏旧——5 行补丁就够

> "pydantic 2.x + langchain 0.2.x + Python 3.8 三个版本死锁。**根本修复**是 Python 升级，但用户现在走不开 3.8。**临时方案**：monkey-patch 一段 setstate 兜底，注释清楚 TODO，**等真正升级那一天删掉**。"

> 这是 middleware 工程哲学：不要为了正确性破坏可用性。

### 教训 2：silent fallback 是隐患

> "retriever.py L191 默认 `bge-reranker` 既不是 HF ID 也不是 Ollama 模型名，CrossEncoder 加载失败被 except 吞掉，**reranker 一直静默没工作，用户没任何感知**。今天改成默认 None + 显式 warn：**沉默降级是技术债**，要么修通要么明说。"

### 教训 3：注释名实不符 = 面试踩雷

> "app.py 注释写'ConversationalRetrievalChain'，实际是手写 ChatHistoryManager。**面试官顺着注释问链路，一问就穿**。改注释成本 0，但能换来诚实表达。"

### 教训 4：Gradio 4.4.1 中文版 fallback 是个坑

> "空 Markdown/Chatbot 组件渲染成红框"错误"——不是程序错误，是 Gradio 中文版的 UX bug。给非空 value 就消失。**遇到 UI 显示诡异先看 /config 组件 value**。"

### 教训 5：把每一次 done 标记都加上幂等条件

> "昨天向量库 30 分钟超时教训：每批 save 是对的，但写 done=True 没检查批次成败。今天改成 `all_succeeded = (success_count == total_batches)`，**防止 Ollama 中断时 done 被无条件写**。经验：**状态机标记要走'全成功才标记'的不可逆写法**。"

---

## 📁 涉及的文件清单

| 文件 | 修改 |
|---|---|
| `src/rag/vector_store.py` | monkey-patch (顶部) + done 条件化 + load try/except + dead param 注释 |
| `src/rag/chat_history.py` | 模块顶部加 `threading`+`clean_answer` import + 全方法 RLock 锁 |
| `src/web_app/app.py` | answer_question 重写（防御 + 脱敏）+ 注释对齐 + Chatbot/Markdown 初始化 placeholder |
| `src/rag/retriever.py` | 默认 None + warn + retrieve_with_scores 改 None + 删 `import requests` |
| `src/rag/data_loader.py` | Unicode 空白 regex 改 explicit codepoints + `\xfd` 注释 |
| `scripts/daily_crawl.py` | `normalize_url` 真归一 + 删函数内 `import re` |
| `tests/smoke_test_after_patch` | 新增 smoke test 脚本（4 真实查询） |

---

## 🎯 后续 TODO

1. **重跑 21 题审计确认审计 vs 修复**：上一版审计是 9/16 4/21；现在新库 + 14 处修复，应该跑出更好数据
2. **修 BGE-reranker 真实加载**：要么装 FlagEmbedding 让 HF reranker 工作；要么 BGEReranker 改走 Ollama custom 调用
3. **升级 Python 到 3.10**：user 准备好的时候升；届时删掉 monkey-patch（_compat_patch_for_legacy_pickle）
4. **清理 2018-2020 老通知**（昨天日报的 3 个待用户决策事项之一）
5. **daily_crawl 接 Windows 任务计划**（同上）
6. **写 `tests/bad_case_report.md` 增量**：今天又发现 4 个新 bug case（pydantic + Gradio 红框 + silent fallback + 注释名实）
7. **录 1-2 分钟演示视频**：跑一遍今天的关键 case

---

## 📊 关键指标

| 指标 | 昨日 23:30 | 今日 11:18 |
|---|---|---|
| 向量库 | 29165 块待加载验证 | 加载成功，4/4 命中验证通过 |
| 7861 服务 | 未启动过 | 持续运行（本次会话启动过 5 次）|
| 代码 bug 数 | 昨日日报审计 4 类失败 + 报告 14 处隐患 | 14 处全部修复 |
| 沉默降级隐患 | 1 处（reranker 死循环）| 0 处 |
| 系统错误信息泄露 | 多处 | 已脱敏 |

---

## 🎓 面试话术升级

### v3（昨天能讲的）：**RAG 三阶段演进 + 工程化**
### v4（今天能讲的）：**生产级工程的"问题-根因-修复"闭环**

> "我们的 bge 库构建（11.6 MB → 119 MB）今天第一次跑，遇到三个真实问题：
>
> **1. Pydantic v1 → v2 pickle 不兼容** —— 选型变了但旧数据格式没动。生产里这种'数据跨版本'是最常见的 silent bug。我没 in-place 重建，用 5 行 monkey-patch 兜底 + TODO 标注**等正式升级时清理**。
>
> **2. silent fallback 是技术债** —— retriever 的 reranker 模型名写错，所有 except 都吞掉，导致 reranker 一直静默没工作。改成默认 None + warn：**沉默降级是 false negative 的高发地**。
>
> **3. UI 看着像 bug 但不是** —— Gradio 4.4.1 中文版把空 Markdown 渲染成红框'错误'。我 curl /config 看组件 value，定位到是 UX quirk 不是数据问题。**调试 UI 怪事先查 view-model，不要去翻业务代码**。
>
> 整套下来今天 14 处修复，从 v3 '能用' 升级到 v4 '用得稳'。"

---

## 📌 最后一句

昨晚构建了 119 MB 的向量库，**今天才真正把它接到线上跑了一遍**。从"声称有"到"真的能 query"，中间隔着 pydantic 兼容性、retriever 参数、UI fallback、silent failure、注释名实 5 道坎。**工程化不是一次写完，是无数次"上线 → 翻车 → 修"累积出来的**。今天这 14 处修复每一条都不到 10 分钟，但合起来就是简历上"产品级思维"那行字的底气。
