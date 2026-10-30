# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. Gradio 上传文件的处理流（gr.File 返回 filepath 列表 → 落临时盘 → 我们读）
# 2. 文件类型嗅探（扩展名 + content_hash 做去重）
# 3. 复用 DocumentLoader / TextChunker 复用现有 RAG 链路
# 4. 增量入向量库的容错：单文件失败不影响其他
# 5. 给用户清晰的回执：成功数 / 失败数 / 跳过数 / 失败原因
#
# 💡 进阶方向（写到简历上）：
#    - 增量索引监控：写完一文件就刷一次 UI（stream out）
#    - 上传审核：不让用户上传 .doc/.pdf 敏感信息（在文末给锐评）
#    - 上传后 "立刻试试问个问题" 一键回归
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

"""
上传处理器：网页用户上传文档 → 自动识别 → 清洗 → 分块 → 入向量库

支持格式：.txt / .md / .pdf / .docx / .doc
复用 src/rag/data_loader.py 与 src/rag/vector_store.py
"""

import os
import sys
import hashlib
import traceback
from typing import List, Dict, Any

# 路径：让 src 包可被 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.rag.data_loader import (
    DocumentLoader,
    TextChunker,
    dedup_chunks,
    _content_hash,
)
from src.rag.chunk_config import CHUNK_SIZE, CHUNK_OVERLAP
from src.rag.vector_store import VectorStoreManager
from langchain_core.documents import Document


# 支持的文件扩展名
SUPPORTED_EXTS = {".txt", ".md", ".pdf", ".docx", ".doc"}
# 单文件大小上限：50 MB（防撞死服务）
MAX_FILE_BYTES = 50 * 1024 * 1024


def _category_meta(file_name: str, category: str) -> dict:
    """构造 chunk 的 source 字段：'用户上传/类别/<file_name>' 形式，方便检索时溯源。"""
    cat = (category or "默认").strip()
    # 路径分隔归一
    cat = cat.replace("\\", "/").strip("/")
    return {
        "source": f"用户上传/{cat}/{file_name}",
        "category": cat,
        "uploaded_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }


def _build_status_md(stats: Dict[str, Any], failed: List[tuple]) -> str:
    """
    把入库统计格式化成 Markdown，UI 上展示。
    failed: [(file_name, error_str), ...]
    """
    lines = []
    if stats.get("uploaded"):
        lines.append(f"✅ **上传成功**：{stats['uploaded']} 个文件")
    if stats.get("chunks_added"):
        lines.append(f"📦 **入库分块**：{stats['chunks_added']} 个新文档块")
    if stats.get("duplicate_skipped"):
        lines.append(f"♻️ **跳过重复**：{stats['duplicate_skipped']} 个分块（基于 content_hash）")

    if failed:
        lines.append("")
        lines.append(f"⚠️ **失败 {len(failed)} 个**（不影响其他）：")
        for fname, err in failed:
            err_one_line = err.replace("\n", " ")[:120]
            lines.append(f"- `{fname}` — {err_one_line}")

    if not stats.get("uploaded") and not failed:
        lines.append("_（本次未新增内容）_")

    lines.append("")
    # 给个提问 demo prompt
    if stats.get("chunks_added"):
        lines.append(
            f"> 💡 **试试提问**：回到「对话」Tab，问一个跟刚上传文档相关的问题（如 `门电路是什么`），"
            f"验证是否能命中刚刚入库的内容。"
        )
    return "\n".join(lines)


def process_uploads(
    file_paths: List[str],
    category: str = "默认",
    vector_db_dir: str = "data/faiss_db_bge",
) -> Dict[str, Any]:
    """
    处理用户上传的一个或多个文件：识别 → 清洗 → 分块 → 入向量库

    Args:
        file_paths: gr.File 组件返回的临时文件路径列表
        category: 用户填写的"类别"，作为 metadata.source 的二级目录
        vector_db_dir: 目标向量库目录

    Returns:
        {
            "uploaded": int,        # 成功入库文件数
            "chunks_added": int,    # 入库分块数
            "duplicate_skipped": int,
            "files_failed": [(file_name, error), ...],
            "message_md": str,      # UI 展示的 Markdown 报告
            "status": "ok" | "warn" | "fail",
        }
    """
    result = {
        "uploaded": 0,
        "chunks_added": 0,
        "duplicate_skipped": 0,
        "files_failed": [],
        "message_md": "",
        "status": "ok",
    }

    if not file_paths:
        result["status"] = "warn"
        result["message_md"] = "⚠️ 没收到文件，请先选择文件再上传。"
        return result

    loader = DocumentLoader()
    chunker = TextChunker(chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
    manager = VectorStoreManager(persist_directory=vector_db_dir)

    # 先加载已有向量库以构建"已有 hash 集合"（用于跨文件去重）
    manager.load_vectorstore()
    existing_hashes = set()
    for doc in (manager._documents or []):
        h = (doc.metadata or {}).get("content_hash")
        if h:
            existing_hashes.add(h)

    seen_file_hash = set()  # 同一份上传两次 → 跳过

    for raw_path in file_paths:
        # Gradio 4.x gr.File 在 file_count='single' 时是 str，multiple 时是 list
        if isinstance(raw_path, (list, tuple)):
            # 嵌套场景防御（不会被触发但写防御）
            inner_files = raw_path
        else:
            inner_files = [raw_path]

        for fp in inner_files:
            file_name = os.path.basename(fp)
            try:
                if not os.path.exists(fp):
                    raise FileNotFoundError(f"上传文件路径丢失（Gradio 临时目录被清理？）: {fp}")

                ext = os.path.splitext(fp)[1].lower()
                if ext not in SUPPORTED_EXTS:
                    raise ValueError(f"不支持的格式: {ext}（仅支持 .txt/.md/.pdf/.docx/.doc）")

                size = os.path.getsize(fp)
                if size > MAX_FILE_BYTES:
                    raise ValueError(f"文件过大 {size/1024/1024:.1f}MB，超过 50MB 限制")

                # 用文件名 + 大小 + 第一行 hash 做"上传文件级去重"
                with open(fp, "rb") as f:
                    file_body = f.read(4096)
                file_fingerprint = hashlib.md5(
                    (file_name + str(size) + file_body.decode("utf-8", errors="ignore")).encode("utf-8")
                ).hexdigest()[:16]
                if file_fingerprint in seen_file_hash:
                    result["duplicate_skipped"] += 1  # 计入重复
                    continue
                seen_file_hash.add(file_fingerprint)

                # 1. 加载 + 清洗
                docs = loader.load_document(fp)
                for d in docs:
                    d.page_content = loader.clean_text(d.page_content)
                    d.metadata.update(_category_meta(file_name, category))

                if not docs or all(not d.page_content.strip() for d in docs):
                    raise ValueError("文件内容为空或纯图片（PDF 扫描版需 OCR）")

                # 2. 分块（带元数据）
                chunks = chunker.split_documents(docs)

                # 3. 去重：先用当前 chunk 间 dedup，再对照已有 hash 集
                before = len(chunks)
                chunks = dedup_chunks(chunks)
                cross_dedup = 0
                filtered = []
                for c in chunks:
                    h = (c.metadata or {}).get("content_hash")
                    if h and h in existing_hashes:
                        cross_dedup += 1
                        continue
                    if h:
                        existing_hashes.add(h)
                    filtered.append(c)
                chunks = filtered
                result["duplicate_skipped"] += (before - len(chunks)) + cross_dedup

                if not chunks:
                    # 文件有效但全部已经在库里
                    result["uploaded"] += 1  # 算"成功处理了"但没新增
                    continue

                # 4. 入库（add_documents 内部加锁）
                manager.add_documents(chunks)
                result["chunks_added"] += len(chunks)
                result["uploaded"] += 1

            except Exception as e:
                err_str = f"{type(e).__name__}: {e!r}"
                result["files_failed"].append((file_name, err_str))
                # 不打断，继续下一个文件

    # 汇总 UI
    result["status"] = "warn" if result["files_failed"] else "ok"
    result["message_md"] = _build_status_md(
        {
            "uploaded": result["uploaded"],
            "chunks_added": result["chunks_added"],
            "duplicate_skipped": result["duplicate_skipped"],
        },
        result["files_failed"],
    )
    return result


if __name__ == "__main__":
    # 调试：直接传文件路径测试
    import sys, json
    if len(sys.argv) > 1:
        out = process_uploads(sys.argv[1:], category="测试")
        print(json.dumps({k: v for k, v in out.items() if k != "message_md"}, ensure_ascii=False, indent=2))
        print("--- UI Markdown ---")
        print(out["message_md"])
