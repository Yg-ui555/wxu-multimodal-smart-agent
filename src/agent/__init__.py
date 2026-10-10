"""
校园 Agent 模块(2026-10-09 新增)
基于 LangGraph ReAct 风格 + 4 个真 tool 的对话引擎

模块:
    react_engine        ReAct 主循环(非流式 + 流式)
    tools               4 个 tool 的实现
"""
from src.agent.react_engine import agent_run, agent_stream

__all__ = ["agent_run", "agent_stream"]
