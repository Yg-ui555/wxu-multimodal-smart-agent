"""
文档加载器：读取 PDF、TXT，清洗换行、多余空格，文本分块
参考 LangChain 官方文档: https://python.langchain.com/docs/tutorials/rag/
"""

import os
import re
from typing import List
from langchain_community.document_loaders import PyMuPDFLoader, TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document


class DocumentLoader:
    """文档加载器，支持 PDF 和 TXT"""

    def __init__(self, docs_dir: str = "data/docs"):
        """
        初始化文档加载器
        Args:
            docs_dir: 文档目录路径
        """
        self.docs_dir = docs_dir

    def load_document(self, file_path: str) -> List[Document]:
        """
        加载单个文档
        Args:
            file_path: 文档路径
        Returns:
            Document 列表
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            loader = PyMuPDFLoader(file_path)
        elif ext == ".txt":
            loader = TextLoader(file_path, encoding="utf-8")
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

        docs = loader.load()
        # 添加更多metadata
        for doc in docs:
            doc.metadata["file_name"] = os.path.basename(file_path)
        return docs

    def clean_text(self, text: str) -> str:
        """
        清洗文本：去除多余换行、空格
        Args:
            text: 原始文本
        Returns:
            清洗后的文本
        """
        # 去除多余换行（保留段落分隔）
        text = re.sub(r'\n{3,}', '\n\n', text)
        # 去除行首行尾空格
        text = re.sub(r'^[ \t]+|[ \t]+$', '', text, flags=re.MULTILINE)
        # 去除多余空格
        text = re.sub(r' {2,}', ' ', text)
        return text

    def load_all_documents(self, recursive: bool = True) -> List[Document]:
        """
        加载目录下所有文档
        Args:
            recursive: 是否递归子文件夹
        Returns:
            所有 Document 列表
        """
        if not os.path.exists(self.docs_dir):
            print(f"警告: 文档目录不存在: {self.docs_dir}")
            return []

        documents = []

        if recursive:
            # 递归遍历所有子文件夹
            for root, dirs, files in os.walk(self.docs_dir):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    if os.path.isfile(file_path):
                        try:
                            docs = self.load_document(file_path)
                            # 清洗每个文档的文本内容
                            for doc in docs:
                                doc.page_content = self.clean_text(doc.page_content)
                                # 记录来源路径
                                doc.metadata["source"] = os.path.relpath(file_path, self.docs_dir)
                            documents.extend(docs)
                            print(f"已加载: {filename} ({len(docs)} 页)")
                        except Exception as e:
                            print(f"加载失败 {filename}: {e}")
        else:
            # 只读取当前目录
            for filename in os.listdir(self.docs_dir):
                file_path = os.path.join(self.docs_dir, filename)
                if os.path.isfile(file_path):
                    try:
                        docs = self.load_document(file_path)
                        for doc in docs:
                            doc.page_content = self.clean_text(doc.page_content)
                        documents.extend(docs)
                        print(f"已加载: {filename} ({len(docs)} 页)")
                    except Exception as e:
                        print(f"加载失败 {filename}: {e}")

        return documents


class TextChunker:
    """文本分块器"""

    def __init__(self, chunk_size: int = 800, overlap: int = 150):
        """
        初始化分块器
        Args:
            chunk_size: 块大小
            overlap: 重叠大小
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]
        )

    def split_documents(self, documents: List[Document]) -> List[Document]:
        """
        将文档列表分块
        Args:
            documents: Document 列表
        Returns:
            分块后的 Document 列表
        """
        return self.splitter.split_documents(documents)

    def split_text(self, text: str) -> List[str]:
        """
        将文本分块
        Args:
            text: 原始文本
        Returns:
            文本块列表
        """
        return self.splitter.split_text(text)


def load_and_chunk(docs_dir: str = "data/docs") -> List[Document]:
    """
    便捷函数：加载目录中所有文档并分块
    Args:
        docs_dir: 文档目录
    Returns:
        分块后的文档列表
    """
    loader = DocumentLoader(docs_dir)
    documents = loader.load_all_documents()

    if not documents:
        print("警告: 未加载到任何文档")
        return []

    chunker = TextChunker(chunk_size=800, overlap=150)
    chunks = chunker.split_documents(documents)

    print(f"文档分块完成: 共 {len(chunks)} 个文本块")
    return chunks


if __name__ == "__main__":
    # 测试
    chunks = load_and_chunk()
    if chunks:
        print(f"\n示例文本块 (前500字符):")
        print(chunks[0].page_content[:500])
