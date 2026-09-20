"""OCR 模块：PaddleOCR 封装 + 自动入向量库"""
from .ocr_engine import (
    extract_text,
    add_ocr_to_vectorstore,
)

__all__ = [
    "extract_text",
    "add_ocr_to_vectorstore",
]