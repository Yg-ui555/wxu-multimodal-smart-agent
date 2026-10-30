"""
LangGraph 工业级 ReAct Agent —— 真·状态机版(2026-10-11 第 2 版)

════════════════════════════════════════════════════════════════════
📚 本文件讲什么(用大白话讲)
════════════════════════════════════════════════════════════════════
你已经有 src/agent/react_engine.py 在跑 ReAct(30 行循环)。
LangGraph 干的是同一件事,**但把这段循环"画成一张流程图"** 让
LangGraph 库按图执行。两个功能一样,画成图能带来 4 个新能力:

    1. 卡在哪里看得见      → 每个节点跑完写"日记"(trace)
    2. 状态自动存盘        → SqliteSaver 落盘,程序崩了下次接上
    3. 中途可以暂停/审核    → interrupt_before("final_answer")(可选)
    4. 真实时事件流        → graph.stream() 让前端真看到"打字机"

════════════════════════════════════════════════════════════════════
🔁 跟 react_engine 的关系
════════════════════════════════════════════════════════════════════
    - 共存:.env 里 USE_LANGGRAPH_AGENT=1/0 切换(默认 0,即 react_engine)
    - 行为对齐:同 LLM + 同 tool + 同 prompt(从 react_engine 复用)
    - 增量能力:hitl + 持久化 + trace(这三个 react_engine 没有)

════════════════════════════════════════════════════════════════════
💡 面试高频问点
════════════════════════════════════════════════════════════════════
    Q: langgraph 跟手写循环的区别?
    A: 状态图、checkpointer、interrupt、stream_mode 这四样是手写循环做不到的。

    Q: 什么时候用哪个?
    A: 需要持久化/hitl/multi-agent 用 langgraph,否则 react_engine。

    Q: checkpointer 怎么落盘?
    A: 默认 SqliteSaver 写到 data/langgraph_checkpoints.db;生产换 PostgresSaver。

    Q: 怎么 debug 一次跑挂了?
    A: 看 logs/langgraph_trace.jsonl(每行一条完整 trace),或在
       .env 设 LANGCHAIN_TRACING_V2=true 上 LangSmith 看图。

════════════════════════════════════════════════════════════════════
"""

# ───────────────────────────────────────────────────────────────────
# 标准库
# ───────────────────────────────────────────────────────────────────
import os
import json
import time
import logging
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import TypedDict, List, Dict, Any, Optional, Iterator, Literal

# ───────────────────────────────────────────────────────────────────
# 第三方
# ───────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
try:
    from langgraph.checkpoint.sqlite import SqliteSaver  # 落盘用(可选)
    from langgraph.checkpoint.sqlite.aio import aiosqlite  # SqliteSaver 依赖
    _HAS_SQLITE = True
except ImportError:
    _HAS_SQLITE = False

load_dotenv()

# ───────────────────────────────────────────────────────────────────
# sys.path:让 `python src/agent/langgraph_engine.py` 自测时能找到 src 包
# ───────────────────────────────────────────────────────────────────
import sys as _sys
from pathlib import Path as _Path
_PROJ_ROOT = str(_Path(__file__).resolve().parent.parent.parent)
if _PROJ_ROOT not in _sys.path:
    _sys.path.insert(0, _PROJ_ROOT)

# ───────────────────────────────────────────────────────────────────
# 项目内(tool 注册)
# ───────────────────────────────────────────────────────────────────
from src.agent.tools import ALL_TOOLS, TOOL_MAP
from src.agent.tools.get_user_schedule import set_current_uid
from src.agent.tools.ocr_image import set_current_image

# Prometheus 埋点(软依赖,没装 prometheus_client 也不影响 Agent)
from src.agent.metrics import observe_tool_call

log = logging.getLogger("agent.langgraph_engine")

# ═══════════════════════════════════════════════════════════════════
# 1) LLM 初始化 —— 跟 react_engine 完全相同(行为对齐)
# ═══════════════════════════════════════════════════════════════════
_llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    temperature=0.3,
    api_key=os.getenv("LLM_API_KEY", ""),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    timeout=30.0,
)
_llm_with_tools = _llm.bind_tools(ALL_TOOLS)


def _get_react_system_prompt() -> str:
    """复用 react_engine 的 SYSTEM_PROMPT —— 字面完全一致,行为才不漂移"""
    from src.agent.react_engine import SYSTEM_PROMPT as REACT_SYSTEM_PROMPT
    return REACT_SYSTEM_PROMPT


def _build_tool_descriptions() -> str:
    """拼所有 tool 的 name + description,塞进 system prompt"""
    return "\n".join(f"- **{t.name}**: {t.description}" for t in ALL_TOOLS)


# ═══════════════════════════════════════════════════════════════════
# 2) 状态定义 —— 整个图共享这一份"数据"
# ═══════════════════════════════════════════════════════════════════
class AgentState(TypedDict, total=False):
    """
    图跑的时候所有"会变"的数据。

    字段:
        messages       完整消息历史(system + human + ai + tool)
        iteration      当前轮数(防死循环,默认上限 5)
        finished       LLM 是否已经给出最终答案
        final_answer   最终答案文本(给前端 SSE 用)
        started_at     epoch 秒(用来算每个节点的延迟)
        trace          每个节点跑完追加一条"日记"
        node_visits    每个节点访问次数(debug 用)
    """
    messages: List
    iteration: int
    finished: bool
    final_answer: str
    started_at: float
    trace: List[Dict[str, Any]]
    node_visits: Dict[str, int]
    # 2026-10-30 新增:当前请求的用户身份 + 图片路径。
    # 工具节点执行前要把它们注入到 contextvars,否则 get_user_schedule
    # 拿不到 uid(工具节点是模块级函数,拿不到闭包变量)。
    uid: str
    image_path: str


def _now() -> float:
    """统一拿当前时间(将来测试时方便 mock)"""
    return time.time()


def _trace_record(state: AgentState, node: str, event: str = "") -> AgentState:
    """
    在 state.trace 追加一条记录,返回**新** state(LangGraph 要求节点返回新 dict,
    不能 in-place 修改,否则隔壁接到的是旧 dict)。

    为什么这么严: LangGraph 内部对 state 做引用比较 + 增量更新,
    in-place 修改会被当成"无变化",造成 trace 漏写。
    """
    trace = list(state.get("trace", []))
    trace.append({
        "node": node,
        "event": event,
        "t_ms": int((_now() - state.get("started_at", _now())) * 1000),
        "msg_count": len(state.get("messages", [])),
    })
    return {**state, "trace": trace}


# ═══════════════════════════════════════════════════════════════════
# 3) 三个节点
# ═══════════════════════════════════════════════════════════════════
# 节点 = 图上的一个方框。每个节点就是一个 Python 函数,签名 (state) -> 新 state

# ───────────────────────────────────────────────────────────────────
# 节点 1: call_model_node —— "让 LLM 想下一步"
# ───────────────────────────────────────────────────────────────────
def call_model_node(state: AgentState) -> AgentState:
    """
    图上的第一个方框。让 LLM 看 messages,决定:
        a) 调某个工具 → finished=False,继续走 tools 节点
        b) 给最终答案 → finished=True + final_answer 写答案,走 final_answer 节点

    完全对应 react_engine 循环里的 `ai_msg = llm.invoke(messages)` 那一步。
    """
    log.info(f"[LG] call_model iter={state.get('iteration', 0)}")

    try:
        ai_msg = _llm_with_tools.invoke(state["messages"])
    except Exception as e:
        log.exception("[LG] LLM 调用失败")
        ai_msg = AIMessage(content=f"(LLM 调用失败:{type(e).__name__}:{e})")

    new_messages = list(state["messages"]) + [ai_msg]

    # LLM 没调 tool_calls → 觉得够了,触发 finished
    finished = not (hasattr(ai_msg, "tool_calls") and ai_msg.tool_calls)

    visits = dict(state.get("node_visits", {}))
    visits["agent"] = visits.get("agent", 0) + 1

    new_state: AgentState = {
        **state,
        "messages": new_messages,
        "finished": finished,
        "final_answer": ai_msg.content if finished else "",
        "node_visits": visits,
    }
    return _trace_record(new_state, "agent", "llm_decided")


# ───────────────────────────────────────────────────────────────────
# 节点 2: tool_executor_node —— "LLM 说了啥,真的去干"
# ───────────────────────────────────────────────────────────────────
def tool_executor_node(state: AgentState) -> AgentState:
    """
    图上的第二个方框。看 LLM 最后那条消息的 tool_calls,挨个跑 tool。

    完全对应 react_engine 循环里 `for call in ai_msg.tool_calls: TOOL_MAP[...].invoke(...)` 那段。

    关键防护(跟 react_engine 一致,避免 Agent 整体崩):
        - 未知工具 → 降级字符串 "(未知工具:XX)"
        - tool 抛异常 → 降级字符串 "(工具失败:...)"
        - result 太长(>4000 字) → 截断防 token 爆炸

    每个工具调用还往 trace 里写一条记录:
        "{tool_name} {ok/error} {latency_ms}ms args={...truncated...}"
    debug 时这一行非常有用 —— 看是哪条 tool 慢了 / 失败了。
    """
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", []) or []

    log.info(f"[LG] tools 节点:跑 {len(tool_calls)} 个 tool")

    new_messages = list(state["messages"])
    visits = dict(state.get("node_visits", {}))
    visits["tools"] = visits.get("tools", 0) + 1

    # 边跑边更新 trace(为了一次性返回新 state)
    trace_work = list(state.get("trace", []))
    work_state: AgentState = {**state, "node_visits": visits, "trace": trace_work}

    for call in tool_calls:
        name = call.get("name", "")
        args = call.get("args", {}) or {}
        cid = call.get("id", "")
        t_start = _now()

        if name not in TOOL_MAP:
            result = f"(未知工具:{name},你只能调:{list(TOOL_MAP.keys())})"
            status = "unknown_tool"
        else:
            try:
                # ★ 紧邻 invoke 设 context(uid / 图片路径)。
                #   uid 从 state 里取 —— 工具节点是模块级函数,拿不到闭包变量。
                #   必须在 invoke 之前设,contextvars 只在同一段代码里才可靠。
                set_current_uid(state.get("uid", ""))
                set_current_image(state.get("image_path", ""))
                result = TOOL_MAP[name].invoke(args)
                status = "ok"
            except Exception as e:
                log.exception(f"[LG] tool {name} 失败")
                result = f"(工具执行失败:{type(e).__name__}:{e})"
                status = "error"
        t_cost = int((_now() - t_start) * 1000)

        # Prometheus:工具调用次数 + 延迟(unknown_tool 不算真实调用)
        if status != "unknown_tool":
            observe_tool_call(name, t_cost / 1000.0)

        if isinstance(result, str) and len(result) > 4000:
            result = result[:4000] + "\n...(已截断)"

        # 把 tool 结果塞回 messages,让 LLM 下轮能看到
        new_messages.append(ToolMessage(content=str(result), tool_call_id=cid))

        # 更新 trace
        trace_work.append({
            "node": "tools",
            "event": f"{name} {status} {t_cost}ms args={json.dumps(args, ensure_ascii=False)[:100]}",
            "t_ms": int((_now() - state.get("started_at", _now())) * 1000),
            "msg_count": len(new_messages),
        })
        work_state = {**work_state, "trace": trace_work}

    return {
        **work_state,
        "messages": new_messages,
        "iteration": state.get("iteration", 0) + 1,
        "finished": False,
        "final_answer": "",
    }


# ───────────────────────────────────────────────────────────────────
# 节点 3: final_answer_node —— "答前停一下等管理员看" (可选,默认无人审核)
# ───────────────────────────────────────────────────────────────────
def final_answer_node(state: AgentState) -> AgentState:
    """
    第 3 个方框(也是 LangGraph 相对手写循环的"卖点之一":可中断的人工审核点)。

    默认行为:这个节点是个"通风格子",把 state 原样返回给 END,无人审核。

    启用审核:.env 加 INTERRUPT_BEFORE_HITL=1,LangGraph 会**在跑到这个
    节点之前暂停**,等程序主动调 graph.invoke(None, config=...) 才会继续。
    真实用法是给个后台管理界面,管理员 approve 一下才往下走。

    你们项目目前用不到这功能,但接口留着,以后想做"敏感回答审核"直接用。
    """
    log.info(f"[LG] final_answer 节点(sid={state.get('started_at')!r}),final_answer 长度={len(state.get('final_answer', '') or '')}")
    return _trace_record(state, "final_answer", "human_approval_point_default_pass_through")


# ═══════════════════════════════════════════════════════════════════
# 4) 路由:should_continue —— 三向分流
# ═══════════════════════════════════════════════════════════════════
def should_continue(state: AgentState) -> Literal["tools", "final_answer"]:
    """
    LangGraph "条件箭头"的路由函数。看完 state,决定从 "agent" 节点跑完后去哪儿:

        finished=True                 → "final_answer"
        iteration >= 5                → "final_answer"(防死循环)
        LLM 最后一条没调 tool_calls   → "final_answer"
        其余(LLM 想调 tool)          → "tools"

    返回 "tools" / "final_answer" 两个字符串字面量,对应图里的箭头,
    LangGraph 看到哪个就走哪个节点。
    """
    if state.get("finished"):
        log.info(f"[LG] router → final_answer (finished)")
        return "final_answer"

    if state.get("iteration", 0) >= 5:
        log.warning(f"[LG] router → final_answer (超 max_iter)")
        return "final_answer"

    last = state["messages"][-1] if state.get("messages") else None
    if last and not (hasattr(last, "tool_calls") and last.tool_calls):
        log.info(f"[LG] router → final_answer (LLM 没调 tool)")
        return "final_answer"

    log.info(f"[LG] router → tools")
    return "tools"


# ═══════════════════════════════════════════════════════════════════
# 5) 拼图函数 —— 把节点 + 箭头 + checkpointer 拼成能跑的图
# ═══════════════════════════════════════════════════════════════════
def build_agent_graph():
    """
    ★ 这是 LangGraph 最值钱的一段代码 ★

    流程图:
        ┌──────────┐
        │  START   │
        └────┬─────┘
             ↓
        ┌────────────┐
        │   agent    │  ← 让 LLM 思考
        └────┬───────┘
             ↓ (看 router)
       ┌─────┴──────┐
       ↓            ↓
   ┌────────┐  ┌──────────────┐
   │ tools  │  │ final_answer │  ← 可挂 hitl
   └────┬───┘  └──────┬───────┘
        ↓             ↓
       (回 agent)    (END)

    四种关键调用:
        - add_node(name, fn)         注册方框
        - set_entry_point(name)      设入口
        - add_edge(from, to)         加无条件箭头
        - add_conditional_edges(fn)  加条件箭头(router 决定走哪)
    """
    workflow = StateGraph(AgentState)

    # 1. 注册 3 个方框
    workflow.add_node("agent", call_model_node)
    workflow.add_node("tools", tool_executor_node)
    workflow.add_node("final_answer", final_answer_node)

    # 2. 设入口
    workflow.set_entry_point("agent")

    # 3. 条件箭头:agent 跑完看 router 决定去哪
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "final_answer": "final_answer",
        }
    )

    # 4. 无条件箭头
    workflow.add_edge("tools", "agent")             # 工具跑完回 agent(让 LLM 看 tool 结果)
    workflow.add_edge("final_answer", END)          # 答完结束

    # ───────────────────────────────────────────────────────────────
    # 5. 装上 checkpointer —— LangGraph 的"状态自动存盘"能力
    # ───────────────────────────────────────────────────────────────
    # checkpointer 是个"小本子",每跑过一个节点 LangGraph 自动把当前 state 写进去。
    # 用途:
    #   a) 程序崩了,下次能从上次接上(掉电恢复)
    #   b) 能做 time-travel 调试(checkpoint_id=XXX 回滚到第 N 步重跑)
    #   c) 配合 interrupt 做"对话暂停,以后接着问"
    #
    # 两种实现:
    #   - MemorySaver:小本子放内存,适合调试,程序关就没
    #   - SqliteSaver:小本子写到 SQLite 文件,生产首选
    use_memory = os.getenv("LANGGRAPH_USE_MEMORY_CHECKPOINTER") == "1"

    if use_memory:
        checkpointer = MemorySaver()
        log.info("[LG] checkpointer = MemorySaver(内存)")
    elif _HAS_SQLITE:
        ckpt_path = "data/langgraph_checkpoints.db"
        Path(ckpt_path).parent.mkdir(parents=True, exist_ok=True)
        # SqliteSaver.from_conn_string 官方推荐用法
        checkpointer = SqliteSaver.from_conn_string(f"sqlite:///{ckpt_path}")
        log.info(f"[LG] checkpointer = SqliteSaver 落盘({ckpt_path})")
    else:
        checkpointer = MemorySaver()
        log.warning("[LG] SqliteSaver 不可用,降级 MemorySaver")

    # ───────────────────────────────────────────────────────────────
    # 6. 可选:hitl 中断点
    # ───────────────────────────────────────────────────────────────
    # 默认无人审核,interrupt_before 空。
    # 设 INTERRUPT_BEFORE_HITL=1 后,跑到 final_answer 节点之前会暂停,
    # 等外部 approve 再继续。
    interrupt_before: Optional[List[str]] = None
    if os.getenv("INTERRUPT_BEFORE_HITL") == "1":
        interrupt_before = ["final_answer"]
        log.info("[LG] hitl 开启:final_answer 前暂停")

    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
    )


# ═══════════════════════════════════════════════════════════════════
# 6) 单例图缓存 —— 每 (uid, sid) 一张图,修复"撞 key"bug
# ═══════════════════════════════════════════════════════════════════
# 修第一版的 bug:
#     第一版用 {uid}-{user_query[:20]} 当 thread_id,导致同一对话不同问题
#     可能撞 key,checkpointer 状态互相覆盖。
#
# 现在用 {uid}::{sid} 当 thread_id,跟会话 ID 绑定,稳。
# 同时缓存"每 (uid,sid) 一张图",避免每次调用都重新编译。
_graph_cache: "OrderedDict[str, Any]" = OrderedDict()
_graph_lock = threading.Lock()
# 2026-10-30:图缓存原来是无上限 dict,每个新 (uid, sid) 都会编译一张新图并
# 常驻内存 —— 匿名用户每来一个新会话就多一张图,跑久了内存只涨不落。
# 改成 LRU:超过上限就淘汰最久没用的。
_GRAPH_CACHE_MAX = int(os.getenv("LANGGRAPH_GRAPH_CACHE_MAX", "64"))


def _make_thread_id(uid: str, sid: str, user_query: str = "") -> str:
    """生成 checkpointer 的 thread_id。

    正常路径:"{uid}::{sid}" —— sid 是会话 ID,稳定且唯一。
    sid 为空(评测脚本 / 内部直接调 agent_run):用随机后缀,
    **不能**退化成 user_query[:8] —— 不同问题前 8 个字相同就会撞 key,
    checkpointer 里的状态互相覆盖(第一版就是这么错的)。
    """
    base = uid or "anon"
    if sid:
        return f"{base}::{sid}"
    return f"{base}::noid-{uuid.uuid4().hex[:12]}"


def get_graph(uid: str = "", sid: str = "") -> Any:
    """
    取一张对应 (uid, sid) 的图。不存在就编译一张缓存起来。

    注:thread_id 在 agent_run / agent_stream 里另外传,这里只是
    保证多用户多会话各自一份图对象(避免 checkpointer 互相冲突)。
    """
    cache_key = f"{uid or 'anon'}::{sid or 'no_sid'}"
    with _graph_lock:
        if cache_key in _graph_cache:
            _graph_cache.move_to_end(cache_key)
            return _graph_cache[cache_key]
        g = build_agent_graph()
        _graph_cache[cache_key] = g
        # LRU 淘汰:超出上限就把最久没用的那张丢掉
        while len(_graph_cache) > _GRAPH_CACHE_MAX:
            old_key, _ = _graph_cache.popitem(last=False)
            log.info(f"[LG] 图缓存超上限({_GRAPH_CACHE_MAX}),淘汰:key={old_key}")
        log.info(f"[LG] 编译新图:key={cache_key},checkpointer 已装")
        return g


def clear_graph_cache() -> None:
    """清空图缓存(测试/重置用,别在生产主动调)"""
    with _graph_lock:
        _graph_cache.clear()


# ═══════════════════════════════════════════════════════════════════
# 7) 辅助函数
# ═══════════════════════════════════════════════════════════════════
def _collect_tool_calls_log(messages: List) -> List[Dict[str, Any]]:
    """从 messages 里反推:这次跑调了哪些工具、参数是啥"""
    out = []
    for m in messages:
        if hasattr(m, "tool_calls") and m.tool_calls:
            for call in m.tool_calls:
                out.append({
                    "tool": call.get("name"),
                    "args": call.get("args", {}),
                })
    return out


def _dump_trace_to_jsonl(state: AgentState, sid: str, query: str, cost_ms: int) -> None:
    """
    把本次 trace 写到 logs/langgraph_trace.jsonl(每行一条 JSON,append-only)。
    """
    log_path = Path("logs/langgraph_trace.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sid": sid,
        "query": query[:80],
        "iterations": state.get("iteration", 0),
        "cost_ms": cost_ms,
        "node_visits": state.get("node_visits", {}),
        "trace": state.get("trace", []),
        "final_answer_len": len(state.get("final_answer", "") or ""),
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════════════
# 8) 对外接口 —— 跟 react_engine 完全对齐
# ═══════════════════════════════════════════════════════════════════

def _init_state(user_query: str,
                history: Optional[List[Dict]] = None,
                uid: str = "",
                image_path: str = "") -> AgentState:
    """
    构造"刚启动时的初始 state"。

    messages = [system_prompt] + 历史对话 + 当前问题

    uid / image_path 也放进 state:工具节点是模块级函数,拿不到闭包变量,
    只能从 state 里取(见 tool_executor_node)。
    """
    sys_prompt = _get_react_system_prompt().format(tool_descriptions=_build_tool_descriptions())
    messages: List = [SystemMessage(content=sys_prompt)]
    for h in (history or []):
        if not isinstance(h, dict):
            continue
        role = h.get("role")
        content = h.get("content")
        if not content:
            continue
        content_str = str(content)
        if role == "user":
            messages.append(HumanMessage(content=content_str))
        elif role == "assistant":
            messages.append(AIMessage(content=content_str))
    messages.append(HumanMessage(content=str(user_query)))

    return {
        "messages": messages,
        "iteration": 0,
        "finished": False,
        "final_answer": "",
        "started_at": _now(),
        "trace": [],
        "node_visits": {},
        "uid": uid or "",
        "image_path": image_path or "",
    }


def agent_run(user_query: str,
              history: Optional[List[Dict]] = None,
              uid: str = "",
              sid: str = "",
              max_iter: int = 5) -> Dict[str, Any]:
    """
    非流式版 ReAct Agent(给评测脚本这种"等结果"的场景用)。

    跟 react_engine.agent_run 接口一致,多了一个 sid 参数(LangGraph 用它
    做 thread_id/checkpointer 隔离)。

    Returns:
        {
          "answer":         str,              最终答案
          "tool_calls_log": [{"tool","args"}],
          "iterations":     int,              跑了几轮
          "trace":          [...],            节点 trace(给前端 trace UI 用)
          "cost_ms":        int,              这次跑总耗时
        }
    """
    # ★ 用 uid::sid 作 thread_id,sid 由 dispatcher 传过来(每个 session 一个)
    #   sid 为空时(评测脚本直接调)用随机后缀,否则不同问题前 8 个字相同就会
    #   撞 checkpointer key,状态互相覆盖。
    thread_id = _make_thread_id(uid, sid, user_query)
    graph = get_graph(uid, sid)

    init = _init_state(user_query, history, uid=uid)
    config = {"configurable": {"thread_id": thread_id}}

    t0 = _now()
    final_state = graph.invoke(init, config=config)
    cost_ms = int((_now() - t0) * 1000)

    # 提取答案(final_answer 优先,fallback 最后一条 AIMessage)
    answer = final_state.get("final_answer", "") or ""
    if not answer:
        for m in reversed(final_state.get("messages", [])):
            if isinstance(m, AIMessage) and m.content:
                answer = m.content
                break
    if not answer:
        answer = "(LLM 没生成答案)"

    # 写 trace(debug 用,前端可读)
    try:
        _dump_trace_to_jsonl(final_state, sid, user_query, cost_ms)
    except Exception as e:
        log.warning(f"[LG] trace dump 失败: {e!r}")

    return {
        "answer": answer,
        "tool_calls_log": _collect_tool_calls_log(final_state.get("messages", [])),
        "iterations": final_state.get("iteration", 0),
        "trace": final_state.get("trace", []),
        "cost_ms": cost_ms,
    }


def agent_stream(user_query: str,
                 history: Optional[List[Dict]] = None,
                 uid: str = "",
                 sid: str = "",
                 image_path: str = "",
                 max_iter: int = 5) -> Iterator[str]:
    """
    流式版(给前端 SSE 用,边生成边吐)。

    ★ 相对第一版改进 ★
        第一版:跑完 invoke() 再手动 5 字一吐 → "伪流式"
        这一版:用 graph.stream(stream_mode="updates") 拿真实时事件
              LangGraph 节点跑完时,会主动 push 一个 event 出来,
              我们直接 yield 即可。

    事件分类:
        - agent 节点 final_answer 更新   → 吐答案增量(像打字机)
        - agent 节点 messages 更新       → 吐 "[🔧 正在调 XX({args})...]"
        - tools 节点 trace 更新          → 吐工具执行细节
        - final_answer 节点进来         → 吐 "[hitrace]=1" 标记
    """
    thread_id = _make_thread_id(uid, sid, user_query)
    graph = get_graph(uid, sid)

    init = _init_state(user_query, history, uid=uid, image_path=image_path)
    config = {"configurable": {"thread_id": thread_id}}

    # graph.stream(mode="updates") 每个事件:{"<node_name>": {"<state_key>": new_value, ...}}
    for event in graph.stream(init, config=config, stream_mode="updates"):
        for node_name, state_delta in event.items():
            if node_name == "agent":
                # LLM 给最终答案了
                fa = state_delta.get("final_answer")
                if fa:
                    for i in range(0, len(fa), 5):
                        yield fa[i:i + 5]
                        time.sleep(0.03)
                # LLM 决定调工具
                msgs = state_delta.get("messages", [])
                if msgs:
                    last_msg = msgs[-1]
                    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                        for call in last_msg.tool_calls:
                            args_show = json.dumps(call.get("args", {}), ensure_ascii=False)
                            yield f"\n\n[🔧 正在调 {call['name']}({args_show})...]\n\n"
            elif node_name == "tools":
                trace_delta = state_delta.get("trace", [])
                if trace_delta:
                    last_event = trace_delta[-1]
                    ev = last_event.get("event", "")
                    if ev:
                        yield f"<tool-trace>{ev}</tool-trace>\n"
            elif node_name == "final_answer":
                yield "\n\n[hitrace]=1 (答完审核点)\n\n"


# ═══════════════════════════════════════════════════════════════════
# 9) 自测 —— 跑这个文件直接验证
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    """
    跑法: python src/agent/langgraph_engine.py

    预期看到 4 道题(Q1-Q4),每道给出:
      - 答案 (answer[:80] 摘要)
      - 调了哪些 tool (tool_calls)
      - 跑了几轮 (iterations)
      - 耗时 (cost_ms)
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    test_cases = [
        ("你好", "small_talk"),
        ("今天上啥课", "schedule"),
        ("今天无锡天气怎么样", "weather"),
        ("奖学金怎么申请", "kb"),
    ]

    print("═══════════════════════════════════════════════════════════")
    print(" LangGraph 自测 (如果你看到这个就是导入成功)")
    print("═══════════════════════════════════════════════════════════\n")

    for query, intent_label in test_cases:
        print(f"=== Q[{intent_label}]: {query} ===")
        try:
            r = agent_run(user_query=query, uid="test-uid", sid=f"test-sid-{intent_label}")
            print(f"  answer      : {r['answer'][:80]}")
            print(f"  tool_calls  : {[c['tool'] for c in r['tool_calls_log']]}")
            print(f"  iterations  : {r['iterations']}")
            print(f"  cost_ms     : {r['cost_ms']}")
            print(f"  trace 节点数: {len(r['trace'])}")
        except Exception as e:
            print(f"  ❌ 异常: {type(e).__name__}: {e}")
        print()

    # 看 trace 文件(如果前面跑成功,这里会有记录)
    trace_file = Path("logs/langgraph_trace.jsonl")
    if trace_file.exists():
        print("═══════════════════════════════════════════════════════════")
        print(f" Trace 文件 logs/langgraph_trace.jsonl ({trace_file.stat().st_size} bytes):")
        print("═══════════════════════════════════════════════════════════")
        lines = trace_file.read_text(encoding="utf-8").strip().split("\n")
        print(f"  累计 {len(lines)} 条 trace(这次跑了 {len(test_cases)} 条)\n")
        for line in lines[-len(test_cases):]:
            try:
                rec = json.loads(line)
                print(f"  · {rec['time']} [{rec['sid']}] {rec['query']} → "
                      f"iters={rec['iterations']} cost={rec['cost_ms']}ms "
                      f"visits={rec['node_visits']}")
            except Exception:
                pass
