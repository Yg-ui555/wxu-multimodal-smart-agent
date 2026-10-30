"""
Tool 2: get_user_schedule
作用:查用户的课表(today / tomorrow / this_week / weekday_N)
关键设计:uid 通过 context 注入(避免函数参数被 LLM 误传)
"""
import logging
from contextvars import ContextVar
from datetime import date

from langchain_core.tools import tool

# 复用现有模块
from src.curriculum import db as cv_db

log = logging.getLogger("agent.tools.get_user_schedule")

# ===== context 注入机制 =====
# LLM 看到的 tool schema 只暴露 "scope" 参数(uid 由后端注入,不让 LLM 传)
# 实际调用时由 ReAct 引擎在 invoke 前 set_current_uid() 一次
#
# 2026-10-30 修复:原来是模块级可变 dict,FastAPI 并发下会串号 ——
#   用户 A 的请求跑到一半,用户 B 的请求改写了同一个 dict,
#   A 调 get_user_schedule 时拿到的却是 B 的 uid(跨用户数据泄露)。
#   改用 contextvars.ContextVar:值跟着"当前执行上下文"走,
#   每个请求/每个线程各自一份,互不干扰。
_current_uid: ContextVar[str] = ContextVar("wxu_current_uid", default="")


def set_current_uid(uid: str) -> None:
    """ReAct 引擎在每次 tool 调用前调用此函数注入当前用户的 uid

    注意:必须在**紧邻 tool.invoke() 之前**调用,不要只在请求入口调一次。
    流式路径下 Starlette 会把 sync generator 拆到不同线程/上下文里跑,
    只有"设值 + 读值"发生在同一段代码里才保证一致。
    """
    _current_uid.set(uid or "")


def _get_current_uid() -> str:
    return _current_uid.get()


# 周几中文映射(LLM 看 scope="weekday_3" 也知道是周三)
_DOW_NAME = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


@tool
def get_user_schedule(scope: str = "today") -> str:
    """查询当前登录用户的课表。

    Args:
        scope: 查询范围,可选值:
               - "today"  今天
               - "tomorrow"  明天
               - "this_week"  本周一到周日
               - "next_week"  下周(课表按周复用,逻辑同 this_week)
               - "weekday_1" 到 "weekday_7"  指定的星期几(1=周一,7=周日)

    Returns:
        格式化好的课表文字,每行一门课,例:"周三 第3-4节 高等数学(教A101) 任课:张老师"
        如果用户没登录或没上传课表,会返回明确的提示。
    """
    uid = _get_current_uid()
    if not uid:
        return "(用户未登录,无法查课表)"

    # 1. 加载课表(复用你已有的 cv_db.load_curriculum)
    try:
        courses = cv_db.load_curriculum(uid)
    except Exception as e:
        log.exception("加载课表失败")
        return f"(加载课表失败: {e})"

    if not courses:
        return "(该用户还没上传课表,请提示他去「上传中心」上传 PDF 课表)"

    # 2. 解析 scope,得到目标 day_of_week 集合
    today_dow = date.today().isoweekday()  # 1=周一

    if scope == "today":
        target_days = {today_dow}
        scope_label = "今天"
    elif scope == "tomorrow":
        target_days = {(today_dow % 7) + 1}
        scope_label = "明天"
    elif scope == "this_week":
        target_days = set(range(1, 8))
        scope_label = "本周"
    elif scope == "next_week":
        target_days = set(range(1, 8))
        scope_label = "下周"
    elif scope.startswith("weekday_"):
        try:
            n = int(scope.split("_")[1])
            if not 1 <= n <= 7:
                return f"(weekday_N 的 N 必须在 1-7 之间,你传了 {n})"
            target_days = {n}
            scope_label = _DOW_NAME[n]
        except (ValueError, IndexError):
            return f"(scope 格式错误: {scope},应该是 weekday_1 到 weekday_7)"
    else:
        return (f"(未知 scope: {scope}。"
                f"请用 today / tomorrow / this_week / next_week / weekday_N)")

    # 3. 过滤 + 排序(LLM 看着舒服)
    filtered = [c for c in courses if c.get("day_of_week") in target_days]
    if not filtered:
        return f"({scope_label}没课,休息一下)"

    filtered.sort(key=lambda c: (c["day_of_week"], c.get("start_section", 0)))

    # 4. 格式化(中文,每行一门课)
    lines = [f"📅 {scope_label}的课表:"]
    for c in filtered:
        line = f"  {_DOW_NAME[c['day_of_week']]} 第{c['start_section']}-{c['end_section']}节 {c['course_name']}"
        if c.get("location"):
            line += f" ({c['location']})"
        if c.get("teacher"):
            line += f" 任课:{c['teacher']}"
        lines.append(line)
    return "\n".join(lines)
