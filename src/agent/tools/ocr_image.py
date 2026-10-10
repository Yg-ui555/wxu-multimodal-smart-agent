"""
Tool 3: ocr_image
作用:用 PaddleOCR 识别图片里的文字(对接你已经封装好的 ocr_engine)
关键设计:任何 tool 失败都不能让 Agent 崩,统一返回降级字符串
"""
import logging
from pathlib import Path

from langchain_core.tools import tool

# 复用现有 OCR
from src.ocr.ocr_engine import extract_text

log = logging.getLogger("agent.tools.ocr_image")

# 上下文注入:图片路径(类似 get_user_schedule 的 uid 注入)
_current_image_holder = {"path": None}


def set_current_image(path: str) -> None:
    """ReAct 引擎在调 OCR 前,把当前会话的图片路径注入进来"""
    _current_image_holder["path"] = path


def _get_current_image() -> str:
    return _current_image_holder["path"] or ""


@tool
def ocr_image(image_path: str = "") -> str:
    """用 OCR 识别图片里的文字内容。

    Args:
        image_path: 图片文件的绝对路径(例: "data/uploads/notice.jpg")。
                    如果不传,会自动用当前会话刚上传的图片(由后端注入)。

    Returns:
        识别出的文字内容(去除空行的纯文本)。如果失败会返回降级消息。
    """
    # 1. 优先用参数,空则用 context 注入
    path = (image_path or "").strip() or _get_current_image()
    if not path:
        return "(没有指定图片路径,也没当前会话图片。请先上传图片再调此 tool)"

    p = Path(path)
    if not p.exists():
        return f"(图片不存在: {path})"

    # 2. 调 OCR(你已有的封装,内部已用全局单例缓存 PaddleOCR 实例)
    try:
        result = extract_text(str(p), conf_threshold=0.5)
    except Exception as e:
        log.exception("OCR 失败")
        return f"(OCR 失败: {type(e).__name__}: {e})"

    # 3. 解析结果
    text = (result or {}).get("text", "").strip()
    if not text:
        return "(图片里没识别出文字,可能是空白图 / 置信度太低 / 纯图形)"

    # 4. 截断防 token 爆炸(单 tool 结果不超过 3000 字符)
    if len(text) > 3000:
        text = text[:3000] + "\n...(已截断,后文省略)"

    return text
