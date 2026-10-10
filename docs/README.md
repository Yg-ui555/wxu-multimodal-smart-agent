# WXU 项目文档导航

> 这里是所有项目文档的索引。点开需要的文档即可。

## 项目必读
- [../README.md](../README.md) - 项目主页（架构、技术栈、评估结果、启动方式）

## 关键决策记录
- [RERANKER_DECISION.md](RERANKER_DECISION.md) - 重排模型（bge-reranker）启用/撤销决策与 TODO
- [LEARNING_GUIDE.md](LEARNING_GUIDE.md) - 项目能力地图（高频面试问题准备）

## 历史归档
- [audit/](audit/) - 早期审计报告（按日期）
- [issues/](issues/) - 早期 bug 修复记录（按日期）

## 测试 & 评估
- 跑分脚本：`scripts/eval_runner.py`
- 评估集：`tests/eval_set.json`（60 题）
- 当前线上基线：`tests/eval_baseline.json` / `tests/eval_current.json`
- 上限对比：`tests/eval_current_upper_bound.json`