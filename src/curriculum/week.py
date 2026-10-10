"""
当前周计算（2026-10-09 新增）
根据用户开学日 + 今天, 算"今天是第几周"。

设计原则：
    - 简单直接:delta_days // 7 + 1
    - 边界保护:开学前返回 1(还没开学), 超过 25 周限制为 25
    - 可单测:接受 today 参数方便测试

为什么单独一个文件:
    - 周次判断会被 /schedule 页面、/api/chat 主动服务、cron 提醒等地方复用
    - 抽出来一处改, 处处生效
"""
from datetime import date
from typing import Optional

# 一学期最多 25 周(对应你的项目现状:ui.py 里 range(1, 21) 是 1-20,
# 留点余量 25)
MAX_WEEKS = 25
DEFAULT_WEEK = 1


def compute_current_week(semester_start_date: str,
                         today: Optional[date] = None) -> int:
    """
    根据开学日期 + 今天，算出今天是第几周

    Args:
        semester_start_date: 开学日期字符串，格式 "YYYY-MM-DD"
        today: 今天的日期（默认 date.today()，单测时可传假日期）

    Returns:
        1-25 之间的整数

    规则：
        - 开学当天 = 第 1 周
        - 开学后 0-6 天 = 第 1 周
        - 开学后 7-13 天 = 第 2 周
        - 开学前(还没开学) = 第 1 周
        - 超过 25 周 = 25
    """
    if not semester_start_date:
        return DEFAULT_WEEK

    try:
        start = date.fromisoformat(semester_start_date)
    except (ValueError, TypeError):
        return DEFAULT_WEEK

    if today is None:
        today = date.today()

    delta_days = (today - start).days
    if delta_days < 0:
        return DEFAULT_WEEK   # 还没开学, 默认显示第 1 周

    week = delta_days // 7 + 1
    return min(week, MAX_WEEKS)
