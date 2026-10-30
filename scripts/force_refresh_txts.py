"""精准重新 embedding 两个网页抓取的 txt，覆盖旧 chunks"""
import os
import sys
sys.path.insert(0, '.')

from src.rag.data_loader import DocumentLoader, TextChunker
from src.rag.chunk_config import CHUNK_SIZE, CHUNK_OVERLAP
from src.rag.vector_store import VectorStoreManager

# 1. 加载现有 FAISS
manager = VectorStoreManager('data/faiss_db_bge')
manager.load_vectorstore()

# 2. 加载并切块
loader = DocumentLoader(docs_dir='data/docs')
chunker = TextChunker(chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)

target_files = [
    'data/docs/学校通知/关于2026年下半年全国大学英语四、六级笔试和口试报名的通知-无锡学院教务处.txt',
    'data/docs/学校通知/无锡学院学校奖学金评定办法（试行）-无锡学院学生工作处.txt',
]

print('=== 加载并切块 ===')
new_chunks = []
for fp in target_files:
    if not os.path.exists(fp):
        print(f'[!] 文件不存在: {fp}')
        continue
    docs = loader.load_document(fp)
    for doc in docs:
        doc.page_content = loader.clean_text(doc.page_content)
        doc.metadata['source'] = os.path.relpath(fp, 'data/docs')
        doc.metadata['file_name'] = os.path.basename(fp)
    chunks = chunker.split_documents(docs)
    new_chunks.extend(chunks)
    print(f'  {os.path.basename(fp)[:60]}: {len(chunks)} 块, {len(docs[0].page_content)} 字符')

print()
print(f'=== 新增 {len(new_chunks)} chunks 到 FAISS ===')
manager.add_documents(new_chunks)
print(f'  FAISS 总数: {manager.vectorstore.index.ntotal}')
print(f'  documents.pkl 总数: {len(manager._documents)}')

# 3. 验证：搜'四六级考试报名通知'
print()
print('=== 验证召回 ===')
queries = ['四六级考试报名通知', '奖学金评定通知', '奖学金一等奖多少钱']
for q in queries:
    results = manager.vectorstore.similarity_search_with_score(q, k=8)
    print(f'\n{q}:')
    for i, (doc, score) in enumerate(results):
        src = doc.metadata.get('source', '?')
        marker = '[NEW]' if any(k in src for k in ['奖学金评定办法', '5934', '2471']) else '     '
        print(f'  {marker} [{i+1}] score={score:.4f} {src[:70]}')
