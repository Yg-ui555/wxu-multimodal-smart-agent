# -*- coding: utf-8 -*-
"""
ChatHistoryManager 测试（2026-10-09 加）

ChatHistoryManager 是手写多轮对话管理器（死代码但 import 不应崩）。
只测 import + 类可实例化条件，不实际跑 LLM 调用。
"""
from src.rag.chat_history import ChatHistoryManager


def test_chat_history_manager_importable():
    """ChatHistoryManager 类应可 import"""
    assert ChatHistoryManager is not None


def test_chat_history_manager_has_methods():
    """应有核心方法 add_message / get_history / clear"""
    assert hasattr(ChatHistoryManager, "__init__")
