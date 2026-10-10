# -*- coding: utf-8 -*-
"""
LLM 链路纯函数测试（2026-10-09 加）

测两个不依赖 LLM 的纯函数：
- format_history_for_prompt([]) 应返回空字符串
- clean_answer("#### hello") 应去掉 # 装饰符号
"""
from src.rag.llm_chain import format_history_for_prompt, clean_answer


def test_format_history_empty_returns_empty():
    """空历史应返回空字符串（不是 None，不是异常）"""
    assert format_history_for_prompt([]) == ""


def test_format_history_none_returns_empty():
    """传 None 也应安全返回空字符串"""
    assert format_history_for_prompt(None) == ""


def test_clean_answer_strips_hash_decorations():
    """清洗应去掉 #### 这种装饰符号"""
    out = clean_answer("#### hello world ####")
    assert "#" not in out
    assert "hello" in out


def test_clean_answer_handles_empty():
    """空字符串输入应安全返回"""
    assert clean_answer("") == ""
