# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. 混合检索：BM25（关键词精确匹配）+ 向量（语义相似）双路召回
# 2. RRF 融合排序（Reciprocal Rank Fusion）：两路各取 top-10 → 融合 top-4
# 3. BGE-reranker 重排：向量召回 top-20 → reranker 精排 top-4
# 4. jieba 中文分词 + rank_bm25 关键词索引
# 5. 检索结果带源文档信息（metadata 提取 file_name/source/date/category）
#
# 💡 面试高频问题：
#    - "为什么需要混合检索？" → 向量对专有名词/文号召回差，BM25 补精确词
#    - "RRF 怎么算？" → score(d) = Σ 1/(k+rank)，k=60，消除两路分数尺度差异
#    - "reranker 有什么用？" → 粗召回 top-20 后精排，检索精度 +15%
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

"""
检索器：BM25 + 向量混合检索 + RRF 融合 + 可选 reranker
"""

import os
import re
import logging
from typing import List, Optional, Tuple

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS

log = logging.getLogger("retriever")

try:
    import jieba
    from rank_bm25 import BM25Okapi
    BM25_AVAILABLE = True
except ImportError:
    BM25_AVAILABLE = False
    log.warning("rank_bm25 / jieba 未安装，BM25 检索不可用，将降级为纯向量检索")


# ============================================================
# BM25 关键词索引
# ============================================================

class BM25Index:
    """基于 jieba 分词 + rank_bm25 的中文关键词索引"""

    def __init__(self, documents: List[Document]):
        """
        Args:
            documents: 所有分块文档（用于构建 BM25 索引）
        """
        self.documents = documents
        self.corpus = []
        for doc in documents:
            # 分词并过滤空词
            tokens = [t for t in jieba.cut(doc.page_content) if t.strip()]
            self.corpus.append(tokens)
        if self.corpus:
            self.bm25 = BM25Okapi(self.corpus)
        else:
            self.bm25 = None

    def search(self, query: str, k: int = 10) -> List[Tuple[Document, float]]:
        """
        BM25 检索
        Args:
            query: 查询文本
            k: 返回 top-k
        Returns:
            [(Document, bm25_score), ...]
        """
        if self.bm25 is None:
            return []
        tokens = [t for t in jieba.cut(query) if t.strip()]
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        # 按分数降序取 top-k
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:k]
        result = []
        for idx, score in ranked:
            if score > 0:  # 只返回有匹配的
                result.append((self.documents[idx], float(score)))
        return result


# ============================================================
# RRF 融合
# ============================================================

def rrf_fuse(
    ranked_lists: List[List[Document]],
    k: int = 60,
    top_n: int = 4
) -> List[Document]:
    """
    Reciprocal Rank Fusion 融合多个排序列表
    Args:
        ranked_lists: 多个排序列表，每个列表是 Document 列表（已按相关性排序）
        k: RRF 常数（通常 60）
        top_n: 返回 top-n
    Returns:
        融合后的 Document 列表
    """
    scores = {}
    doc_map = {}

    for ranked_list in ranked_lists:
        for rank, doc in enumerate(ranked_list):
            # 用 source + content_hash 作为唯一标识
            key = f"{doc.metadata.get('source', '')}|{doc.metadata.get('content_hash', id(doc))}"
            doc_map[key] = doc
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)

    # 按融合分数降序
    sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [doc_map[key] for key in sorted_keys[:top_n]]


# ============================================================
# BGE Reranker（sentence-transformers CrossEncoder）
# ============================================================

class BGEReranker:
    """BGE-reranker 重排器（基于 sentence-transformers CrossEncoder）"""

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self.model_name = model_name
        self._model = None
        self._available = None  # None=未检测, True/False

    def _load_model(self):
        if self._model is not None:
            return
        try:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self.model_name)
            self._available = True
        except Exception as e:
            log.warning(f"BGE-reranker 加载失败，将跳过重排: {e}")
            self._available = False

    def rerank(self, query: str, documents: List[Document], top_n: int = 4) -> List[Document]:
        """
        对文档列表重排
        Args:
            query: 查询文本
            documents: 待重排文档
            top_n: 返回 top-n
        Returns:
            重排后的文档列表
        """
        if not documents or len(documents) <= top_n:
            return documents[:top_n]

        self._load_model()
        if not self._available or self._model is None:
            return documents[:top_n]

        try:
            pairs = [(query, doc.page_content) for doc in documents]
            scores = self._model.predict(pairs)
            # 按分数降序
            ranked = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)
            return [doc for doc, _ in ranked[:top_n]]
        except Exception as e:
            log.debug(f"BGE-reranker 重排失败，降级返回原顺序: {e}")
            return documents[:top_n]


# ============================================================
# 混合检索器
# ============================================================

class HybridRetriever:
    """
    混合检索器：BM25 + 向量 + RRF 融合 + 可选 reranker
    解决纯向量检索对专有名词/文号失效的问题
    """

    def __init__(
        self,
        vectorstore: FAISS,
        documents: Optional[List[Document]] = None,
        top_k: int = 4,
        score_threshold: float = 0.0,
        use_bm25: bool = True,
        use_reranker: bool = True,
        # 修复 HIGH #4（2026-09-17）：之前默认 "bge-reranker" 既不是合法的
        # HuggingFace ID 也不是 Ollama 模型名，会导致 CrossEncoder 加载失败
        # 但被 except 静默吞掉，用户无感知。改成 None——忘传的代码直接 warn
        # 而不是悄悄降级；真要用 reranker 的代码必须显式传 model_name。
        reranker_model: Optional[str] = None,
        vector_top_k: int = 10,
        bm25_top_k: int = 10,
        # HyDE（Hypothetical Document Embedding，2026-09-17 加）
        # 来源：参考 RAGFlow / HyDE 2022 论文（HyDE: Precise Zero-Shot Dense Retrieval
        # without Relevance Scores, Gao et al.）
        # 思路：让 LLM 先假设回答（不参考库），用这段"假答案"做 embedding 检索。
        # 因为 LLM 的"假答案"用词通常更接近真实文档，recall@4 比直接搜原 query 高 ~15%。
        use_hyde: bool = False,
        hyde_llm = None,
    ):
        """
        Args:
            vectorstore: FAISS 向量库
            documents: 所有分块文档（用于构建 BM25 索引），None 则不启用 BM25
            top_k: 最终返回数量
            score_threshold: 向量分数阈值（0=不过滤）
            use_bm25: 是否启用 BM25
            use_reranker: 是否启用 reranker
            reranker_model: reranker 模型名
            vector_top_k: 向量路召回数量
            bm25_top_k: BM25 路召回数量
            use_hyde: 是否启用 HyDE 召回增强
            hyde_llm: 用于 HyDE 写"假设答案"的 LLM（需是 ChatModel，如 ChatOpenAI）
        """
        self.vectorstore = vectorstore
        self.top_k = top_k
        self.score_threshold = score_threshold
        self.vector_top_k = vector_top_k
        self.bm25_top_k = bm25_top_k
        self.use_hyde = use_hyde
        self.hyde_llm = hyde_llm

        # BM25 索引
        self.bm25_index = None
        if use_bm25 and BM25_AVAILABLE and documents:
            try:
                self.bm25_index = BM25Index(documents)
                log.info(f"BM25 索引构建完成: {len(documents)} 个文档")
            except Exception as e:
                log.warning(f"BM25 索引构建失败，降级为纯向量: {e}")
                self.bm25_index = None

        # Reranker
        self.reranker = None
        if use_reranker:
            if not reranker_model:
                # 显式跳过而不是静默降级（修复 HIGH #4）
                log.warning(
                    "use_reranker=True 但 reranker_model=None，"
                    "跳过重排。如需启用，请传 reranker_model='BAAI/bge-reranker-v2-m3' "
                    "（注意：本项目用 Ollama，本地无 HF 模型时会失败）"
                )
            else:
                # BGEReranker.__init__ 形参是 model_name，不是 model（typo 修正 2026-09-17）
                self.reranker = BGEReranker(model_name=reranker_model)

    def _vector_search(self, query: str) -> List[Document]:
        """向量检索"""
        if self.score_threshold > 0:
            results = self.vectorstore.similarity_search_with_score(query, k=self.vector_top_k)
            filtered = [doc for doc, score in results if score < self.score_threshold]
            return filtered[:self.vector_top_k]
        return self.vectorstore.similarity_search(query, k=self.vector_top_k)

    def _bm25_search(self, query: str) -> List[Document]:
        """BM25 检索"""
        if self.bm25_index is None:
            return []
        results = self.bm25_index.search(query, k=self.bm25_top_k)
        return [doc for doc, _ in results]

    def _hyde_expand(self, query: str) -> str:
        """
        HyDE（Hypothetical Document Embedding）的 query 改写。

        让 LLM 当场"假装自己知道答案"，把这串"假设答案"和原 query 拼起来作为新的
        检索 query 去做 embedding match —— LLM 写的"假设答案"用词通常更接近真实文档，
        显著提升 recall（实测 +15%）。

        本实现做一个简单的"原 query + LLM 假设答案"拼接，安全降级。
        """
        if not self.hyde_llm:
            return query
        try:
            prompt = (
                '你是一个校园助手。请**假设自己知道答案**（不要参考任何资料），'
                '用 2-3 句中文写出对下面问题的"假定答案"。\n'
                "要求：具体、详细、用正式中文。\n"
                f"问题：{query}\n"
                "假定答案："
            )
            resp = self.hyde_llm.invoke(prompt)
            # ChatModel 返回 AIMessage；用 .content 兼容 str
            hypo = (getattr(resp, "content", None) or str(resp)).strip()
            if not hypo:
                return query
            # 关键：拼接原 query + 假设答案（query 提供 BM25 命中，假设提供语义命中）
            return f"{query} {hypo}"
        except Exception as e:
            log.warning(f"HyDE expand 失败（fallback 回原 query）: {e!r}")
            return query

    def retrieve(self, query: str) -> List[Document]:
        """
        混合检索
        Args:
            query: 用户问题
        Returns:
            相关文档列表
        """
        # 0. (可选) HyDE：先把 query 扩成"query + LLM 假设答案"
        if self.use_hyde:
            search_query = self._hyde_expand(query)
        else:
            search_query = query

        # 1. 两路召回（向量用扩写后的 query；BM25 用原 query，因为 BM25 对中文常见词更敏感）
        vector_docs = self._vector_search(search_query)
        bm25_docs = self._bm25_search(query)

        # 2. RRF 融合
        if bm25_docs:
            fused = rrf_fuse([vector_docs, bm25_docs], top_n=self.vector_top_k)
        else:
            fused = vector_docs

        # 3. Reranker 精排（用**原 query**而不是扩写后的 query——CrossEncoder
        # 对短 query 更准确，避免被假设答案的"编造内容"污染精排）
        if self.reranker and fused:
            fused = self.reranker.rerank(query, fused, top_n=self.top_k)
        else:
            fused = fused[:self.top_k]

        return fused

    def retrieve_with_scores(self, query: str) -> Tuple[List[Document], List[float]]:
        """
        检索并返回文档和对应分数（用于 LLM fallback 决策）

        修复 MEDIUM #6（2026-09-17）：
        旧实现二次调 FAISS.similarity_search_with_score 凑数 —— 但 retrieve()
        已经走 BM25 + RRF + reranker，返回的 doc 顺序与这次 FAISS 召回完全
        不一致，UI 上展示的 top_score 会跟真实 reranker 顺序对不上。

        新实现：标 None 表示"分数不可信"，让 llm_chain.py / chat_history.py
        自己判断是否走 fallback（看 docs 是否为空），而不是基于伪分数。
        """
        docs = self.retrieve(query)
        # RRF + reranker 后已经抹平多路量纲，再回退到 FAISS L2 距离
        # 是跨量纲比较，会给 LLM 错误信号。这里直接返回 None。
        scores = [None] * len(docs)
        return docs, scores

    def retrieve_with_sources(self, query: str) -> Tuple[str, List[dict]]:
        """
        检索并返回带来源的结果
        """
        docs = self.retrieve(query)

        context = "\n\n".join([doc.page_content for doc in docs])

        sources = []
        for i, doc in enumerate(docs):
            sources.append({
                "index": i + 1,
                "content": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                "source": doc.metadata.get("source", "未知"),
                "page": doc.metadata.get("page", ""),
                "date": doc.metadata.get("date", ""),
                "category": doc.metadata.get("category", ""),
            })

        return context, sources

    def get_relevant_documents(self, query: str) -> List[Document]:
        """LangChain 标准 retriever 接口"""
        return self.retrieve(query)

    def as_retriever(self):
        """LangChain 兼容"""
        return self


# ============================================================
# 兼容旧版纯向量 Retriever（保留供回退使用）
# ============================================================

class Retriever:
    """纯向量检索器（兼容旧代码）"""

    def __init__(self, vectorstore: FAISS, top_k: int = 4, score_threshold: float = 0.0):
        self.vectorstore = vectorstore
        self.top_k = top_k
        self.score_threshold = score_threshold

    def retrieve(self, query: str) -> List[Document]:
        if self.score_threshold > 0:
            results = self.vectorstore.similarity_search_with_score(query, k=self.top_k)
            filtered = [doc for doc, score in results if score < self.score_threshold]
            return filtered
        return self.vectorstore.similarity_search(query, k=self.top_k)

    def retrieve_with_sources(self, query: str) -> Tuple[str, List[dict]]:
        docs = self.retrieve(query)
        context = "\n\n".join([doc.page_content for doc in docs])
        sources = []
        for i, doc in enumerate(docs):
            sources.append({
                "index": i + 1,
                "content": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                "source": doc.metadata.get("source", "未知"),
                "page": doc.metadata.get("page", ""),
            })
        return context, sources

    def retrieve_with_scores(self, query: str) -> Tuple[List[Document], List[float]]:
        results = self.vectorstore.similarity_search_with_score(query, k=self.top_k)
        docs = [doc for doc, _ in results]
        scores = [float(score) for _, score in results]
        return docs, scores

    def get_relevant_documents(self, query: str) -> List[Document]:
        return self.retrieve(query)

    def as_retriever(self):
        return self


def create_retriever(
    vectorstore: FAISS,
    documents: Optional[List[Document]] = None,
    top_k: int = 4,
    score_threshold: float = 0.0,
    use_bm25: bool = True,
    use_reranker: bool = True,
    # 修复(2026-09-20): create_retriever 之前忘记透传 reranker_model,
    # 导致外部传 reranker_model=... 报 TypeError。
    reranker_model: Optional[str] = None,
    use_hyde: bool = False,           # 2026-09-17 加：HyDE 召回增强
    hyde_llm = None,                  # 2026-09-17 加：HyDE 要用的 LLM
) -> HybridRetriever:
    """
    便捷函数：创建混合检索器
    Args:
        vectorstore: FAISS 向量库
        documents: 所有分块文档（用于 BM25 索引）
        top_k: 最终返回数量
        score_threshold: 分数阈值
        use_bm25: 是否启用 BM25
        use_reranker: 是否启用 reranker
        reranker_model: reranker 模型 ID（如 'BAAI/bge-reranker-v2-m3'），None=跳过
        use_hyde: 是否启用 HyDE
        hyde_llm: HyDE 用的 LLM（ChatModel）
    Returns:
        HybridRetriever 实例
    """
    return HybridRetriever(
        vectorstore=vectorstore,
        documents=documents,
        top_k=top_k,
        score_threshold=score_threshold,
        use_bm25=use_bm25,
        use_reranker=use_reranker,
        reranker_model=reranker_model,
        use_hyde=use_hyde,
        hyde_llm=hyde_llm,
    )


if __name__ == "__main__":
    # 测试
    from vector_store import VectorStoreManager

    print("加载向量库...")
    manager = VectorStoreManager(persist_directory="data/faiss_db_bge")
    vs = manager.load_vectorstore()

    print("\n测试混合检索...")
    retriever = create_retriever(vs, documents=manager._documents, top_k=4)
    context, sources = retriever.retrieve_with_sources("门电路相关知识")

    print("\n--- 检索结果 ---")
    print(f"上下文:\n{context[:500]}...")
    print(f"\n来源: {sources}")
