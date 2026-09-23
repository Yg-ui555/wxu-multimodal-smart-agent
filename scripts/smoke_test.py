# -*- coding: utf-8 -*-
"""
venv311 + 依赖 + 本地模型 + 向量库 一站式冒烟测试（smoke test）
================================================================
用途：每次换电脑/重装环境后，跑这一个脚本就能知道整条 RAG 链路是否还活着。

覆盖范围：
  [1/4] 关键库 import（cv2/paddleocr/gradio/faiss/bm25/jieba/sentence-transformers）
  [2/4] Ollama 服务 + bge-m3 嵌入 + bge-reranker 模型在线
  [3/4] FAISS 向量库加载（确认 29k chunks 索引可读）
  [4/4] 完整 RAG 检索链路：query → bge-m3 embedding → FAISS top-5 → reranker → top-4

注意：
  - 不调 LLM（避免依赖 .env 里的 LLM_API_KEY + 节省时间）
  - 不重建向量库（只测加载）
  - 跑通 ≈ web_app + eval_runner.py 一定能跑（除了 LLM 那一跳）

退出码：0 = 全通；非 0 = 有 FAIL 项

用法：
    python scripts/smoke_test.py                  # 跑全 4 步
    python scripts/smoke_test.py --skip-rerank   # 跳过重排（首次下载 BGE 重排模型太慢时）
    python scripts/smoke_test.py --quick          # 只跑 [1][2]（imports + Ollama），最快
"""
import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ──────────────────────────────────────
# 测试结果统计
# ──────────────────────────────────────
RESULTS = {"OK": 0, "FAIL": 0, "FAIL_LIST": []}


def _check(name: str, fn_, *, detail: bool = True):
    """执行 fn_，捕获异常并统计 OK/FAIL"""
    try:
        result = fn_()
        print(f"  [OK]   {name}")
        if detail and result is not None:
            print(f"        -> {result}")
        RESULTS["OK"] += 1
        return result
    except Exception as e:
        print(f"  [FAIL] {name}  {type(e).__name__}: {e}")
        RESULTS["FAIL"] += 1
        RESULTS["FAIL_LIST"].append((name, f"{type(e).__name__}: {e}"))
        return None


# ──────────────────────────────────────
# [1/4] 关键库 import
# ──────────────────────────────────────
#
# ⚠️ import 顺序很重要！
# sentence_transformers 通过 transformers 间接 import torch，依赖 shm.dll。
# paddleocr 在 import 时会把 site-packages/paddleocr 推到 sys.path 第 0 位，
# 污染 torch 的 DLL 搜索路径，触发 WinError 127。
# 解决办法：sentence_transformers 必须在 paddleocr 之前 import。
# （2026-09-21 smoke_test 实测发现）
def step_imports():
    """检查 venv311 里关键库都能 import"""
    import_order = [
        # 先 torch 链路（避免被 paddleocr 污染 DLL 路径）
        "sentence_transformers",   # sentence-transformers（reranker），依赖 torch
        "faiss",                   # FAISS
        "rank_bm25",               # BM25
        "jieba",                   # jieba 中文分词
        # 再 paddleocr 链路
        "cv2",                     # OpenCV
        "paddleocr",               # PaddleOCR（会改 sys.path，必须放后面）
        "gradio",                  # Gradio
        # 工具
        "requests",                # HTTP（Ollama 调用）
        "pymupdf",                 # PyMuPDF（fitz）
        "docx",                    # python-docx
        "dotenv",                  # python-dotenv
    ]
    versions = {}
    for m in import_order:
        try:
            mod = __import__(m)
            versions[m] = getattr(mod, "__version__", "?")
        except ImportError:
            raise RuntimeError(f"缺库: {m}（pip install -r requirements.txt）")
    return versions


# ──────────────────────────────────────
# [2/4] Ollama 服务 + 本地模型
# ──────────────────────────────────────
def step_ollama():
    """检查 Ollama 服务 + bge-m3 嵌入 + bge-reranker 模型在线"""
    import requests
    r = requests.get("http://localhost:11434/api/tags", timeout=5)
    r.raise_for_status()
    models = [m["name"] for m in r.json().get("models", [])]
    required = ["bge-m3:latest", "qllama/bge-reranker-v2-m3:latest"]
    missing = [m for m in required if m not in models]
    if missing:
        raise RuntimeError(
            f"Ollama 缺模型: {missing}\n"
            f"        拉取: ollama pull {' '.join(m.replace(':latest','') for m in missing)}"
        )
    return f"已装 {len(models)} 个模型（含 bge-m3 + reranker）"


# ──────────────────────────────────────
# [3/4] FAISS 向量库加载
# ──────────────────────────────────────
def step_vectorstore():
    """加载 FAISS 向量库，确认 chunks 数量"""
    from src.rag.vector_store import VectorStoreManager
    vs_dir = PROJECT_ROOT / "data" / "faiss_db_bge"
    mgr = VectorStoreManager(persist_directory=str(vs_dir))
    mgr.load_vectorstore()
    if mgr.vectorstore is None:
        raise RuntimeError(f"FAISS 向量库加载失败（{vs_dir} 目录？）")
    docs = mgr._documents or []
    return f"chunks={len(docs)}（index.faiss={(vs_dir / 'index.faiss').stat().st_size // 1024 // 1024}MB）"


# ──────────────────────────────────────
# [4/4] RAG 检索链路（含 reranker）
# ──────────────────────────────────────
def step_retrieve(skip_rerank: bool = False):
    """跑一道题：query → embedding → FAISS top-5 → reranker → top-4"""
    from src.rag.vector_store import VectorStoreManager
    from src.rag.retriever import create_retriever

    vs_dir = PROJECT_ROOT / "data" / "faiss_db_bge"
    mgr = VectorStoreManager(persist_directory=str(vs_dir))
    mgr.load_vectorstore()

    retriever = create_retriever(
        mgr.vectorstore,
        documents=mgr._documents,
        top_k=4,
        use_bm25=False,
        use_reranker=not skip_rerank,
        reranker_model='BAAI/bge-reranker-v2-m3',  # HF 模型名（已预下载）
        vector_top_k=5,
        bm25_top_k=5,
        use_hyde=False,
        hyde_llm=None,
    )

    question = "2024-2025 学年什么时候开学？"
    t0 = time.time()
    docs = retriever.retrieve(question)
    dt = time.time() - t0

    if not docs:
        raise RuntimeError("retrieve 返回空（FAISS 索引可能损坏）")
    if not docs[0].page_content.strip():
        raise RuntimeError("top-1 doc 内容为空（documents.pkl 可能没加载）")

    top_sources = [d.metadata.get("source", "?") for d in docs[:4]]
    snippet = docs[0].page_content[:60].replace("\n", " ")
    return (
        f"latency={dt:.2f}s  top1={Path(top_sources[0]).name if top_sources[0] else '?'}  "
        f"snippet='{snippet}...'"
    )


# ──────────────────────────────────────
# 主流程
# ──────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="venv311 + RAG 环境冒烟测试")
    ap.add_argument("--skip-rerank", action="store_true",
                    help="跳过 BGE reranker（首次下载 HF 模型太慢时用）")
    ap.add_argument("--quick", action="store_true",
                    help="只跑 [1] import + [2] Ollama（最快，2 分钟确认环境）")
    args = ap.parse_args()

    print("=" * 60)
    print(" wxu-multimodal-smart-agent smoke test")
    print("=" * 60)

    print("\n[1/4] 关键库 import")
    _check("imports", step_imports)

    print("\n[2/4] Ollama + 本地模型")
    _check("ollama_models", step_ollama)

    if args.quick:
        # --quick 模式只跑这两步
        _print_summary()
        return 0 if RESULTS["FAIL"] == 0 else 1

    print("\n[3/4] FAISS 向量库加载")
    _check("vectorstore", step_vectorstore)

    print("\n[4/4] RAG 检索链路（embedding + FAISS + reranker）")
    _check("retrieve", step_retrieve, detail=False)
    rerank_label = "（已跳过 reranker）" if args.skip_rerank else "（含 reranker）"
    print(f"        -> {rerank_label}")

    _print_summary()
    return 0 if RESULTS["FAIL"] == 0 else 1


def _print_summary():
    print("\n" + "=" * 60)
    print(f" Result: {RESULTS['OK']} OK, {RESULTS['FAIL']} FAIL")
    if RESULTS["FAIL_LIST"]:
        print("\n 失败项:")
        for name, err in RESULTS["FAIL_LIST"]:
            print(f"   - {name}: {err}")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())