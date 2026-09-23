"""
RAG 引擎（2026-09-22 抽离自 src/web_app/app.py）

背景：
    原 src/web_app/app.py 里的 RAGApp 类同时承担了 RAG 引擎 + Gradio UI 两件事。
    FastAPI 版（src/web_app/fastapi_app.py）不需要 Gradio，但 dispatcher 需要调 RAG 核心。
    直接 `import src.web_app.app` 会触发 `import gradio`，启动慢 + 占内存 ~1 GB。
    所以把 RAG 核心（vectorstore / retriever / qa_system / initialize）剥离到这里。

调用：
    from src.rag.rag_engine import RAGEngine
    eng = RAGEngine()
    if eng.initialize():
        result = eng.qa_system.answer(query)
"""
import logging
from typing import Optional

from src.rag.vector_store import VectorStoreManager
from src.rag.retriever import create_retriever
from src.rag.llm_chain import create_qa_system

log = logging.getLogger("rag_engine")


class RAGEngine:
    """RAG 引擎（无 UI 依赖版，给 FastAPI dispatcher 用）"""
    _instance: Optional["RAGEngine"] = None

    def __init__(self):
        self.vectorstore = None
        self.retriever = None
        self.qa_system = None

    def initialize(self) -> bool:
        """加载向量库 → 建混合检索器 → 建问答链 → 注入 HyDE LLM"""
        log.info("RAG 引擎开始初始化...")
        manager = VectorStoreManager(persist_directory="data/faiss_db_bge")
        try:
            self.vectorstore = manager.load_vectorstore()
            log.info("向量库加载成功")
        except FileNotFoundError:
            log.error("向量库不存在，请先运行 build_vectorstore.py 构建")
            return False

        # 2026-09-18 修复循环依赖：retriever.hyde_llm 依赖 qa_system.llm，但 qa_system 依赖 retriever
        # 解决：先建 retriever（hyde_llm=None），再建 qa_system，最后注入 hyde_llm
        self.retriever = create_retriever(
            self.vectorstore,
            documents=manager._documents,
            top_k=4,
            score_threshold=0.0,
            use_bm25=True,
            use_reranker=True,
            reranker_model="BAAI/bge-reranker-v2-m3",  # 首次启动会下载 ~2.27 GB
            vector_top_k=8,    # 两路各召回 8，RRF 融合后 reranker 精排 8 → top4
            bm25_top_k=8,
            use_hyde=False,    # 先关闭，等 qa_system 建好再开
            hyde_llm=None,
        )
        log.info("混合检索器创建成功（BM25 + 向量 + RRF + BGE-reranker）")

        try:
            self.qa_system = create_qa_system(self.retriever)
            log.info("问答系统创建成功")
        except ValueError as e:
            log.error(f"LLM 初始化失败: {e}")
            return False

        # 现在 qa_system 已建好，注入 hyde_llm
        # HyDE（Hypothetical Document Embedding）= 让 LLM 先写"假设答案"再去搜，提升 recall@4
        self.retriever.use_hyde = True
        self.retriever.hyde_llm = self.qa_system.llm
        log.info("RAG 引擎初始化完成（HyDE 已注入）")
        return True


if __name__ == "__main__":
    # 自测：能加载向量库 + 建检索器 + 问答链
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    eng = RAGEngine()
    if eng.initialize():
        r = eng.qa_system.answer("奖学金怎么申请？")
        print("\n=== 答案 ===")
        print(r.get("answer", "（无）"))
        print(f"\n=== 来源 ({len(r.get('sources', []))} 条) ===")
        for s in r.get("sources", [])[:3]:
            print(f"  - {s.get('source', '?')}")
    else:
        print("❌ 初始化失败")