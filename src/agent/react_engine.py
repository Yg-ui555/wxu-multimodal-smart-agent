"""
ReAct 引擎(2026-10-09 新增)
基于 LangChain tool calling + 手动循环实现的"思考 + 行动"Agent。

流程:
    user_query
      ↓
    LLM 思考: "要不要调 tool?"
      ↓ 调
    真的执行 tool → 拿到结果
      ↓
    LLM 再思考: "够了吗?还要不要再调?"
      ↓ 调 / 不调
    最终出答案

设计选择:
    - 不引 LangGraph(重),手写 30 行循环(轻、调试方便、性能好)
    - LLM 用 DeepSeek(便宜 + 中文好 + tool calling 支持)
    - 异常统一降级:任何 tool 抛异常都返回 "(失败: ...)" 不让 Agent 崩
    - max_iter=5 防 LLM 调 tool 上瘾
    - 单 tool 结果截断 4000 字符,防 token 爆炸
"""
import json
import logging
import os
import time
from typing import Iterator, List, Dict, Any

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

# 加载 .env(放最前,LLM 初始化要读 API key)
load_dotenv()

# 4 个 tool
from src.agent.tools import ALL_TOOLS, TOOL_MAP

# context 注入 setter
from src.agent.tools.get_user_schedule import set_current_uid
from src.agent.tools.ocr_image import set_current_image

# Prometheus 埋点(软依赖,没装 prometheus_client 也不影响 Agent)
from src.agent.metrics import observe_tool_call

log = logging.getLogger("agent.react_engine")

# ============================================================
# 1) System Prompt
# ============================================================
# 这是给 LLM 的"操作手册",告诉它:
#   - 它是谁(校园助手)
#   - 能调什么 tool(自动从 ALL_TOOLS 拼)
#   - 工作流程(think → act → observe → think ...)
#   - 关键规则(基于工具结果回答,不要瞎编)
SYSTEM_PROMPT = """你是无锡学院的智能校园助手,可以用工具帮学生解决问题。

## 你有以下工具可用
{tool_descriptions}

## 工作流程
1. 看完用户问题,先想:"要不要查什么?"
2. 如果要查,调对应 tool(一次 1-2 个,别贪多)
3. 拿到结果再想:"够了吗?还要不要再调别的?"
4. 觉得够了,直接用中文给最终答案(3-5 句,简洁)

## 重要规则
- **必须**基于工具结果回答,不能瞎编(尤其是奖学金截止日、考试时间)
- 引用资料时直接说"根据 XX 通知..."即可,**不要**自己编 JSON / sources 块
- 涉及试卷/答案/真题等保密内容,礼貌拒绝
- 工具失败时换个思路(比如 RAG 没命中就调 web_search),不要反复调同一个失败的 tool
- 不要废话(像"我来帮您查一下"这种),直接给结果

## 关于"我是谁"这类闲聊
不用调 tool,直接用你自己的知识简短回答。
"""


def _build_tool_descriptions() -> str:
    """把所有 tool 的 name + description 拼成一段,塞进 system prompt"""
    lines = []
    for t in ALL_TOOLS:
        lines.append(f"- **{t.name}**: {t.description}")
    return "\n".join(lines)


# ============================================================
# 2) 初始化 LLM
# ============================================================
# 用项目同款 .env 配置(LLM_API_KEY / LLM_BASE_URL / LLM_MODEL)
# 复用 dispatcher.py 已经验证过的 ChatOpenAI + DeepSeek 组合
_llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    temperature=0.3,  # 偏低,让 LLM 决策更稳定,别瞎调 tool
    api_key=os.getenv("LLM_API_KEY", ""),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    timeout=30.0,
)

# bind_tools 把 4 个 tool 的 schema 注入 LLM
# LLM 看到后,输出会多一个 tool_calls 字段(结构化 JSON: name + args)
_llm_with_tools = _llm.bind_tools(ALL_TOOLS)


# ============================================================
# 3) ReAct 主循环 - 非流式版(完整跑完才返回)
# ============================================================
def agent_run(user_query: str,
              history: List[Dict] | None = None,
              uid: str = "",
              max_iter: int = 5) -> Dict[str, Any]:
    """跑完整 ReAct 循环。

    Args:
        user_query: 当前用户问题
        history: 多轮对话历史,格式 [{"role": "user/assistant", "content": "..."}]
        uid: 当前用户 ID(注入到 get_user_schedule tool 的 context)
        max_iter: 最大循环次数(防 LLM 调 tool 上瘾)

    Returns:
        {
            "answer": 最终答案(str),
            "tool_calls_log": [{"tool": "search_school_notices", "args": {...}}, ...],
            "iterations": 实际跑了多少轮,
        }
    """
    # 0. context 注入(让 get_user_schedule / ocr_image 知道当前用户和图片)
    #    2026-10-30:改到"紧邻 tool.invoke()"处设置(见下面的 2.3.1 / 2.5.1)。
    #    这里提前设一次在并发 + 流式下不可靠 —— Starlette 会把 sync generator
    #    拆到不同线程/上下文跑,请求入口设的值到 tool 执行时可能已经不是本次的了。

    # 1. 构造 messages 列表
    messages = [SystemMessage(content=SYSTEM_PROMPT.format(
        tool_descriptions=_build_tool_descriptions(),
    ))]
    for h in (history or []):
        if h.get("role") == "user" and h.get("content"):
            messages.append(HumanMessage(content=str(h["content"])))
        elif h.get("role") == "assistant" and h.get("content"):
            messages.append(AIMessage(content=str(h["content"])))
    messages.append(HumanMessage(content=user_query))

    tool_calls_log: List[Dict] = []

    # 2. ReAct 主循环
    for iteration in range(max_iter):
        log.info(f"[Agent] iter={iteration + 1}/{max_iter}, messages={len(messages)}")

        # 2.1 LLM 决定下一步(可能调 tool,可能出答案)
        try:
            ai_msg = _llm_with_tools.invoke(messages)
        except Exception as e:
            log.exception("LLM 调用失败")
            return {
                "answer": f"(LLM 调用失败: {type(e).__name__}: {e})",
                "tool_calls_log": tool_calls_log,
                "iterations": iteration + 1,
            }

        messages.append(ai_msg)

        # 2.2 没调 tool → LLM 直接给答案,结束循环
        if not ai_msg.tool_calls:
            return {
                "answer": ai_msg.content or "(LLM 没生成答案)",
                "tool_calls_log": tool_calls_log,
                "iterations": iteration + 1,
            }

        # 2.3 调 tool(可能一次调多个,挨个执行)
        for call in ai_msg.tool_calls:
            tool_name = call.get("name", "")
            tool_args = call.get("args", {})
            call_id = call.get("id", "")

            log.info(f"[Agent] → {tool_name}({tool_args})")
            tool_calls_log.append({"tool": tool_name, "args": tool_args})

            # 2.3.1 执行 tool
            if tool_name not in TOOL_MAP:
                result = f"(未知工具: {tool_name},你只能调: {list(TOOL_MAP.keys())})"
            else:
                t_start = time.time()
                try:
                    # ★ 紧邻 invoke 设 uid —— 见 get_user_schedule 里的说明:
                    #   contextvars 只在"设值 + 读值"处于同一段代码时可靠,
                    #   所以不能只在请求入口设一次。
                    set_current_uid(uid)
                    result = TOOL_MAP[tool_name].invoke(tool_args)
                except Exception as e:
                    log.exception(f"tool {tool_name} 失败")
                    result = f"(工具执行失败: {type(e).__name__}: {e})"
                finally:
                    observe_tool_call(tool_name, time.time() - t_start)

            # 2.3.2 截断防 token 爆炸
            if isinstance(result, str) and len(result) > 4000:
                result = result[:4000] + "\n...(已截断)"

            log.info(f"[Agent] ← {tool_name} 返回 {len(str(result))} 字符")

            # 2.3.3 把 tool 结果塞回 messages,LLM 下次能看到
            messages.append(ToolMessage(
                content=str(result),
                tool_call_id=call_id,
            ))

    # 3. 超 max_iter → 强制结束
    log.warning(f"[Agent] 超出 max_iter={max_iter},强制结束")
    return {
        "answer": "(抱歉,我尝试了多次都没能完整回答,建议你换个更具体的问题试试)",
        "tool_calls_log": tool_calls_log,
        "iterations": max_iter,
    }


# ============================================================
# 4) ReAct 流式版 - 适配现有 SSE 前端
# ============================================================
def agent_stream(user_query: str,
                 history: List[Dict] | None = None,
                 uid: str = "",
                 image_path: str = "",
                 max_iter: int = 5) -> Iterator[str]:
    """流式版:每生成一点 token 就 yield 出去,前端边收边渲染。

    Yields:
        str: 增量文本片段(LLM 答案 token + 工具调用提示)

    协议:
        - 普通 text:  "data: {... 'text': chunk}"
        - 工具调用:  "...[🔧 正在调 XX]..."  (前端的 "[🔧" 标记可做特殊渲染)
    """
    # 0. context 注入
    #    2026-10-30:改到"紧邻 tool.invoke()"处设置(见下面的 2.5.1),
    #    原因同 agent_run —— 流式下请求入口设的值不可靠。

    # 1. 构造 messages
    messages = [SystemMessage(content=SYSTEM_PROMPT.format(
        tool_descriptions=_build_tool_descriptions(),
    ))]
    for h in (history or []):
        if h.get("role") == "user" and h.get("content"):
            messages.append(HumanMessage(content=str(h["content"])))
        elif h.get("role") == "assistant" and h.get("content"):
            messages.append(AIMessage(content=str(h["content"])))
    messages.append(HumanMessage(content=user_query))

    # 2. 主循环
    #
    # 设计选择(2026-10-09 修复):
    # 之前用 _llm_with_tools.stream() 想边拿 token 边 yield,但发现:
    #   - DeepSeek stream 模式下,tool_calls 的 args 字段会被吞掉(只剩 {})
    #   - tool name + id 是分块出来的,args 是空 dict,直接传给 AIMessage 报错
    #   - 累积逻辑复杂且不可靠
    #
    # 改为:用 invoke() 拿完整 ai_msg(tool_calls 完整),再手动把 content
    #      拆成 5-10 字一段伪流式 yield(用户体验差不多,代码简单可靠)
    for iteration in range(max_iter):
        # 2.1 一次性 invoke 拿完整 ai_msg
        try:
            ai_msg = _llm_with_tools.invoke(messages)
        except Exception as e:
            yield f"\n\n(LLM 调用失败: {type(e).__name__}: {e})"
            return

        # 2.2 把 ai_msg 加到 messages(让下一轮能看到 LLM 决策)
        messages.append(ai_msg)

        # 2.3 伪流式 yield content(LLM 已经想完了,前端看着像流式)
        # 注意:不能用 yield _fake_stream(...) — 那会 yield 一个 generator 对象,不是字符串
        if ai_msg.content:
            import time as _t
            text = ai_msg.content
            for i in range(0, len(text), 5):
                yield text[i:i + 5]
                _t.sleep(0.03)

        # 2.4 没调 tool → LLM 觉得够了,流式结束
        if not ai_msg.tool_calls:
            return

        # 2.5 调 tool 并 yield 工具调用提示
        for call in ai_msg.tool_calls:
            tool_name = call.get("name", "")
            tool_args = call.get("args", {}) or {}
            call_id = call.get("id", "")

            # yield 工具调用提示(前端可以做"AI 正在用 XX 工具"展示)
            args_show = json.dumps(tool_args, ensure_ascii=False) if tool_args else ""
            yield f"\n\n[🔧 正在调 {tool_name}({args_show})...]\n\n"
            log.info(f"[Agent stream] → {tool_name}({tool_args})")

            # 2.5.1 执行
            if tool_name not in TOOL_MAP:
                result = f"(未知工具: {tool_name})"
            else:
                t_start = time.time()
                try:
                    # ★ 紧邻 invoke 设 context(uid / 图片路径)。
                    #   流式路径下 Starlette 把 sync generator 拆到不同线程跑,
                    #   只有在这里设值才能保证跟这次 invoke 在同一上下文。
                    set_current_uid(uid)
                    set_current_image(image_path)
                    result = TOOL_MAP[tool_name].invoke(tool_args)
                except Exception as e:
                    result = f"(工具执行失败: {type(e).__name__}: {e})"
                finally:
                    observe_tool_call(tool_name, time.time() - t_start)

            # 2.5.2 截断
            if isinstance(result, str) and len(result) > 4000:
                result = result[:4000] + "\n...(已截断)"

            # 2.5.3 塞回 messages
            messages.append(ToolMessage(
                content=str(result),
                tool_call_id=call_id,
            ))

    # 3. 超 max_iter
    yield "\n\n(超出最大步数,已强制结束)"
