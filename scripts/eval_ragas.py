# -*- coding: utf-8 -*-
"""
RAGAS 评估脚本（2026-10-11 新增）

────────────────────────────────────────────────────────────────────
📚 本文件讲什么
────────────────────────────────────────────────────────────────────
用 RAGAS（专业 RAG 评估框架）替代现有 eval_set.json 的"关键字匹配"评分，
跑工业级 4 个核心指标：

    ┌─────────────────────┬──────────────────────────────────────────┐
    │ 指标                │ 含义                                     │
    ├─────────────────────┼──────────────────────────────────────────┤
    │ faithfulness        │ 答案是否忠于 context（低 = LLM 瞎编）    │
    │ answer_relevancy    │ 答案是否切题（低 = 答非所问）            │
    │ context_precision   │ 召回的文档是不是真的相关（高 = 不浪费）  │
    │ context_recall      │ 标准答案的信息是否都在召回里（高=召回全）│
    └─────────────────────┴──────────────────────────────────────────┘

────────────────────────────────────────────────────────────────────
🔧 跟现有 eval_runner.py 的关系
────────────────────────────────────────────────────────────────────
两者并存不冲突，跑分互补：
    - eval_runner.py：60 题全跑、keyword match、快（分钟级）
    - eval_ragas.py ：子集跑（默认前 15 题）、LLM-as-judge、准（20-30 分钟）

为什么不全 60 题跑 RAGAS？
    - RAGAS 内部每个指标都要调一次 LLM 当"裁判"（faithfulness/answer_relevancy）
    - 60 题 × 4 指标 × 2-3 次 LLM = 720+ 次 API 调用，单次要 20-30 元
    - 子集评估够看出"方法 A vs B 哪个优"

────────────────────────────────────────────────────────────────────
💡 关键设计
────────────────────────────────────────────────────────────────────
1. 用 generate_context 测试模式：复用 retriever 真召回
2. ground_truth 从 eval_set.json 的 expected_keywords 拼接
3. 用 LLM 当裁判（默认 DeepSeek，跟你 .env 一致）
4. 输出 DataFrame + CSV + JSON，肉眼对比

────────────────────────────────────────────────────────────────────
⚠️ 注意
────────────────────────────────────────────────────────────────────
- RAGAS 0.1+ API 跟旧版不同，本文件用 ragas>=0.1.0 兼容接口
- 如果只装了 ragas但没装 datasets，先 `pip install datasets`
────────────────────────────────────────────────────────────────────

用法：
    python scripts/eval_ragas.py --limit 15 --label v7_ragas
    python scripts/eval_ragas.py --limit 15 --label v8_ragas --save-baseline
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_SET = PROJECT_ROOT / "tests" / "eval_set.json"
OUTPUT_DIR = PROJECT_ROOT / "tests" / "ragas"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 1) 把现有 eval_set.json 转成 RAGAS 要求的 dataset 格式
# ============================================================
def build_ragas_dataset(eval_set: list, limit: int = 15) -> dict:
    """
    把项目自己的 60 题评测集，转成 RAGAS 能吃的格式。

    输入：
        eval_set = [{"id": 1, "question": "...", "expected_keywords": [...],
                     "banned_keywords": [...], "category": "...", ...}, ...]

    输出（RAGAS 要求至少 4 个字段）：
        {
          "question": ["Q1", "Q2", ...],
          "answer":   ["A1", "A2", ...],     # 由 RAG 生成（下面调）
          "contexts": [["ctx1", "ctx2"], ...], # 由 retriever 召回
          "ground_truth": ["真值1", "真值2", ...]  # expected_keywords 用空格拼
        }
    """
    questions = []
    ground_truths = []

    for case in eval_set[:limit]:
        questions.append(case["question"])
        # ground_truth 用 expected_keywords 拼成一句话
        # （RAGAS 要求字符串形式："奖学金 评定 学年"）
        gt = " ".join(case.get("expected_keywords") or [""])
        # 或者直接拼成 "标准答案：奖学金；评定；学年"
        if gt.strip():
            gt = f"标准答案关键词：{gt}"
        else:
            gt = case.get("note", "无明确标准答案")[:100]
        ground_truths.append(gt)

    return {
        "question": questions,
        "answer": [],       # 等会儿用 RAG 跑出来再填
        "contexts": [],     # 等会儿用 retriever 召回再填
        "ground_truth": ground_truths,
    }


# ============================================================
# 2) 用现有 RAG 链路给每题生成 answer + contexts
# ============================================================
def generate_predictions(questions: list, use_langgraph: bool = False) -> tuple:
    """
    跑"完整 RAG 链路"（含 retriever + LLM）给每题拿到 answer 和 contexts。

    Args:
        questions: 问题列表
        use_langgraph: True → 用 langgraph 版 Agent；False → 用现有 react_engine

    Returns:
        (answers, contexts_list)
        - answers: ["A1", "A2", ...]
        - contexts_list: [[c1, c2, ...], [c1, c2, ...], ...] 嵌套 list
    """
    # 2.1 初始化 RAG
    from src.rag.vector_store import VectorStoreManager
    from src.rag.retriever import create_retriever
    from src.rag.llm_chain import create_qa_system

    print(f"[ragas] 加载向量库...")
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
    )
    qa = create_qa_system(retriever)

    # 2.2 逐题跑
    answers = []
    contexts_list = []
    print(f"[ragas] 跑 {len(questions)} 题（用现有 RAG 链路）...")
    t0 = time.time()
    for i, q in enumerate(questions, 1):
        # 召回
        docs = retriever.retrieve(q)
        contexts_list.append([d.page_content for d in docs])

        # 生成答案
        result = qa.answer(q, return_sources=False)
        answers.append(result.get("answer", ""))

        # 进度
        if i % 5 == 0:
            dt = time.time() - t0
            print(f"  [{i}/{len(questions)}] 平均 {dt / i:.1f}s/题")

    print(f"[ragas] 跑完，耗时 {time.time() - t0:.1f}s")
    return answers, contexts_list


# ============================================================
# 3) 跑 RAGAS 4 个核心指标
# ============================================================
def run_ragas(dataset_dict: dict) -> dict:
    """
    用 RAGAS 评估 4 个指标。

    关键 API（ragas>=0.1.0）：
        from ragas.metrics import faithfulness, answer_relevancy, ...
        from datasets import Dataset
        ds = Dataset.from_dict(dataset_dict)
        result = evaluate(ds, metrics=[faithfulness, answer_relevancy, ...])
        # result 有 .to_pandas() 方法
    """
    print(f"[ragas] 启动 RAGAS 评估（每个指标会让 LLM 当裁判打分）...")

    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import (
            faithfulness,
            answer_relevancy,
            context_precision,
            context_recall,
        )
    except ImportError as e:
        print(f"❌ 缺依赖: {e}")
        print(f"   pip install ragas datasets")
        return {"error": str(e)}

    # 3.1 构造 Dataset
    ds = Dataset.from_dict(dataset_dict)

    # 3.2 让 RAGAS 用 DeepSeek 当 LLM（默认会去抓 OPENAI_API_KEY，要覆盖）
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    judge_llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        temperature=0.0,   # 裁判要稳定，不要随机
    )
    # 3.3 RAGAS 的 faithfulness 还需要 embeddings 来比对 answer vs context
    try:
        from langchain_ollama import OllamaEmbeddings
        # bge-m3 是本地 Ollama 的，直接用
        judge_embeddings = OllamaEmbeddings(model="bge-m3")
    except Exception as e:
        print(f"[ragas] ⚠️ OllamaEmbeddings 不可用: {e!r}，faithfulness 评分会受影响")
        judge_embeddings = None

    # 3.4 跑评估
    print(f"[ragas] 4 个指标：faithfulness / answer_relevancy / context_precision / context_recall")
    try:
        if judge_embeddings:
            result = evaluate(
                ds,
                metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
                llm=judge_llm,
                embeddings=judge_embeddings,
            )
        else:
            result = evaluate(
                ds,
                metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
                llm=judge_llm,
            )
    except Exception as e:
        print(f"❌ RAGAS evaluate 失败: {e}")
        import traceback; traceback.print_exc()
        return {"error": str(e)}

    # 3.5 转成 dict（result 是 EvaluationResult）
    df = result.to_pandas()
    print(f"\n[ragas] 4 个指标均值：")
    summary = {}
    for metric_name in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
        if metric_name in df.columns:
            mean = float(df[metric_name].mean())
            summary[metric_name] = round(mean, 4)
            print(f"  {metric_name:20s}: {mean:.4f}")

    return {
        "summary": summary,
        "per_question": df.to_dict(orient="records"),
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ============================================================
# 4) 主入口
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="RAGAS RAG 评估")
    ap.add_argument("--limit", type=int, default=15, help="跑前 N 题（默认 15）")
    ap.add_argument("--label", default="v1_ragas", help="输出 JSON 后缀")
    ap.add_argument("--save-baseline", action="store_true", help="存为 baseline")
    ap.add_argument("--set", type=str, default=str(DEFAULT_SET), help="测试集路径")
    args = ap.parse_args()

    # 4.1 加载测试集
    with open(args.set, "r", encoding="utf-8") as f:
        eval_set = json.load(f)
    print(f"[ragas] 加载 {len(eval_set)} 题，跑前 {args.limit} 题")

    # 4.2 构造 RAGAS 格式
    dataset_dict = build_ragas_dataset(eval_set, limit=args.limit)

    # 4.3 用现有 RAG 跑 predictions
    answers, contexts_list = generate_predictions(
        dataset_dict["question"],
        use_langgraph=False,  # 默认用现有 react_engine；切 True 改用 LangGraph 版
    )
    dataset_dict["answer"] = answers
    dataset_dict["contexts"] = contexts_list

    # 4.4 跑 RAGAS 评估
    result = run_ragas(dataset_dict)

    # 4.5 保存
    if "error" not in result:
        out_path = OUTPUT_DIR / f"ragas_{args.label}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n[ragas] 写到 {out_path}")

        # 跟 baseline 对比
        baseline_path = OUTPUT_DIR / "ragas_baseline.json"
        if not args.save_baseline and baseline_path.exists():
            with open(baseline_path, "r", encoding="utf-8") as f:
                baseline = json.load(f)
            print(f"\n[ragas] 与 baseline 对比:")
            for k, v in result["summary"].items():
                bv = baseline["summary"].get(k, 0)
                delta = v - bv
                arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
                print(f"  {k:20s}  baseline={bv:.4f}  current={v:.4f}  {arrow} {delta:+.4f}")

        elif args.save_baseline:
            import shutil
            shutil.copy(out_path, baseline_path)
            print(f"[ragas] 已存为 baseline: {baseline_path}")
    else:
        print(f"\n❌ RAGAS 评估失败: {result['error']}")


if __name__ == "__main__":
    main()
