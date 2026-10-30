"""
Tool 3: ocr_image
作用:用 PaddleOCR 识别图片里的文字(对接你已经封装好的 ocr_engine)
关键设计:任何 tool 失败都不能让 Agent 崩,统一返回降级字符串

2026-10-30 安全修复(两个):
  1) 路径不再由 LLM 传。原来签名是 ocr_image(image_path: str),
     LLM 只要写个绝对路径就能让后端去读任意文件(比如 .env / 源码 / 密钥),
     再把内容 OCR 出来回显给用户 —— 这是标准的"任意文件读取"原语。
     现在改成零参数 tool,图片路径只能由后端注入,LLM 无权指定。
  2) 注入的路径还要再过一道白名单:必须落在项目 data/ 目录内。
"""
import logging
from contextvars import ContextVar
from pathlib import Path

from langchain_core.tools import tool

# 复用现有 OCR
from src.ocr.ocr_engine import extract_text

log = logging.getLogger("agent.tools.ocr_image")

# 上下文注入:图片路径(类似 get_user_schedule 的 uid 注入)
#
# 2026-10-30 修复:原来是模块级可变 dict,FastAPI 并发下会串号 ——
#   用户 A 上传的图片路径会被用户 B 的请求覆盖,导致 A 看到 B 的图。
#   改用 contextvars.ContextVar,值跟着"当前执行上下文"走。
_current_image: ContextVar[str] = ContextVar("wxu_current_image", default="")

# 项目根: src/agent/tools/ocr_image.py -> parents[3] == 项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
# 允许 OCR 的根目录(相对项目根)。超出白名单一律拒绝。
_ALLOWED_ROOTS = ("data",)


def set_current_image(path: str) -> None:
    """ReAct 引擎在调 OCR 前,把当前会话的图片路径注入进来

    注意:必须在**紧邻 tool.invoke() 之前**调用。流式路径下 Starlette 会把
    sync generator 拆到不同线程/上下文里跑,只有"设值 + 读值"发生在
    同一段代码里才保证一致。
    """
    _current_image.set(path or "")


def _get_current_image() -> str:
    return _current_image.get()


def _resolve_allowed(path: str) -> Path | None:
    """把图片路径限制在白名单目录内,越界 / 不存在 / 非文件都返回 None"""
    if not path:
        return None
    try:
        p = Path(path).resolve()
    except Exception:  # 非法路径字符
        return None
    for rel in _ALLOWED_ROOTS:
        root = (_PROJECT_ROOT / rel).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            continue
        return p if p.is_file() else None
    return None


@tool
def ocr_image() -> str:
    """用 OCR 识别当前会话上传的图片里的文字内容。

    不需要任何参数:图片由后端注入(用户刚在「上传中心」传的那张),
    你只要调用即可。识别结果会以纯文本返回。

    Returns:
        识别出的文字内容(去除空行的纯文本)。如果失败会返回降级消息。
    """
    # 1. 只认后端注入的路径,LLM 不能指定
    raw_path = _get_current_image()
    if not raw_path:
        return "(当前会话没有上传图片。请先让用户去「上传中心」上传图片再试)"

    p = _resolve_allowed(raw_path)
    if p is None:
        log.warning("ocr_image 路径被白名单拒绝: %s", raw_path)
        return "(图片路径不合法或不在允许范围内,已拒绝读取)"

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
