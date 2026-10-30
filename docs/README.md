# WXU 项目文档导航

> 这里是所有项目文档的索引。点开需要的文档即可。

## 项目必读

- [../README.md](../README.md) - 项目主页（架构、技术栈、评估结果、启动方式）
- [interview_prep_tech_details.md](interview_prep_tech_details.md) - **技术面试要点**（13 个高频问题 + 数字速查表 + 已知短板话术）

## 关键决策记录

- [RERANKER_DECISION.md](RERANKER_DECISION.md) - 重排模型（bge-reranker）启用/撤销决策与 TODO
- [LEARNING_GUIDE.md](LEARNING_GUIDE.md) - 项目能力地图（高频面试问题准备）
- [deployment.md](deployment.md) - Docker Compose 部署 + Grafana Dashboard 配置

## 历史归档

- [audit/](audit/) - 早期审计报告（按日期）
- [issues/](issues/) - 早期 bug 修复记录（按日期）

## 测试 & 评估

- 单元测试：`python -m pytest tests/ -q`（22 个用例，CI 里也会跑）
- 跑分脚本：`scripts/eval_runner.py`（线上同步配置：bge-m3 + BM25/向量 RRF 双路，无 reranker）
- 多跳对比：`scripts/eval_multihop.py --mode compare`
- 冒烟：`scripts/smoke_test.py`

**评估集**：

| 文件 | 是什么 |
|---|---|
| `tests/eval_set.json` | **当前生效**的评估集，300 题 |
| `tests/eval_set_300.json` | 生成脚本的产物（人工 review 后已合并进上面那份，内容一致） |
| `tests/eval_set_60_backup.json` | 60 题历史基线 —— README 的「60 题 vs 300 题」对比表依赖它，要能复现就留着 |
| `tests/eval_set_multihop.json` | 多跳检索专用小集（8 道跨文档题） |

> ⚠️ `tests/eval_baseline.json` / `tests/eval_current*.json` 是**本地跑分产物，不进版本库**（见 `.gitignore`）。
> 换机器 / 换人看这个仓库时，这些文件不存在是正常的 —— 用上面的脚本现跑一遍即可生成。
