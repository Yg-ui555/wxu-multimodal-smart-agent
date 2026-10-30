# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. 工程化构建脚本（进度显示 + 错误日志 + 异常容错）
# 2. logging 体系（FileHandler + StreamHandler 双输出）
# 3. 单文件失败不中断（try-except 容错 + 失败汇总到日志）
# 4. 耗时估算（已完成批次 / 累计时间 → 预计剩余）
# 5. 断点续跑触发（无需手动，启动时检测 progress.json 自动续跑）
# 6. Ollama 故障诊断（明确的错误提示 + 检查清单）
#
# 💡 这个脚本是"工程化"的最佳示范：能直接拿去面试讲项目"工程能力"
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

"""
构建向量库脚本（带进度显示与错误日志）
使用方法: python src/rag/build_vectorstore.py
"""

import os
import sys
import time
import logging
from datetime import datetime

# 将项目根目录添加到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.rag.data_loader import DocumentLoader, TextChunker
from src.rag.chunk_config import CHUNK_SIZE, CHUNK_OVERLAP
from src.rag.vector_store import create_vectorstore_from_docs

# 日志配置：同时输出到文件 + 控制台
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
log_path = os.path.join(LOG_DIR, "build_vectorstore.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("build_vectorstore")


def format_duration(seconds: float) -> str:
    """把秒数转成 mm:ss 或 hh:mm:ss"""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def collect_files(docs_dir: str):
    """递归收集支持的文件，返回按类型分组的 dict 和总列表"""
    supported = ('.pdf', '.txt', '.docx', '.doc')
    grouped = {'.pdf': [], '.txt': [], '.docx': [], '.doc': []}
    all_files = []
    for root, _, files in os.walk(docs_dir):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in supported:
                full = os.path.join(root, f)
                grouped[ext].append(full)
                all_files.append(full)
    return all_files, grouped


def main():
    print("=" * 60)
    print("构建向量库  [带进度版]")
    print(f"日志: {log_path}")
    print(f"实时查看: Get-Content '{log_path}' -Tail 20 -Wait")
    print("=" * 60)
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    docs_dir = "data/docs"
    if not os.path.exists(docs_dir):
        log.error(f"文档目录不存在: {docs_dir}")
        return

    all_files, grouped = collect_files(docs_dir)
    if not all_files:
        log.error(f"文档目录为空: {docs_dir}")
        return

    # 文件统计
    log.info("📁 文档统计:")
    log.info(f"  PDF : {len(grouped['.pdf'])}")
    log.info(f"  TXT : {len(grouped['.txt'])}")
    log.info(f"  DOCX: {len(grouped['.docx'])}")
    log.info(f"  DOC : {len(grouped['.doc'])}  (旧版二进制，部分可能读不了)")
    log.info(f"  总计: {len(all_files)} 个文件\n")

    # ============ 阶段 1：加载并分块 ============
    log.info("[阶段 1/2] 加载文档并分块")
    log.info("-" * 60)
    started_at = time.time()

    loader = DocumentLoader(docs_dir=docs_dir)
    chunker = TextChunker(chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)

    all_chunks = []
    success_files = []
    failed_files = []  # [(rel_path, error_msg)]

    total = len(all_files)
    for idx, file_path in enumerate(all_files, start=1):
        fname = os.path.basename(file_path)
        rel = os.path.relpath(file_path, docs_dir)
        t0 = time.time()
        try:
            docs = loader.load_document(file_path)
            for doc in docs:
                doc.page_content = loader.clean_text(doc.page_content)
                doc.metadata["source"] = rel
                doc.metadata["file_name"] = fname
            chunks = chunker.split_documents(docs)
            all_chunks.extend(chunks)
            success_files.append(rel)
            dt = time.time() - t0
            elapsed = time.time() - started_at
            speed = idx / elapsed if elapsed > 0 else 0
            remain = (total - idx) / speed if speed > 0 else 0
            display = rel if len(rel) <= 50 else "..." + rel[-47:]
            log.info(
                f"  [{idx:>4}/{total}] ✓ {display:<50}  "
                f"+{len(chunks):>3}块  {dt:>4.1f}s  "
                f"已用 {format_duration(elapsed)}  剩 {format_duration(remain)}"
            )
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            failed_files.append((rel, err))
            display = rel if len(rel) <= 50 else "..." + rel[-47:]
            log.info(
                f"  [{idx:>4}/{total}] ✗ {display:<50}  失败: {err[:80]}"
            )

    elapsed1 = time.time() - started_at
    log.info("-" * 60)
    log.info(f"加载完成: 成功 {len(success_files)} / 失败 {len(failed_files)}")
    log.info(f"生成文本块: {len(all_chunks)}")
    log.info(f"阶段 1 耗时: {format_duration(elapsed1)}\n")

    # 失败文件汇总
    if failed_files:
        log.warning(f"⚠️  共 {len(failed_files)} 个文件加载失败:")
        for rel, err in failed_files[:30]:
            log.warning(f"   - {rel}: {err}")
        if len(failed_files) > 30:
            log.warning(f"   ... 还有 {len(failed_files) - 30} 个，详见日志 {log_path}")

    if not all_chunks:
        log.error("没有可用的文本块，向量库构建中止。")
        return

    # ============ 阶段 2：向量化 + 入库 ============
    log.info("[阶段 2/2] 向量化 + 写入 FAISS")
    log.info("-" * 60)
    log.info(f"调用 Ollama (bge-m3 中文向量) 将 {len(all_chunks)} 个文本块转向量...")
    log.info(f"预计需要 {format_duration(len(all_chunks) * 0.3)} 左右（取决于网络/CPU）\n")

    started_at = time.time()
    try:
        vs = create_vectorstore_from_docs(
            documents=all_chunks,
            persist_directory="data/faiss_db_bge",
            collection_name="wxu_knowledge",
        )
    except Exception as e:
        log.error(f"向量库构建失败: {type(e).__name__}: {e}")
        log.error("可能原因:")
        log.error("  1. Ollama 未启动 (检查 127.0.0.1:11434)")
        log.error("  2. bge-m3 未拉取 (运行: ollama pull bge-m3)")
        log.error("  3. 网络/防火墙拦截")
        sys.exit(1)

    elapsed2 = time.time() - started_at
    log.info("-" * 60)
    log.info("✅ 向量库构建完成!")
    log.info(f"  - 位置      : data/faiss_db_bge")
    log.info(f"  - 文本块数量: {len(all_chunks)}")
    log.info(f"  - 成功加载  : {len(success_files)} / {total}")
    log.info(f"  - 加载失败  : {len(failed_files)}（详见 {log_path}）")
    log.info(f"  - 阶段 1 耗时: {format_duration(elapsed1)}")
    log.info(f"  - 阶段 2 耗时: {format_duration(elapsed2)}")
    log.info(f"  - 总耗时    : {format_duration(elapsed1 + elapsed2)}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
