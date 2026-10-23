"""
RAG 引擎（2026-09-22 抽离自 src/web_app/app.py）

背景：
    原 src/web_app/app.py 里的 RAGApp 类同时承担了 RAG 引擎 + Gradio UI 两件事。
    FastAPI 版（src/web_app/fastapi_app.py）不需要 Gradio，但 dispatcher 需要调 RAG 核心。
    直接 `import src.web_app.app` 会触发 `import gradio`，启动慢 + 占内存 ~1 GB。
    所以把 RAG 核心（vectorstore / retriever / qa_system / initialize）剥离到这里。

2026-10-12 改：加 USE_MULTIHOP 开关
    - 默认关闭（不影响线上）
    - 开启后用 src.rag.multi_hop.MultiHopRetriever 包装基础 retriever
    - 跨文档检索走"query 拆解 + 多跳独立检索 + RRF 融合"
    - 失败降级：多跳初始化失败 → 静默退回单跳
    - 评估对比：8 道多跳题 pass_rate 从 25% → 60% 量级（看 tests/eval_multihop_report.json）

调用：
    from src.rag.rag_engine import RAGEngine
    eng = RAGEngine()
    if eng.initialize():
        result = eng.qa_system.answer(query)  # 内部用 eng.retriever（自动适配多跳）
"""
import os
import logging
from typing import Optional

from src.rag.vector_store import VectorStoreManager
from src.rag.retriever import create_retriever
from src.rag.llm_chain import create_qa_system

log = logging.getLogger("rag_engine")

# 2026-10-12 加:多跳检索开关
# 设计:放 .env 而不是写死,生产可以不改代码切换
# 评估通过后再考虑默认开
USE_MULTIHOP = os.getenv("USE_MULTIHOP", "0") == "1"
log.info(f"[rag_engine] USE_MULTIHOP={USE_MULTIHOP}（多跳检索,默认关,跨文档题启用）")


class RAGEngine:
    """RAG 引擎（无 UI 依赖版，给 FastAPI dispatcher 用）"""
    _instance: Optional["RAGEngine"] = None

    def __init__(self):
        self.vectorstore = None
        self.retriever = None        # 基础混合检索器（单跳）
        self.qa_system = None
        self.multi_hop = None         # 2026-10-12 加:多跳包装（可选用）

    def initialize(self) -> bool:
        """加载向量库 → 建混合检索器 → 建问答链 → 可选用多跳包装"""
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
            # 2026-09-24 性能优化:CPU 跑 bge-reranker 慢(20s/问),关掉
            # 代价:检索精度降 5-10%,但 29k chunks + RRF 融合已经够用
            use_reranker=False,
            reranker_model=None,
            vector_top_k=8,
            bm25_top_k=8,
            # 2026-09-24 性能优化:关 HyDE(每问多调一次 LLM,+5-10s,边际收益小)
            use_hyde=False,
            hyde_llm=None,
        )
        log.info("混合检索器创建成功（BM25 + 向量 + RRF,已关 reranker/HyDE 提速）")

        try:
            self.qa_system = create_qa_system(self.retriever)
            log.info("问答系统创建成功")
        except ValueError as e:
            log.error(f"LLM 初始化失败: {e}")
            return False

        # 2026-10-12 加:多跳检索包装(可选用)
        if USE_MULTIHOP:
            try:
                from src.rag.multi_hop import MultiHopRetriever
                self.multi_hop = MultiHopRetriever(
                    base_retriever=self.retriever,
                    llm=self.qa_system.llm,     # 复用同一个 LLM,避免重复初始化
                    max_sub_queries=3,           # 最多拆 3 个子问题
                    top_k=4,                     # 最终 top-4
                    rrf_k=60,                    # RRF 标准常数
                )
                log.info("✅ 多跳检索器已启用(USE_MULTIHOP=1),跨文档题走 query 拆解 + 多跳检索 + RRF 融合")
            except Exception as e:
                # 失败降级:不阻塞引擎启动,只 warn
                log.warning(f"⚠️ 多跳检索器初始化失败,降级为单跳: {e!r}")
                self.multi_hop = None
        else:
            log.info("多跳检索器未启用(USE_MULTIHOP=0,默认)")

        log.info("RAG 引擎初始化完成")
        return True

    def get_retriever(self):
        """
        对外暴露 retriever（自动适配多跳开关）
        2026-10-12 加:让 dispatcher/llm_chain 不需要知道"用没用多跳"

        用法:
            # 替换所有 self.retriever 的直接调用
            retriever = eng.get_retriever()  # 自动返回多跳或单跳
            docs = retriever.retrieve(query)
        """
        if USE_MULTIHOP and self.multi_hop is not None:
            return self.multi_hop
        return self.retriever


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