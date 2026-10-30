"""
可断点续跑的评估器（2026-10-12 新增）
=================================================

# 设计动机
#   eval_runner.py 一次跑全部 case,LLM API 挂了前面全丢(惨痛教训:300 题跑了 50 分钟)
#   这个版本:每跑完一题追加一行 JSONL,挂了能从断点继续
#
# 用法:
#   python scripts/_eval_resumable.py                  # 跑全部 300 题
#   python scripts/_eval_resumable.py --limit 50       # 只跑 50 题
#   python scripts/_eval_resumable.py --show-summary   # 只看已跑完的汇总
#
# 输出:
#   tests/eval_300_partial.jsonl    # 每行一题的结果(JSONL 格式,断点用)
#   tests/eval_300_summary.json     # 最终汇总(写到控制台 + 文件)
"""
import os
import sys
import io
import json
import time
import argparse
import logging
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s: %(message)s')
log = logging.getLogger('eval_resumable')

EVAL_SET = PROJECT_ROOT / "tests" / "eval_set.json"
PARTIAL = PROJECT_ROOT / "tests" / "eval_300_partial.jsonl"
SUMMARY = PROJECT_ROOT / "tests" / "eval_300_summary.json"


# ============================================================
# 1) 加载已有结果(断点续跑)
# ============================================================
def load_partial() -> dict:
    """读已跑的 JSONL,返回 {case_id: result}"""
    if not PARTIAL.exists():
        return {}
    results = {}
    with open(PARTIAL, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                results[r['id']] = r
            except Exception:
                continue
    return results


def append_partial(result: dict):
    """追加一题结果到 JSONL"""
    with open(PARTIAL, 'a', encoding='utf-8') as f:
        f.write(json.dumps(result, ensure_ascii=False) + '\n')


# ============================================================
# 2) 单题评测
# ============================================================
def score_case(case: dict, answer: str, docs: list) -> dict:
    """和 eval_runner.py 完全一致的评分逻辑"""
    expected = case.get("expected_keywords") or []
    banned = case.get("banned_keywords") or []
    docs_text = "".join(
        ((d.metadata or {}).get("source", "") + "\n" + (d.page_content or ""))
        for d in docs[:4]
    )
    recall = all((kw in docs_text) for kw in expected) if expected else True
    answer_hit = all((kw in answer) for kw in expected) if expected else True
    if expected:
        hit = sum(1 for kw in expected if kw in docs_text)
        precision = hit / len(expected)
    else:
        precision = 1.0 if not banned else (0.0 if any((bk in answer) for bk in banned) else 1.0)
    banned_hit = any((bk in answer) for bk in banned)
    return {
        "recall@4": bool(recall),
        "precision": round(precision, 3),
        "answer_hit": bool(answer_hit),
        "banned_hit": bool(banned_hit),
        "passed": bool(recall and answer_hit and not banned_hit),
    }


# ============================================================
# 3) 主流程
# ============================================================
def run(limit: int = 0, retries: int = 3):
    """跑评估,断点续跑

    Args:
        limit: 限制跑几题(测试用)
        retries: 单题 LLM 失败重试次数
    """
    if not EVAL_SET.exists():
        print(f"!! 找不到 {EVAL_SET},请先跑生成脚本")
        return
    cases = json.load(open(EVAL_SET, encoding='utf-8'))
    print(f"[eval] 总题数: {len(cases)}")
    if limit > 0:
        cases = cases[:limit]
        print(f"[eval] 限制跑前 {limit} 题")

    # 加载已跑结果
    partial = load_partial()
    print(f"[eval] 已跑: {len(partial)} 题,待跑: {len(cases) - len(partial)} 题")

    # 初始化 RAG
    print("[eval] 加载 RAG 引擎...")
    from src.rag.vector_store import VectorStoreManager
    from src.rag.retriever import create_retriever
    from src.rag.llm_chain import create_qa_system
    from src.rag.chat_history import create_chat_manager

    mgr = VectorStoreManager(persist_directory="data/faiss_db_bge")
    mgr.load_vectorstore()
    retriever = create_retriever(
        mgr.vectorstore,
        documents=mgr._documents,
        top_k=4,
        use_bm25=True,
        use_reranker=False,
        vector_top_k=8,
        bm25_top_k=8,
        use_hyde=False,
    )
    qa = create_qa_system(retriever)
    cm = create_chat_manager(qa.llm, retriever, use_simple=True)  # simple 模式,无历史开销

    # 跑题
    skipped = 0
    failed = 0
    t_start = time.time()
    for i, case in enumerate(cases):
        if case['id'] in partial:
            skipped += 1
            continue

        # 重试机制
        for attempt in range(retries):
            try:
                t0 = time.time()
                result = cm.chat(case["question"])
                answer = result.get("answer", "")
                docs = retriever.retrieve(case["question"])
                dt = time.time() - t0

                sc = score_case(case, answer, docs)
                case_result = {
                    "id": case["id"],
                    "category": case.get("category", ""),
                    "difficulty": case.get("difficulty", ""),
                    "question": case["question"],
                    "expected_keywords": case.get("expected_keywords", []),
                    "banned_keywords": case.get("banned_keywords", []),
                    "answer": (answer or "")[:200],
                    "intent": result.get("intent"),
                    "latency_sec": round(dt, 2),
                    **sc,
                }
                append_partial(case_result)
                status = "PASS" if sc["passed"] else "FAIL"
                print(f"  [{i+1:>3d}/{len(cases)}] Q{case['id']:>3d} {status} {dt:5.1f}s  {case['question'][:30]}")
                break
            except Exception as e:
                log.warning(f"  Q{case['id']} 第 {attempt+1} 次重试失败: {type(e).__name__}: {str(e)[:80]}")
                if attempt < retries - 1:
                    time.sleep(5 * (attempt + 1))  # 退避
                else:
                    # 重试用完,记 error
                    failed += 1
                    err_result = {
                        "id": case["id"],
                        "category": case.get("category", ""),
                        "difficulty": case.get("difficulty", ""),
                        "question": case["question"],
                        "error": f"{type(e).__name__}: {str(e)[:200]}",
                        "passed": False,
                        "recall@4": False,
                        "answer_hit": False,
                        "banned_hit": False,
                        "precision": 0.0,
                    }
                    append_partial(err_result)
                    print(f"  [{i+1:>3d}/{len(cases)}] Q{case['id']:>3d} ERROR {type(e).__name__}")

    # 汇总
    elapsed = time.time() - t_start
    print(f"\n[eval] 本次跳过 {skipped} 题,新增 {len(cases) - skipped} 题,失败 {failed} 题,耗时 {elapsed:.0f}s")
    summarize()


def summarize():
    """汇总所有 partial 结果"""
    partial = load_partial()
    if not partial:
        print("[summary] 没有结果")
        return
    n = len(partial)
    passed = sum(1 for r in partial.values() if r.get("passed"))
    summary = {
        "total": n,
        "passed": passed,
        "pass_rate": round(passed / n, 4) if n else 0,
        "recall_at_4_rate": round(sum(1 for r in partial.values() if r.get("recall@4")) / n, 4) if n else 0,
        "answer_hit_rate": round(sum(1 for r in partial.values() if r.get("answer_hit")) / n, 4) if n else 0,
        "banned_violations": sum(1 for r in partial.values() if r.get("banned_hit")),
        "errors": sum(1 for r in partial.values() if r.get("error")),
        "avg_precision": round(sum(r.get("precision", 0) for r in partial.values()) / n, 4) if n else 0,
    }
    latencies = [r["latency_sec"] for r in partial.values() if "latency_sec" in r]
    if latencies:
        latencies.sort()
        summary["latency_p50_sec"] = round(latencies[len(latencies) // 2], 2)
        summary["latency_p90_sec"] = round(latencies[int(len(latencies) * 0.9)], 2)
        summary["latency_avg_sec"] = round(sum(latencies) / len(latencies), 2)

    # 分类分项
    cat_stats = {}
    for r in partial.values():
        cat = r.get("category", "unknown")
        if cat not in cat_stats:
            cat_stats[cat] = {"total": 0, "passed": 0}
        cat_stats[cat]["total"] += 1
        if r.get("passed"):
            cat_stats[cat]["passed"] += 1
    summary["by_category"] = {k: {**v, "pass_rate": round(v["passed"] / v["total"], 4)} for k, v in cat_stats.items()}

    # 输出
    print("\n" + "=" * 60)
    print("📊 300 题评估汇总")
    print("=" * 60)
    print(f"  总题数:     {summary['total']}")
    print(f"  通过:       {summary['passed']}")
    print(f"  pass_rate:  {summary['pass_rate']*100:.1f}%")
    print(f"  recall@4:   {summary['recall_at_4_rate']*100:.1f}%")
    print(f"  answer_hit: {summary['answer_hit_rate']*100:.1f}%")
    print(f"  banned_violations: {summary['banned_violations']}")
    print(f"  errors: {summary['errors']}")
    print(f"  avg_precision: {summary['avg_precision']:.3f}")
    if "latency_p50_sec" in summary:
        print(f"  p50 延迟:   {summary['latency_p50_sec']}s")
        print(f"  p90 延迟:   {summary['latency_p90_sec']}s")
        print(f"  avg 延迟:   {summary['latency_avg_sec']}s")

    print("\n  按分类:")
    for cat, s in sorted(summary["by_category"].items(), key=lambda x: -x[1]["total"]):
        print(f"    {s['total']:>3d}  {s['passed']:>3d}  {s['pass_rate']*100:5.1f}%  {cat}")

    # 写文件
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n  汇总写入: {SUMMARY}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题(测试用)")
    ap.add_argument("--show-summary", action="store_true", help="只看已跑汇总")
    ap.add_argument("--retries", type=int, default=3, help="单题重试次数")
    args = ap.parse_args()

    if args.show_summary:
        summarize()
    else:
        run(limit=args.limit, retries=args.retries)
