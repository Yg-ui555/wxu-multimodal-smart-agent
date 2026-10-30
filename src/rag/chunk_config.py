"""文本分块参数 —— 单一真相源

chunk_size=800 / overlap=150 的取舍依据见 src/rag/data_loader.py 顶部的注释。
集中在这里是为了改一处就能全局生效，不再散落在 7 个调用点。
"""

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
