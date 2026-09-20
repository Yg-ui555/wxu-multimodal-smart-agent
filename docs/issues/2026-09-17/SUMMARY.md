# WXU 项目日报 - 2026-09-17 总结

> **今日主题**：从"切库 → 修 bug → 多用户多 session → 评估仪表盘 → HyDE 实验 → 项目调研 + Agent 概念厘清"一整天的完整闭环
>
> 本文整合：[2026-09-17/README.md](README.md)（上午+中午） + 下午/晚上对话中的产出
> 适用：项目自查 / 面试叙述 / 复盘答辩

---

## 🕐 全天时间线

| 时段 | 主线 | 关键产出 | 状态 |
|---|---|---|---|
| **上午 09:00-12:00** | 切 bge 库 + 修 runtime 翻车 | monkey-patch pydantic / 14 处 bug fix / smoke test 4/4 命中 | ✅ |
| **中午 12:00-14:00** | 静态审计 + 一次性修复 | 14 处修复（3 CRITICAL + 4 HIGH + 5 MEDIUM + 2 LOW）| ✅ |
| **下午 14:00-18:00** | 产品能力建设 | 用户上传 / 多用户隔离 / 多 session（豆包式）/ 评估仪表盘 | ✅ |
| **下午 18:00-19:00** | **HyDE 实验第一轮** | `_hyde_expand()` 拼接版 → 60% pass 平 / recall@4 65.55% ↓ | ⚠️ 平 |
| **晚上 19:00-21:30** | 调研 + Agent 概念厘清 | 中文 RAG 5 强 + Qwen-Agent 重新定位 + Agent = LLM 调工具 | ✅ |

---

## 🌅 上午：切 bge 库 + 修运行时翻车

> 详细见 [README.md#上午](README.md#上午)

### 三道坎

1. **pydantic v1 compat 反序列化失败** → 5 行 monkey-patch（`_compat_patch_for_legacy_pickle`）
2. **`BGEReranker(model=...)` typo** → 改 `model_name=...`
3. **UI "错误"红框** → Chatbot/Markdown 加非空 placeholder

### 4 个查询 smoke test 验证
| 查询 | 命中 |
|---|---|
| 门电路是什么 | ✅ 第二章-门电路.pdf（**bge-m3 同义词召回核心收益**）|
| 图书馆开放时间 | ✅ 教务处通知 |
| 奖学金评定标准 | ✅ 评定办法 |
| 校历 2024-2025 | ✅ 教学日历 |

---

## ☀️ 中午：14 处一次性修复

| 严重度 | 数 | 关键 |
|---|---|---|
| 🔴 CRITICAL | 3 | done 条件化 / RLock 防并发 / 注释对齐 |
| 🟡 HIGH | 4 | reranker_model=None+warn / clean_answer import / Unicode 空白 regex / normalize_url 真归一 |
| 🟢 MEDIUM / LOW | 7 | 异常脱敏 / 类型防御 / load try-except / 删 dead import 等 |

**修复完 4/4 smoke test 不变**——证明全部是 cleanup，不影响主链路。

---

## 🌇 下午：产品能力 + 评估体系

### 1. 用户上传文档
- Tab2 + `gr.File` + dedup_chunks + 50MB 限制 + 容错
- `.txt/.md/.pdf/.docx/.doc` 五格式

### 2. 多用户隔离
- UUID cookie + localStorage
- `data/user_histories/<uid>/index.json` 持久化

### 3. 多 session 隔离（豆包式）
- 左侧 session 列表 + ➕新对话 / 🗑删除 / 🔄刷新
- 每用户 dict cache + 每 session 磁盘 `.pkl`

### 4. 评估仪表盘
- `tests/eval_set.json`（15 题覆盖校历/奖学金/课程/同义词/闲聊/常识）
- `scripts/eval_runner.py`（跑测试集 + 算指标）
- `scripts/eval_report_html.py`（matplotlib bar chart + bad case 表格）
- `eval_html --html report.html` 一行命令出可视化对比

### Baseline 数据
| 指标 | 数值 |
|---|---|
| **pass_rate** | 60.0% |
| **recall@4** | 73.3% |
| **latency p50** | 3.11s |

---

## ⚠️ 下午晚：HyDE 实验第一轮（拼接版）

### 改了什么
- `retriever.py`：新增 `_hyde_expand()` → 拼接 "原问题 + LLM 假设答案" 作为检索 query
- `app.py`：启动时 `use_hyde=True`
- `eval_runner.py`：加 `--use-hyde` + `--label with_hyde`

### 结果（15 题 eval）
| 指标 | baseline | HyDE v1（拼接） | 变化 |
|---|---|---|---|
| pass_rate | 60.0% | 60.0% | 平 |
| **recall@4** | **73.3%** | **65.55%** | **↓ 6.66%** |
| answer_hit | - | 涨 | - |

### 结论
**拼接方案反降**——因为把"问句风格"和"答句风格"糊一起，污染了检索信号。

**正确做法（待做）**：两路分别检索 + RRF 融合
- 路 1：原 query 走 FAISS + BM25
- 路 2：LLM 假设答案走 FAISS
- 两个结果用 RRF(k=60) 融合

---

## 🌃 晚上：调研 + 概念厘清

### 1. RAGFlow 思路通俗讲解（用校园助手打比方）

| 概念 | 类比 |
|---|---|
| **parsers 拆细** | 整本学生手册撕成便利贴 → 按主题/地点/时间分类贴墙 |
| **HyDE 召回** | 先让 AI 假装是图书馆管理员写"周一 21:30 关门"，再去对文档 |
| **评估细化** | 不光记对错，记"答了哪些核心实体、有没有数字时间、有没有错" |

### 2. 中文 RAG 项目调研：从 10 个筛到 5 强

| 排名 | 项目 | 对你的核心价值 |
|---|---|---|
| 1 | **Qwen-Agent** ⭐重新定位 | Agent 工具调用框架（你项目缺这个）|
| 2 | Langchain-Chatchat | RAG 模块划分（跟你最像）|
| 3 | QAnything | BGE 中文检索整合 |
| 4 | RAGFlow | PDF 解析 / 切分粒度 |
| 5 | Dify / FastGPT | 产品化 UI |

**关键纠正**：Qwen-Agent 一开始被砍掉（"偏 Agent 框架不算 RAG"），但用户项目本身就是 Agent + RAG 混合体——**Qwen-Agent 是核心参考，不是补刀**。

### 3. Agent 概念厘清（**最关键的一次认知升级**）

> 用户原话："感觉 agent 好难"

**正解**：Agent 不是新东西，**用户已经在用 Agent 模式**——只是不知道它叫 Agent。

```
用户问"图书馆几点关门"
  ↓
LLM 看问题 → 决定要不要查文档
  ↓
查 RAG → 把答案塞回去
  ↓
LLM 组织语言回复
```

这就是 Agent。

**Agent 框架做的事**：把"LLM 决定要不要查文档"的 if-else，从手写变成 LLM 自己挑。

| 术语 | 白话 |
|---|---|
| Function Calling | 让 LLM 输出结构化 JSON，照 JSON 调工具 |
| LangGraph | 画流程图，让 LLM 按图走 |
| ReAct | 想 → 干 → 看结果 → 再想 |

**全是 if-else 的包装**。

---

## 📁 今日涉及文件清单

| 模块 | 文件 | 改动 |
|---|---|---|
| RAG 核心 | `vector_store.py` | monkey-patch + done 条件化 + load try-except |
| | `retriever.py` | 14 处 fix + `_hyde_expand()` HyDE v1 |
| | `chat_history.py` | RLock + clean_answer import |
| | `data_loader.py` | Unicode 空白 regex |
| 前端 | `app.py` | 14 处 fix + Chatbot/Markdown placeholder + HyDE 启动 |
| | `upload_handler.py` | 新增：上传 + dedup + 容错 |
| | `user_store.py` | 重构：多用户多 session + 白名单 + 原子写 |
| 采集 | `daily_crawl.py` | normalize_url 真归一 |
| 评估 | `tests/eval_set.json` | 15 题 |
| | `scripts/eval_runner.py` | 跑测试集 + HyDE flag |
| | `scripts/eval_report_html.py` | HTML 报告生成 |
| 文档 | `docs/issues/2026-09-17/README.md` | 上午+中午日报 |
| | `docs/issues/2026-09-17/SUMMARY.md` | **本文档** |

---

## 📊 关键指标（今日变化）

| 指标 | 9/16 23:30 | 9/17 晚 21:30 | 变化 |
|---|---|---|---|
| 向量库 chunks | 29165 | 29169（含 4 个上传测试）| +4 |
| 代码 bug 隐患 | 14 处 | 0 处 | **-14** |
| 沉默降级 | 1 处 | 0 处 | **-1** |
| 多用户隔离 | ❌ | ✅ | 新增 |
| 多 session | ❌ | ✅ | 新增 |
| 评估体系 | ❌ | ✅ 60% baseline | 新增 |
| HyDE 验证 | 未做 | v1 平 / v2 待做 | 新增 |

---

## 🎓 经验教训（今天新增）

### 教训 1：看了概念 ≠ 落地有效
> HyDE 论文里 +15%，你的项目 +0%——**query set 不同 / 文档分布不同，结果就不同**。但这本身就是进步：你现在懂什么是 HyDE、为什么这次没起效、正确做法是啥。

### 教训 2：拼接 ≠ 融合
> "把两个东西粘一起"和"两路分别检索 + RRF 融合"是两种完全不同的工程思路。前者污染信号，后者保留两路优势。

### 教训 3：项目类别决定参考项目类别
> 你的项目是 Agent + RAG 混合体，不能用"纯 RAG 标杆"的标准挑参考项目。**Qwen-Agent 是核心参考，不是补刀**——这是看自己项目时容易犯的盲点。

### 教训 4：Agent 不难，是名字吓人
> "Agent 框架"听着高大上，本质就是"LLM 自己决定调哪个工具"。你已经在做的 RAG+LLM+yolo 就是 Agent 雏形——**30 行代码就能跑通一个最小 Agent**。

### 教训 5：评估比感觉靠谱
> "我优化了"是感觉，"60% pass / 73.3% recall@4"是事实。**没有 baseline 的优化都是耍流氓**——今天 HyDE 第一轮跑出来平，立刻知道"这次没起效"，省了一周自我怀疑。

---

## 🚀 明天继续做

| 优先级 | 任务 | 预计 | 价值 |
|---|---|---|---|
| ⭐⭐⭐⭐⭐ | **HyDE v2：两路独立检索 + RRF 融合** | 2h | recall@4 目标 75%+ |
| ⭐⭐⭐⭐⭐ | **看 Qwen-Agent README**（拉下 1 个项目对照 20 分钟）| 1h | 面试叙述立刻上档次 |
| ⭐⭐⭐⭐ | 录 1-2 分钟演示视频 | 半天 | **面试 ROI 最高** |
| ⭐⭐⭐ | 修 BGE-reranker（真实加载）| 半天 | recall +10% |
| ⭐⭐⭐ | 写 GitHub README + 配截图 | 半天 | 简历门面 |

---

## 🎯 一句话总结

> **9.17 是从"工程能跑"到"产品级 AI 应用"再到"知道下一步该往哪里走"的一天**。
>
> 上午把库真正接上线跑起来（5 行 monkey-patch 兜底 + 14 处清理），下午补齐产品能力（上传/多用户/多 session/评估），晚上做了一次不达预期的 HyDE 实验但拿到了真数据 + 把 Agent 概念从"看着吓人"变成"其实你已经在用"。

**今天最大的收获不是代码，而是：**
- **评估数据告诉你"这次优化没起效"** —— 这是工程化必备的元能力
- **Qwen-Agent 是核心参考不是补刀** —— 这是项目定位的元认知
- **Agent 不难是名字吓人** —— 这是面试时敢讲的元自信

---

## 🔗 相关文档

- [9.17 上午+中午日报](README.md)
- [9.15-17 三天总结](../2026-09-15-17/SUMMARY.md)
- [面试 cheat sheet](../interview_prep_tech_details.md)
- [Bad case 报告](../../tests/bad_case_report.md)