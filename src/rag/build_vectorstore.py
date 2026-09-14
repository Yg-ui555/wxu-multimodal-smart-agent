"""
构建向量库脚本
使用方法: python src/rag/build_vectorstore.py
"""

import os
import sys

# 将项目根目录添加到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.rag.data_loader import load_and_chunk
from src.rag.vector_store import create_vectorstore_from_docs


def main():
    """主函数：构建向量库"""
    print("=" * 50)
    print("构建向量库")
    print("=" * 50)

    docs_dir = "data/docs"

    # 检查文档目录
    if not os.path.exists(docs_dir):
        print(f"错误: 文档目录不存在: {docs_dir}")
        return

    # 检查是否有文档（包括子文件夹）
    all_files = []
    for root, dirs, files in os.walk(docs_dir):
        for f in files:
            if f.endswith(('.pdf', '.txt')):
                all_files.append(os.path.join(root, f))

    if not all_files:
        print(f"错误: 文档目录为空: {docs_dir}")
        print("请先将 PDF 或 TXT 文档放入 data/docs 目录")
        return

    print(f"\n找到 {len(all_files)} 个文件")
    for f in all_files:
        print(f"  - {f}")

    # 1. 加载并分块文档
    print("\n[1/2] 加载文档并分块...")
    chunks = load_and_chunk(docs_dir)

    if not chunks:
        print("错误: 文档加载失败")
        return

    # 2. 创建向量库
    print("\n[2/2] 创建向量库...")
    vectorstore = create_vectorstore_from_docs(
        documents=chunks,
        persist_directory="data/faiss_db",
        collection_name="wxu_knowledge"
    )

    print("\n" + "=" * 50)
    print("向量库构建完成!")
    print(f"向量库位置: data/faiss_db")
    print(f"文档块数量: {len(chunks)}")
    print("=" * 50)


if __name__ == "__main__":
    main()
