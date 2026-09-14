"""
检索器：接收用户问题，返回最相关的文档片段
"""

from typing import List, Optional, Tuple
from langchain.schema import Document
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings


class Retriever:
    """文档检索器"""

    def __init__(
        self,
        vectorstore: FAISS,
        top_k: int = 4,
        score_threshold: float = 0.0
    ):
        """
        初始化检索器
        Args:
            vectorstore: FAISS 向量库实例
            top_k: 返回 Top-K 个结果
            score_threshold: 相似度分数阈值（0-1），低于此分数的结果过滤掉
        """
        self.vectorstore = vectorstore
        self.top_k = top_k
        self.score_threshold = score_threshold

    def retrieve(self, query: str) -> List[Document]:
        """
        检索相关文档
        Args:
            query: 用户问题
        Returns:
            相关文档列表
        """
        if self.score_threshold > 0:
            results = self.vectorstore.similarity_search_with_score(
                query, k=self.top_k
            )
            # 过滤低分结果
            filtered = [(doc, score) for doc, score in results if score < self.score_threshold]
            return [doc for doc, _ in filtered]
        else:
            return self.vectorstore.similarity_search(query, k=self.top_k)

    def retrieve_with_sources(self, query: str) -> Tuple[str, List[dict]]:
        """
        检索并返回带来源的结果
        Args:
            query: 用户问题
        Returns:
            (合并的文本, 来源列表)
        """
        docs = self.retrieve(query)

        # 合并文本
        context = "\n\n".join([doc.page_content for doc in docs])

        # 提取来源信息
        sources = []
        for i, doc in enumerate(docs):
            source = {
                "index": i + 1,
                "content": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                "source": doc.metadata.get("source", "未知"),
                "page": doc.metadata.get("page", "")
            }
            sources.append(source)

        return context, sources


def create_retriever(
    vectorstore: FAISS,
    top_k: int = 4,
    score_threshold: float = 0.0
) -> Retriever:
    """
    便捷函数：创建检索器
    Args:
        vectorstore: FAISS 向量库
        top_k: Top-K
        score_threshold: 分数阈值
    Returns:
        Retriever 实例
    """
    return Retriever(vectorstore, top_k, score_threshold)


if __name__ == "__main__":
    # 测试
    from vector_store import VectorStoreManager

    print("加载向量库...")
    manager = VectorStoreManager(persist_directory="data/faiss_db")
    vs = manager.load_vectorstore()

    print("\n测试检索...")
    retriever = create_retriever(vs, top_k=3)
    context, sources = retriever.retrieve_with_sources("电子信息工程专业需要多少学分？")

    print("\n--- 检索结果 ---")
    print(f"上下文:\n{context[:500]}...")
    print(f"\n来源: {sources}")
