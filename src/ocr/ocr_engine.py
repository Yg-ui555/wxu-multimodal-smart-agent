"""
OCR 引擎：PaddleOCR 封装 + 自动入向量库

用法（作为库）:
    from src.ocr import extract_text, add_ocr_to_vectorstore

    text = extract_text("data/uploads/library_sign.jpg")
    add_ocr_to_vectorstore(image_path="...", text=text)

模块自测:
    python src/ocr/ocr_engine.py <image_path>
"""

from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
import logging

from PIL import Image

log = logging.getLogger("ocr.engine")


# ============ 全局缓存 PaddleOCR 实例（首次加载较慢 ~5-10s） ============
_ocr_instance = None


def _get_ocr():
    """懒加载 PaddleOCR 全局单例"""
    global _ocr_instance
    if _ocr_instance is None:
        from paddleocr import PaddleOCR
        log.info("首次加载 PaddleOCR 模型（中文 + 英文）...")
        _ocr_instance = PaddleOCR(
            use_angle_cls=True,   # 支持倾斜文字
            lang="ch",            # 中文
            show_log=False,       # 静默 PaddleOCR 内部日志
        )
        log.info("PaddleOCR 模型加载完成")
    return _ocr_instance


def extract_text(
    image_path: str,
    conf_threshold: float = 0.5,
    progress=None,
) -> Dict:
    """
    从图片中识别文字（PaddleOCR）。

    Args:
        image_path: 图片文件路径
        conf_threshold: 置信度阈值（默认 0.5，太低的预测会被丢弃）
        progress: gr.Progress() 回调，可选

    Returns:
        dict: {
            "text": str,                  # 完整文本（按行拼接）
            "lines": List[{text, conf}],  # 每行明细
            "image_path": str,            # 原图路径
            "line_count": int,
            "model": str,                 # PaddleOCR 版本
        }
    """
    if progress:
        progress(0.1, desc="加载 OCR 模型...")

    ocr = _get_ocr()

    if progress:
        progress(0.3, desc="读取图片...")

    img_path = Path(image_path)
    if not img_path.exists():
        raise FileNotFoundError(f"图片不存在: {image_path}")

    if progress:
        progress(0.5, desc="识别文字中...")

    # PaddleOCR 识别
    result = ocr.ocr(str(img_path), cls=True)

    if progress:
        progress(0.85, desc="整理识别结果...")

    lines: List[Dict] = []
    if result and result[0]:
        for line in result[0]:
            # line 格式: [bbox, (text, conf)]
            text = line[1][0]
            conf = float(line[1][1])
            if conf >= conf_threshold:
                lines.append({"text": text, "conf": round(conf, 3)})

    # 按行拼接成完整文本
    full_text = "\n".join(line["text"] for line in lines)

    if progress:
        progress(1.0, desc=f"识别完成，共 {len(lines)} 行")

    return {
        "text": full_text,
        "lines": lines,
        "image_path": str(img_path),
        "line_count": len(lines),
        "model": "PaddleOCR",
    }


def add_ocr_to_vectorstore(
    image_path: str,
    text: str,
    line_count: int = 0,
    target_dir: str = "data/docs/用户上传OCR",
    progress=None,
) -> Dict:
    """
    把 OCR 识别到的文本落盘 + 入向量库。

    流程:
        1. 把 text 写到 target_dir/时间戳_原图名.txt
        2. metadata 包含: original_image / ocr_model / extracted_at
        3. 调用 VectorStoreManager.add_documents 入库

    Returns:
        dict: {
            "text_file": str,        # 落盘的文件路径
            "added_chunks": int,     # 入库的 chunk 数
            "collection": str,       # 向量库 collection 名
        }
    """
    if not text or not text.strip():
        raise ValueError("OCR 文本为空，无可入库内容")

    if progress:
        progress(0.1, desc="准备落盘目录...")

    img_path = Path(image_path)
    project_root = Path(__file__).resolve().parent.parent.parent
    target = (project_root / target_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)

    # 文件名: 2026-09-19_图书馆海报.txt
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safe_stem = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in img_path.stem
    )[:40]  # 限制长度
    text_file = target / f"{timestamp}_{safe_stem}.txt"

    if progress:
        progress(0.3, desc="写入文本文件...")

    # 写文件 + metadata 头部（让 RAG 检索时能看到上下文）
    metadata_header = (
        f"# 来源：用户上传 OCR 识别\n"
        f"# 原图：{img_path.name}\n"
        f"# 提取时间：{datetime.now().isoformat()}\n"
        f"# OCR 模型：PaddleOCR\n"
        f"# 识别行数：{line_count}\n"
        f"\n---\n\n"
    )
    text_file.write_text(metadata_header + text, encoding="utf-8")

    if progress:
        progress(0.5, desc="加载向量库...")

    # 入向量库
    from src.rag.vector_store import VectorStoreManager
    from langchain_core.documents import Document

    # 用项目根绝对路径,避免 cwd 不在项目根时相对路径解析失败
    vsm = VectorStoreManager(
        persist_directory=str(project_root / "data" / "faiss_db_bge")
    )
    vs = vsm.load_vectorstore()  # 加载已有向量库

    if progress:
        progress(0.75, desc="向量化 + 入库...")

    # 构造 Document 对象（metadata 方便后续追溯）
    doc = Document(
        page_content=text,
        metadata={
            "source": str(text_file.relative_to(project_root)),
            "file_name": text_file.name,
            "original_image": str(img_path),
            "ocr_model": "PaddleOCR",
            "extracted_at": datetime.now().isoformat(),
            "category": "用户上传OCR",
            "line_count": line_count,
        },
    )

    # 切片（与现有文档同等切块）
    from src.rag.data_loader import TextChunker
    chunker = TextChunker()
    chunks = chunker.split_documents([doc])

    # 入库
    vsm.add_documents(chunks)

    if progress:
        progress(1.0, desc=f"入库完成，共 {len(chunks)} 个 chunk")

    return {
        "text_file": str(text_file),
        "added_chunks": len(chunks),
        "collection": "wxu_knowledge",
    }


# ============ 模块自测 ============
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python src/ocr/ocr_engine.py <image_path>")
        sys.exit(1)

    img = sys.argv[1]
    print(f"=== OCR 识别 {img} ===")
    result = extract_text(img)
    print(f"\n识别到 {result['line_count']} 行:")
    print("-" * 60)
    print(result["text"])
    print("-" * 60)
    print(f"\n正在入库...")
    info = add_ocr_to_vectorstore(img, result["text"], result["line_count"])
    print(f"已入库: {info}")
