# -*- coding: utf-8 -*-
"""
评估 Runner：跑 tests/eval_set.json → 算指标 → 输出 JSON 与 HTML 报告
用法：
    python scripts/eval_runner.py --save-baseline   # 首次：存 baseline.json
    python scripts/eval_runner.py                   # 跟历史 baseline 对比
    python scripts/eval_runner.py --html report.html # 加 HTML 报告
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ─────────────────────────── 加载测试集 ───────────────────────────
DEFAULT_SET = PROJECT_ROOT / "tests" / "eval_set.json"
BASELINE_PATH = PROJECT_ROOT / "tests" / "eval_baseline.json"
CURRENT_PATH = PROJECT_ROOT / "tests" / "eval_current.json"


def load_eval_set(path: Path) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────── 单题评分 ───────────────────────────
def _doc_text(d) -> str:
    """把 Document 的 page_content + 文件名拼成检索口径字符串"""
    src = (d.metadata or {}).get("source", "")
    return (src + "\n" + (d.page_content or ""))


def score_case(case: dict, answer: str, docs: list) -> dict:
    """
    返回每题评分：
        recall@4    : top-4 docs 里是否含全部 must-have keywords
        precision   : top-4 docs 的字符里 must-have keyword 占比
        answer_hit  : answer 文本里是否含 must-have keywords
        banned_hit  : answer 含 banned_keywords（应 = 0）
    """
    expected = case.get("expected_keywords") or []
    banned = case.get("banned_keywords") or []
    docs_text = "".join(_doc_text(d) for d in docs[:4])

    recall = all((kw in docs_text) for kw in expected) if expected else True
    answer_hit = all((kw in answer) for kw in expected) if expected else True
    # precision: 用 must-have keywords 在前 4 篇 doc 文本里的命中率除以 keyword 数
    if expected:
        hit = sum(1 for kw in expected if kw in docs_text)
        precision = hit / len(expected)
    else:
        # 没有 expected 时，按"answer_hit == True"算
        precision = 1.0 if not banned else (0.0 if any((bk in answer) for bk in banned) else 1.0)

    # banned：answer 文本不应该出现 banned
    banned_hit = any((bk in answer) for bk in banned)

    return {
        "recall@4": bool(recall),
        "precision": round(precision, 3),
        "answer_hit": bool(answer_hit),
        "banned_hit": bool(banned_hit),
        # 是否算 pass：recall OK + answer_hit OK + 没 banned
        "passed": bool(recall and answer_hit and not banned_hit),
    }


# ─────────────────────────── 主跑评估 ───────────────────────────
def run_eval(vector_db_dir: str = "data/faiss_db_bge", use_simple: bool = False,
            use_hyde: bool = False, use_upper_bound: bool = False) -> dict:
    """
    跑整个 eval_set，返回结构化结果（含 summary + cases）。

    Args:
        vector_db_dir: 向量库路径
        use_simple: 是否用 SimpleChatManager（默认用 ChatHistoryManager）
        use_hyde: 是否启用 HyDE 检索增强（2026-09-17 新加）
    """
    from src.rag.vector_store import VectorStoreManager
    from src.rag.retriever import create_retriever
    from src.rag.llm_chain import create_qa_system
    from src.rag.chat_history import create_chat_manager

    print(f"[eval] 启动 {datetime.now().isoformat(timespec='seconds')}")
    print(f"[eval] 配置: use_hyde={use_hyde} use_upper_bound={use_upper_bound}")
    print(f"[eval] 配置: {'评估上限(纯 bge + reranker)' if use_upper_bound else '生产同步(bm25+向量 RRF,无 reranker)'}")

    # 初始化 RAG（轻量：和 gr.App 一样）
    mgr = VectorStoreManager(persist_directory=vector_db_dir)
    mgr.load_vectorstore()
    # 2026-10-08 改:默认配置跟生产 rag_engine.py 对齐(关 reranker + 开 BM25 + 8/8 双路)。
    # 评估上限模式(纯 bge + BGE-reranker 启用)用 --upper-bound 触发
    retriever = create_retriever(
        mgr.vectorstore,
        documents=mgr._documents,
        top_k=4,
        use_bm25=not use_upper_bound,             # 生产开 BM25,评估上限关
        use_reranker=use_upper_bound,             # 评估上限启用重排器
        reranker_model='BAAI/bge-reranker-v2-m3' if use_upper_bound else None,
        vector_top_k=5 if use_upper_bound else 8, # 上限模式 5,生产 8
        bm25_top_k=5 if use_upper_bound else 8,
        use_hyde=use_hyde,
        hyde_llm=None,
    )
    qa = create_qa_system(retriever)
    # 如果启用 HyDE，把 LLM 注入 retriever
    if use_hyde:
        retriever.hyde_llm = qa.llm
    cm = create_chat_manager(qa.llm, retriever, use_simple=use_simple)

    eval_set = load_eval_set(DEFAULT_SET)
    case_results = []
    latencies = []
    for case in eval_set:
        t0 = time.time()
        # 走 chat_manager 完整链路：query rewriting + intent route + RAG + LLM
        result = cm.chat(case["question"])
        answer = result.get("answer", "")
        # retriever 直接拿 docs（不依赖 cm 的 processed docs）
        docs = retriever.retrieve(case["question"])
        dt = time.time() - t0
        latencies.append(dt)

        sc = score_case(case, answer, docs)
        case_results.append({
            **{k: v for k, v in case.items() if k != "note"},
            **sc,
            "answer": answer.strip()[:200] + ("..." if len(answer) > 200 else ""),
            "intent": result.get("intent"),
            "sources": [s.get("source", "?") for s in (result.get("sources") or [])][:2],
            "latency_sec": round(dt, 2),
        })

    n = len(eval_set)
    passed = sum(1 for c in case_results if c["passed"])
    summary = {
        "total": n,
        "passed": passed,
        "pass_rate": round(passed / n, 4) if n else 0,
        "recall_at_4_rate": round(sum(1 for c in case_results if c["recall@4"]) / n, 4) if n else 0,
        "answer_hit_rate": round(sum(1 for c in case_results if c["answer_hit"]) / n, 4) if n else 0,
        "banned_violations": sum(1 for c in case_results if c["banned_hit"]),
        "avg_precision": round(sum(c["precision"] for c in case_results) / n, 4) if n else 0,
        "latency_p50_sec": round(sorted(latencies)[len(latencies) // 2], 2) if latencies else 0,
        "latency_p90_sec": round(sorted(latencies)[int(len(latencies) * 0.9)], 2) if latencies else 0,
        "latency_avg_sec": round(sum(latencies) / n, 2) if n else 0,
    }

    return {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "vector_db_dir": vector_db_dir,
        "summary": summary,
        "cases": case_results,
    }


# ─────────────────────────── 入口 ───────────────────────────
def main():
    ap = argparse.ArgumentParser(description="RAG 评估 runner")
    ap.add_argument("--save-baseline", action="store_true", help="首次：存为 baseline")
    ap.add_argument("--html", metavar="PATH", help="生成 HTML 报告")
    ap.add_argument("--vs", metavar="PATH", default=str(BASELINE_PATH),
                    help="与哪个 JSON 对比（默认 baseline）")
    ap.add_argument("--vector-db", default="data/faiss_db_bge")
    ap.add_argument("--use-hyde", action="store_true",
                    help="启用 HyDE 召回增强（2026-09-17 新加）")
    ap.add_argument("--upper-bound", action="store_true",
                    help="2026-10-08 加：评估上限模式（纯 bge + 启用 BGE-reranker + 5/5 双路），"
                         "用于看 pass_rate 上限，与生产同步配置 (BM25+向量 8/8 关 reranker) 对比")
    ap.add_argument("--label", default="current",
                    help="输出 JSON 后缀（默认 current）")
    args = ap.parse_args()

    result = run_eval(vector_db_dir=args.vector_db, use_hyde=args.use_hyde,
                      use_upper_bound=args.upper_bound)
    target = BASELINE_PATH if args.save_baseline else Path(str(CURRENT_PATH).replace(".json", f"_{args.label}.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    s = result["summary"]
    print(f"[eval] saved to {target}")
    print(
        f"[eval] summary: total={s['total']} passed={s['passed']} "
        f"pass_rate={s['pass_rate']*100:.1f}% "
        f"recall@4={s['recall_at_4_rate']*100:.1f}% "
        f"answer_hit={s['answer_hit_rate']*100:.1f}% "
        f"avg_prec={s['avg_precision']:.2f} "
        f"latency_p50={s['latency_p50_sec']}s "
        f"banned_violations={s['banned_violations']}"
    )

    # 与 baseline 对比
    if not args.save_baseline and Path(args.vs).exists():
        baseline = json.load(open(args.vs, "r", encoding="utf-8"))
        bs = baseline["summary"]
        cs = result["summary"]
        print("\n[eval] vs baseline 对比:")
        for k in ["pass_rate", "recall_at_4_rate", "answer_hit_rate",
                 "avg_precision", "latency_p50_sec", "banned_violations"]:
            b = bs.get(k, 0)
            c = cs.get(k, 0)
            delta = c - b
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
            print(f"  {k:20s}  {b:.4f} → {c:.4f}   {arrow} {delta:+.4f}")

    # HTML 报告
    if args.html:
        try:
            from scripts.eval_report_html import render_html
        except ImportError:
            sys.path.insert(0, str(Path(__file__).parent))
            from eval_report_html import render_html
        baseline = json.load(open(args.vs, "r", encoding="utf-8")) if Path(args.vs).exists() else None
        render_html(baseline=baseline, current=result, out_path=args.html)
        print(f"[eval] HTML report: {args.html}")


if __name__ == "__main__":
    main()
