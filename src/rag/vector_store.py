"""
FAISS 向量库：将分块文本转为向量，存入本地 FAISS
使用 Ollama 嵌入模型
"""

import os
import pickle
from typing import List, Optional
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings
from langchain.schema import Document


class VectorStoreManager:
    """FAISS 向量库管理器"""

    def __init__(
        self,
        persist_directory: str = "data/faiss_db",
        embedding_model: str = "nomic-embed-text"
    ):
        """
        初始化向量库管理器
        Args:
            persist_directory: FAISS 持久化目录
            embedding_model: Ollama 嵌入模型名称
        """
        self.persist_directory = persist_directory
        self.embedding_model = embedding_model

        # 初始化嵌入模型（使用 Ollama）
        self.embeddings = OllamaEmbeddings(
            model=embedding_model
        )

        # 向量库实例（延迟初始化）
        self._vectorstore = None
        self._documents = None  # 保存原始文档

    @property
    def vectorstore(self) -> Optional[FAISS]:
        """获取向量库实例"""
        return self._vectorstore

    def create_vectorstore(self, documents: List[Document], collection_name: str = "wxu_knowledge") -> FAISS:
        """
        从文档创建向量库
        Args:
            documents: 分块后的文档列表
            collection_name: Collection 名称（用于保存文档）
        Returns:
            FAISS 向量库实例
        """
        if not documents:
            raise ValueError("文档列表为空")

        # 保存原始文档
        self._documents = documents

        # 确保目录存在
        os.makedirs(self.persist_directory, exist_ok=True)

        # 创建向量库
        self._vectorstore = FAISS.from_documents(
            documents=documents,
            embedding=self.embeddings
        )

        # 保存向量库到磁盘
        self._save_vectorstore()

        print(f"向量库创建完成: {len(documents)} 个文档块")
        return self._vectorstore

    def _save_vectorstore(self):
        """保存向量库到磁盘"""
        if self._vectorstore is not None:
            self._vectorstore.save_local(self.persist_directory)
            # 保存原始文档
            doc_path = os.path.join(self.persist_directory, "documents.pkl")
            with open(doc_path, "wb") as f:
                pickle.dump(self._documents, f)
            print(f"向量库已保存到: {self.persist_directory}")

    def load_vectorstore(self, collection_name: str = "wxu_knowledge") -> FAISS:
        """
        加载已存在的向量库
        Args:
            collection_name: Collection 名称
        Returns:
            FAISS 向量库实例
        """
        if not os.path.exists(self.persist_directory):
            raise FileNotFoundError(f"向量库目录不存在: {self.persist_directory}")

        # 加载向量库
        self._vectorstore = FAISS.load_local(
            self.persist_directory,
            self.embeddings,
            allow_dangerous_deserialization=True
        )

        # 加载原始文档
        doc_path = os.path.join(self.persist_directory, "documents.pkl")
        if os.path.exists(doc_path):
            with open(doc_path, "rb") as f:
                self._documents = pickle.load(f)

        print(f"向量库加载完成: {len(self._documents) if self._documents else 0} 个文档")
        return self._vectorstore

    def add_documents(self, documents: List[Document]) -> None:
        """
        添加新文档到向量库
        Args:
            documents: 新增的文档列表
        """
        if self._vectorstore is None:
            self.load_vectorstore()

        # 添加文档
        self._vectorstore.add_documents(documents)

        # 更新原始文档列表
        if self._documents is None:
            self._documents = []
        self._documents.extend(documents)

        # 重新保存
        self._save_vectorstore()

        print(f"新增 {len(documents)} 个文档块")

    def similarity_search(self, query: str, k: int = 4) -> List[Document]:
        """
        相似度检索
        Args:
            query: 查询文本
            k: 返回 Top-K 个结果
        Returns:
            相关的文档列表
        """
        if self._vectorstore is None:
            raise RuntimeError("向量库未初始化，请先创建或加载向量库")

        return self._vectorstore.similarity_search(query, k=k)

    def similarity_search_with_score(self, query: str, k: int = 4) -> List[tuple]:
        """
        相似度检索（带分数）
        Args:
            query: 查询文本
            k: 返回 Top-K 个结果
        Returns:
            (文档, 分数) 元组列表
        """
        if self._vectorstore is None:
            raise RuntimeError("向量库未初始化，请先创建或加载向量库")

        return self._vectorstore.similarity_search_with_score(query, k=k)

    def as_retriever(self, search_kwargs: dict = None):
        """
        转换为 LangChain Retriever
        Args:
            search_kwargs: 检索参数，如 {"k": 4}
        Returns:
            Retriever 对象
        """
        if self._vectorstore is None:
            raise RuntimeError("向量库未初始化")

        if search_kwargs is None:
            search_kwargs = {"k": 4}

        return self._vectorstore.as_retriever(search_kwargs=search_kwargs)


def create_vectorstore_from_docs(
    documents: List[Document],
    persist_directory: str = "data/faiss_db",
    collection_name: str = "wxu_knowledge"
) -> FAISS:
    """
    便捷函数：从文档创建向量库
    Args:
        documents: 文档列表
        persist_directory: 持久化目录
        collection_name: Collection 名称
    Returns:
        FAISS 向量库
    """
    manager = VectorStoreManager(persist_directory=persist_directory)
    return manager.create_vectorstore(documents, collection_name)


if __name__ == "__main__":
    # 测试
    from data_loader import load_and_chunk

    print("加载文档...")
    chunks = load_and_chunk("data/docs")

    if chunks:
        print("\n创建向量库...")
        vs = create_vectorstore_from_docs(chunks)

        print("\n测试检索...")
        results = vs.similarity_search("图书馆开放时间", k=2)
        for i, doc in enumerate(results):
            print(f"\n--- 结果 {i+1} ---")
            print(doc.page_content[:200])
