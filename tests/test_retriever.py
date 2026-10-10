# -*- coding: utf-8 -*-
"""
检索器测试（2026-10-09 加）

只测 import + callable，不实际跑检索（跑检索要 FAISS 索引 + Ollama）。
"""
from src.rag.retriever import create_retriever


def test_create_retriever_is_callable():
    """create_retriever 应是一个可调用对象"""
    assert callable(create_retriever)


def test_create_retriever_signature_has_use_reranker():
    """create_retriever 应支持 use_reranker 参数（生产关 reranker 的开关）"""
    import inspect
    sig = inspect.signature(create_retriever)
    assert "use_reranker" in sig.parameters
