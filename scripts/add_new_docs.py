"""
增量添加新文档到 FAISS（绕过 build_vectorstore.py 的断点续跑 bug）

用法: python scripts/add_new_docs.py

适用场景:
- 上次向量库已构建完成（done=true）
- 又新增了几个文档（手动复制 / 网页抓取）
- 不希望重跑整个 build_vectorstore.py

工作原理:
1. 加载现有 FAISS + documents.pkl
2. 扫描 data/docs 找出现在但不在 documents.pkl 里的新文件
3. 加载 + 切块 + add_documents
"""
import os
import sys

# 项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rag.data_loader import DocumentLoader, TextChunker
from src.rag.vector_store import VectorStoreManager


def main():
    docs_dir = "data/docs"
    persist_dir = "data/faiss_db_bge"

    if not os.path.exists(os.path.join(persist_dir, "index.faiss")):
        print(f"❌ 向量库不存在，请先跑 build_vectorstore.py")
        return

    print("=" * 60)
    print("增量添加新文档到 FAISS")
    print("=" * 60)

    # 1. 加载现有 FAISS
    print("\n[1/3] 加载现有向量库...")
    manager = VectorStoreManager(persist_directory=persist_dir)
    manager.load_vectorstore()
    print(f"  ✓ 现有 chunks: {len(manager._documents or [])}")

    # 2. 找出已入库的文件相对路径
    existing_sources = set()
    for doc in (manager._documents or []):
        src = doc.metadata.get("source", "")
        if src:
            existing_sources.add(src)
    print(f"  ✓ 已入库文件: {len(existing_sources)} 个")

    # 3. 扫描 docs_dir 找新文件
    print("\n[2/3] 扫描新文件...")
    supported = ('.pdf', '.txt', '.docx', '.doc')
    new_files = []
    for root, _, files in os.walk(docs_dir):
        for f in files:
            if f.endswith(supported):
                full_path = os.path.join(root, f)
                rel = os.path.relpath(full_path, docs_dir)
                # 检查是否新文件（按 source 路径 OR 文件 mtime 更新）
                if rel not in existing_sources:
                    new_files.append((full_path, rel, 'new'))
                else:
                    # 已存在 - 检查文件是否被修改（用文件大小简单判断）
                    try:
                        existing_doc_size = 0
                        for doc in manager._documents:
                            if doc.metadata.get('source') == rel:
                                existing_doc_size = len(doc.page_content)
                                break
                        new_size = os.path.getsize(full_path)
                        # 如果大小差异超过 200 字符，认为内容变了
                        if abs(existing_doc_size - new_size) > 200:
                            new_files.append((full_path, rel, 'updated'))
                    except Exception:
                        pass

    if not new_files:
        print("  ✓ 没有新文件需要添加")
        return

    print(f"  ✓ 找到 {len(new_files)} 个新/更新文件:")
    for full, rel, status in new_files:
        marker = '[NEW]' if status == 'new' else '[UPDATED]'
        print(f"    {marker} {rel}")

    # 4. 加载 + 切块 + 加入
    print("\n[3/3] 加载 + 切块 + 入库...")
    loader = DocumentLoader(docs_dir=docs_dir)
    chunker = TextChunker(chunk_size=800, overlap=150)

    new_chunks = []
    for full_path, rel, status in new_files:
        try:
            docs = loader.load_document(full_path)
            for doc in docs:
                doc.page_content = loader.clean_text(doc.page_content)
                doc.metadata["source"] = rel
                doc.metadata["file_name"] = os.path.basename(full_path)
            chunks = chunker.split_documents(docs)
            new_chunks.extend(chunks)
            print(f"  [OK] {rel}: {len(chunks)} 块")
        except Exception as e:
            print(f"  [FAIL] {rel}: {e}")

    if not new_chunks:
        print("\n[!] 没有可用的 chunks")
        return

    # 5. 加到 FAISS
    print(f"\n正在添加 {len(new_chunks)} 个新 chunks 到 FAISS...")
    manager.add_documents(new_chunks)

    print("\n" + "=" * 60)
    print("[OK] 完成!")
    print(f"  - 新增文件: {len(new_files)}")
    print(f"  - 新增 chunks: {len(new_chunks)}")
    print(f"  - 现有 chunks 总量: {len(manager._documents or [])}")
    print("=" * 60)
    print("\n下一步: 重启 app.py，问之前的问题看看")


if __name__ == "__main__":
    main()