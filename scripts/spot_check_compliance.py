# -*- coding: utf-8 -*-
"""
合规过滤 spot-check v3（2026-09-21）
用 sys.stdout.flush + unbuffered 调用 retriever，验证 _is_exam_doc() 在实际检索中是否生效。
不启用 reranker（避免推理慢），不调用 LLM。
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# unbuffered 输出
sys.stdout.reconfigure(line_buffering=True)

from src.rag.vector_store import VectorStoreManager
from src.rag.retriever import create_retriever


def main():
    print("=" * 60, flush=True)
    print("合规过滤 spot-check v3（实际 retriever）", flush=True)
    print("=" * 60, flush=True)

    vs_dir = PROJECT_ROOT / "data" / "faiss_db_bge"
    mgr = VectorStoreManager(persist_directory=str(vs_dir))
    mgr.load_vectorstore()
    print("[load] 29377 docs loaded", flush=True)

    # 不启用 reranker（避免 5s/题）
    retriever = create_retriever(
        mgr.vectorstore,
        documents=mgr._documents,
        top_k=4,
        use_bm25=False,
        use_reranker=False,
        vector_top_k=8,
        bm25_top_k=8,
        use_hyde=False,
        hyde_llm=None,
    )

    cases = [
        ("试卷-应过滤", "信号与系统 A 卷答案"),
        ("试卷-应过滤", "复变函数期末试卷"),
        ("元信息-应保留", "信号与系统什么时候考试"),
        ("普通-应保留", "图书馆开放时间"),
    ]

    for case_type, question in cases:
        print(f"\n[{case_type}] Q: {question}", flush=True)
        docs = retriever.retrieve(question)
        if not docs:
            print(f"  -> 召回 0 篇 (合规)", flush=True)
            continue
        print(f"  -> 召回 {len(docs)} 篇:", flush=True)
        for i, d in enumerate(docs[:4], 1):
            src = d.metadata.get("source", "?") or d.metadata.get("file_name", "?")
            try:
                src_display = src[:80]
            except Exception:
                src_display = "?"
            snippet = (d.page_content or "")[:60].replace("\n", " ")
            print(f"    [{i}] {src_display}", flush=True)
            print(f"        {snippet}...", flush=True)


if __name__ == "__main__":
    main()