# -*- coding: utf-8 -*-
"""
多跳检索对比评估（2026-10-12 新增）
=================================================

目的：
    对比 "基线 HybridRetriever" vs "MultiHopRetriever" 在跨文档题目上的召回表现。
    直接出 before/after 数字，写进 README。

用法：
    # 跑对比（基线 vs 多跳）
    python scripts/eval_multihop.py

    # 只跑多跳
    python scripts/eval_multihop.py --mode multihop

    # 只跑基线
    python scripts/eval_multihop.py --mode baseline

输出：
    tests/eval_multihop_report.json  # 完整结果
    控制台打印: 每个 case 的 pass/fail + 总分对比

设计要点（面试能讲）：
    - 公平对比：两个 retriever 共享同一份 docs + 同一份 LLM，只换"拆不拆"那一层
    - 指标分开记：recall@4 / pass_rate / latency / 多跳自身统计
    - 输出 README 友好的 markdown 表格（直接复制）
"""
import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("eval_multihop")

MULTIHOP_SET = PROJECT_ROOT / "tests" / "eval_set_multihop.json"
REPORT_PATH = PROJECT_ROOT / "tests" / "eval_multihop_report.json"


# ============================================================
# 评分逻辑（和 eval_runner 对齐）
# ============================================================
def _doc_text(d) -> str:
    """拼检索口径:source + page_content"""
    src = (d.metadata or {}).get("source", "")
    return src + "\n" + (d.page_content or "")


def score_case(case: dict, docs: list) -> dict:
    """
    多跳题评分（比通用 eval 略宽松，因为跨文档题本身就难）
    - recall@4: 期望关键词是否出现在 top-4 docs 里（不要求全中，中一个就算召回）
    - partial_match: 至少命中 2 个 expected_keywords
    - passed: 至少命中 2 个（多跳题关键指标）
    """
    expected = case.get("expected_keywords") or []
    docs_text = "".join(_doc_text(d) for d in docs[:4])
    hit = [kw for kw in expected if kw in docs_text]
    return {
        "recall_count": len(hit),
        "recall_total": len(expected),
        "recall@4": len(hit) > 0,               # 至少命中 1 个就算召回
        "partial_match": len(hit) >= 2,          # 命中 ≥2 个算"多跳成功"
        "passed": len(hit) >= 2,                 # 多跳通过的判定标准
        "hit_keywords": hit,
        "miss_keywords": [kw for kw in expected if kw not in docs_text],
    }


# ============================================================
# 跑单条
# ============================================================
def run_one(retriever, case: dict) -> tuple:
    """跑一条 case，返回 (score_dict, docs, latency)"""
    t0 = time.time()
    try:
        docs = retriever.retrieve(case["question"])
    except Exception as e:
        log.warning("retriever 异常: %r", e)
        return score_case(case, []), [], 0.0
    dt = time.time() - t0
    return score_case(case, docs), docs, dt


# ============================================================
# 主流程
# ============================================================
def run_compare(vector_db_dir: str = "data/faiss_db_bge", mode: str = "compare"):
    """
    跑多跳评估
    Args:
        vector_db_dir: 向量库路径
        mode: "baseline" / "multihop" / "compare"
    """
    from src.rag.vector_store import VectorStoreManager
    from src.rag.retriever import create_retriever
    from src.rag.llm_chain import create_qa_system
    from src.rag.multi_hop import MultiHopRetriever

    print(f"[multihop-eval] 启动 {datetime.now().isoformat(timespec='seconds')}")
    print(f"[multihop-eval] mode={mode}, vector_db={vector_db_dir}")

    # 1. 加载向量库 + 基础 retriever
    mgr = VectorStoreManager(persist_directory=vector_db_dir)
    mgr.load_vectorstore()
    base_retriever = create_retriever(
        mgr.vectorstore,
        documents=mgr._documents,
        top_k=4,
        use_bm25=True,
        use_reranker=False,         # 与生产同步：关 reranker
        vector_top_k=8,
        bm25_top_k=8,
    )
    print(f"[multihop-eval] 基础 retriever 就绪: {len(mgr._documents)} 文档")

    # 2. 加载 LLM（用于 query decomposition）
    qa = create_qa_system(base_retriever)
    llm = qa.llm

    # 3. 构造多跳 retriever
    multihop = MultiHopRetriever(
        base_retriever=base_retriever,
        llm=llm,
        max_sub_queries=3,
        top_k=4,
    )

    # 4. 加载测试集
    cases = json.load(open(MULTIHOP_SET, "r", encoding="utf-8"))
    print(f"[multihop-eval] 加载 {len(cases)} 道多跳题\n")

    # 5. 跑 baseline
    baseline_results = []
    if mode in ("baseline", "compare"):
        print("=" * 60)
        print("📊 跑 基线 HybridRetriever（单跳）")
        print("=" * 60)
        for case in cases:
            sc, docs, dt = run_one(base_retriever, case)
            baseline_results.append({
                **{k: v for k, v in case.items() if k != "note"},
                **sc,
                "latency_sec": round(dt, 2),
            })
            mark = "✅" if sc["passed"] else "❌"
            print(f"  {mark} Q{case['id']}: 命中 {sc['recall_count']}/{sc['recall_total']} | {dt:.2f}s | {case['question'][:30]}...")

    # 6. 跑 multihop
    multihop_results = []
    if mode in ("multihop", "compare"):
        print("\n" + "=" * 60)
        print("📊 跑 MultiHopRetriever（多跳）")
        print("=" * 60)
        for case in cases:
            sc, docs, dt = run_one(multihop, case)
            multihop_results.append({
                **{k: v for k, v in case.items() if k != "note"},
                **sc,
                "latency_sec": round(dt, 2),
            })
            mark = "✅" if sc["passed"] else "❌"
            print(f"  {mark} Q{case['id']}: 命中 {sc['recall_count']}/{sc['recall_total']} | {dt:.2f}s | {case['question'][:30]}...")

    # 7. 汇总
    def summarize(rs, label):
        if not rs:
            return None
        n = len(rs)
        return {
            "label": label,
            "total": n,
            "passed": sum(1 for r in rs if r["passed"]),
            "pass_rate": round(sum(1 for r in rs if r["passed"]) / n, 4),
            "recall_at_4": round(sum(1 for r in rs if r["recall@4"]) / n, 4),
            "partial_match_rate": round(sum(1 for r in rs if r["partial_match"]) / n, 4),
            "avg_latency_sec": round(sum(r["latency_sec"] for r in rs) / n, 2),
        }

    summary = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "vector_db_dir": vector_db_dir,
        "baseline": summarize(baseline_results, "baseline(单跳)"),
        "multihop": summarize(multihop_results, "multihop(多跳)"),
        "multihop_stats": multihop.get_stats() if multihop_results else None,
    }

    # 8. 控制台对比表
    print("\n" + "=" * 60)
    print("📈  对比汇总")
    print("=" * 60)
    if summary["baseline"] and summary["multihop"]:
        b = summary["baseline"]
        m = summary["multihop"]
        print(f"{'指标':25s} {'基线(单跳)':>15s} {'多跳':>15s} {'Δ':>10s}")
        print("-" * 70)
        for k, label in [("pass_rate", "pass_rate(≥2命中)"),
                         ("recall_at_4", "recall@4(≥1命中)"),
                         ("partial_match_rate", "partial_match"),
                         ("avg_latency_sec", "avg_latency")]:
            bv = b.get(k, 0)
            mv = m.get(k, 0)
            delta = mv - bv
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
            print(f"  {label:23s} {bv:>15.4f} {mv:>15.4f} {arrow} {delta:>+8.4f}")

        print(f"\n💡 多跳统计: {summary['multihop_stats']}")

        # 9. 输出可直接贴 README 的 markdown
        print("\n" + "=" * 60)
        print("📋 README 可用 Markdown 表格（直接复制）")
        print("=" * 60)
        print("| 配置 | pass_rate | recall@4 | avg_latency |")
        print("|---|---|---|---|")
        print(f"| **基线**（单跳 HybridRetriever）| {b['pass_rate']*100:.1f}% | {b['recall_at_4']*100:.1f}% | {b['avg_latency_sec']}s |")
        print(f"| **多跳**（MultiHopRetriever）| {m['pass_rate']*100:.1f}% | {m['recall_at_4']*100:.1f}% | {m['avg_latency_sec']}s |")

    # 10. 写报告
    report = {
        "summary": summary,
        "baseline_cases": baseline_results,
        "multihop_cases": multihop_results,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[multihop-eval] 报告写入: {REPORT_PATH}")

    return summary


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="多跳检索对比评估")
    ap.add_argument("--mode", choices=["baseline", "multihop", "compare"],
                    default="compare", help="跑哪种模式（默认 compare）")
    ap.add_argument("--vector-db", default="data/faiss_db_bge")
    args = ap.parse_args()

    run_compare(vector_db_dir=args.vector_db, mode=args.mode)
