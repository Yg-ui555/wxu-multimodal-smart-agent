# RERANKER 启用决策（2026-09-20）

> ⚠️ **本文决策已撤销（2026-10-09 修订）**：
>
> 本文当时基于 15 题基线（60%）决策"启用 BGE reranker"。但后续实测发现**开启 reranker 反而准确率下降**（见 [README.md](../README.md) "评估上限表"段）——生产环境已**关闭** reranker，只在评估脚本 `scripts/eval_runner.py` 里保留开关用于对比。
>
> 决策变更原因：bge-reranker-v2-m3 对短查询 + 校园短文本场景的语义匹配效果不稳定，多次实测结果低于不reranker 的混合检索（BM25 + 向量 + RRF）。生产取舍：**简单优于复杂**，关闭 reranker 节省显存 + 延迟。
>
> 下文保留作为决策记录参考，但**当前生产配置以 README 为准**。

> 本文记录项目第一个生产化改造的决策过程，方便后续维护 + 写项目 README 跟进。

## 1. 背景

RAG 检索当前 pipeline（生产路径）：

```
用户问题
  ↓
1. 向量召回（FAISS）→ top-10
2. BM25 召回（关键词）→ top-10
3. RRF 融合（Reciprocal Rank Fusion，逆秩融合） → top-10
4. BGE reranker（重排序）→ top-4 ← 本次改造点
5. LLM 生成答案
```

第 4 步的 `BGEReranker` 类代码已存在（`src/rag/retriever.py:125`），但调用方（`create_retriever`）未透传 `reranker_model` 参数，reranker 实际被跳过（启动日志警告："use_reranker=True 但 reranker_model=None，跳过重排"）。

## 2. 动机

baseline eval 显示（`tests/eval_current.json`，2026-09-17 跑）：

| 指标 | 值 |
|------|-----|
| pass_rate | 60.0% |
| recall@4_rate | 73.3% |
| answer_hit_rate | 66.7% |
| avg_precision | 0.8445 |

6 个失败 case 归因（详见 `tests/bad-case-report.md`）显示：

- **Case 8（图书馆借几本书）**：retrieval 阶段命中了无关文档（教材订购通知），应该被 reranker 挡掉
- **Case 10（你是谁）**：retrieval 阶段 Top-4 里 KB 自我介绍文档没进，reranker 也救不了
- **Case 15（选体育课规则）**：retrieval 阶段实际召回了相关文档（precision=0.5），但 Top-4 排序里被后面遗忘的更相关文档顶掉 → reranker 应能改善排序

预期 reranker 启用后：
- precision 提升 5-10%（行业经验值，参考 BAAI 官方 benchmark）
- latency p50 涨 200-400ms（CPU 推理）

## 3. 决策

**启用 BGE reranker-v2-m3**，理由：

| 维度 | 评估 |
|------|------|
| 精度提升 | 检索精度的"卡脖子"问题在 Top-K 排序，reranker 直接对症 |
| 延迟影响 | 可接受（3.11s → ~3.5s 仍 OK，UI 用户无感） |
| 资源占用 | CPU 推理即可，本地无 GPU 也能跑 |
| 维护成本 | 模型缓存到 `~/.cache/huggingface/`，复用即可 |
| 回滚 | 把 `reranker_model=None` 即可立刻回退 |

**风险**：
- 首次启动会下载 2.27 GB 模型（5-15 分钟），CI/CD / 新机器部署时要预留
- CPU 推理每次约 200ms，15 题 eval 比 baseline 多 3-5 秒
- HF 模型版本可能更新，pin 固定 `revision` 字段能锁定（**TODO：** 待补）

## 4. 实现

### 改动文件

| 文件 | 改动 |
|------|------|
| `requirements.txt` | 加 `sentence-transformers==6.1.0`（自动依赖 transformers + torch） |
| `src/rag/retriever.py` | `create_retriever()` 加 `reranker_model` 参数并透传给 `HybridRetriever`（之前忘了透传，导致外部传 `reranker_model=...` 报 TypeError） |
| `src/web_app/app.py` | `RAGApp._init_rag_retriever()` 传 `reranker_model='BAAI/bge-reranker-v2-m3'` |
| `scripts/eval_runner.py` | 同样传 `reranker_model='BAAI/bge-reranker-v2-m3'`，让 eval 跑的是真生产路径 |

### 模型缓存

- 路径：`~/.cache/huggingface/hub/`（Windows 默认 `C:\Users\<user>\.cache\huggingface\hub`）
- 大小：~2.27 GB（model.safetensors）
- 预下载方式：第一次实例化 `CrossEncoder('BAAI/bge-reranker-v2-m3')` 自动下载

### 验证

跑 eval（`scripts/eval_runner.py --label v5`），对比 `tests/eval_current_v4.json`（baseline，无 reranker）和 `tests/eval_current_v5.json`（启用 reranker）。

## 5. 后续 TODO

按"项目第一性"原则，这是**第一个**生产化改造，不是终点。剩余待办：

- [ ] **Pin 模型版本**：在 CrossEncoder 调用里传 `revision='xxx'` 锁定 HF commit，避免作者更新导致 breaking change
- [ ] **监控 reranker 失败率**：当前 `BGEReranker._load_model()` 失败时静默降级回原顺序，需要加 metric
- [ ] **GPU 推理**：如果有 GPU 资源，把 device 切到 CUDA 让 rerank 延迟降到 50ms 以内
- [ ] **chunk_size ablation（消融实验）**：当前 800，看 500/1200/1500 是否进一步提分
- [ ] **HyDE 决策**：看 `tests/eval_current_v5.json` 对比 `tests/eval_current_with_hyde.json`，数据驱动决定是否启用
- [ ] **抓图书馆借阅规则 PDF 入库**（修 Case 8 真 KB 缺口）
- [ ] **监控告警**（prod gap）：抓取失败 / LLM 报错 / latency 飙高无人知道
- [ ] **CI/CD**：自动跑 eval 回归，保证后续改动不破现有 case

## 6. 回滚预案

如果启用后出问题，回滚步骤：

1. 把 `src/web_app/app.py:368` 和 `scripts/eval_runner.py:100` 的 `reranker_model='BAAI/bge-reranker-v2-m3'` 改成 `None`
2. 重启服务
3. 重跑 eval 验证回到 baseline

不需要重建向量库，不需要改模型缓存。

---

**变更记录**

- 2026-09-20：决策启用 BGE reranker-v2-m3，提交后 commit