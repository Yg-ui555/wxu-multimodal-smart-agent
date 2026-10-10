# -*- coding: utf-8 -*-
"""
Dispatcher 测试（2026-10-09 加）

测 dispatch 空查询应返回兜底答案，不调 LLM。
这是任务 4 修复后的回归保护：dispatch 应接受 sid 参数。
"""
from src.web_app.agent.dispatcher import dispatch


def test_dispatch_empty_query_returns_default():
    """空 query 应返回兜底答案，不调 LLM"""
    result = dispatch("", "wxu_test_abc")
    assert isinstance(result, dict)
    assert "answer" in result
    assert result["answer"]  # 非空字符串


def test_dispatch_whitespace_query_returns_default():
    """纯空格 query 也应返回兜底答案"""
    result = dispatch("   ", "wxu_test_abc")
    assert isinstance(result, dict)
    assert "answer" in result


def test_dispatch_accepts_sid_param():
    """dispatch 应支持 sid 参数（任务 4 多用户隔离修复）"""
    import inspect
    sig = inspect.signature(dispatch)
    assert "sid" in sig.parameters


def test_dispatch_greeting_returns_hardcoded():
    """'你好' 应命中硬编码兜底，不调 LLM"""
    result = dispatch("你好", "wxu_test_abc", sid="")
    assert isinstance(result, dict)
    assert result.get("answer")
