"""
课表周视图 HTML 生成器（2026-09-21）

输出格式：HTML + 内联 CSS（避免被 Gradio 过滤）
布局：周一-周日（7 列）× 第 1-12 节（12 行）网格
配色：HSV 黄金角分配，每门课按名称 hash 稳定唯一色（参考 Sleepy 算法）
课块：紧凑卡片，显示课程名 + 教师 + 地点 + 周次类型

API:
    render_week_view(courses, current_week=1) -> str  # 周视图 HTML
    render_empty_view() -> str  # 空状态（未上传课表）
    render_summary_text(courses) -> str  # 文本汇总（给小U对话用）
"""
import hashlib
import math
from typing import List, Dict

DAY_NAMES = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]
DAY_SHORT = ["", "一", "二", "三", "四", "五", "六", "日"]

# 课时起止时间（默认，10 节工作日 + 2 节晚自习）
# 格式: (start_time, end_time)
SECTION_TIMES = {
    1: ("08:00", "08:50"), 2: ("09:00", "09:50"), 3: ("10:10", "11:00"),
    4: ("11:10", "12:00"), 5: ("13:30", "14:20"), 6: ("14:30", "15:20"),
    7: ("15:40", "16:30"), 8: ("16:40", "17:30"), 9: ("18:30", "19:20"),
    10: ("19:30", "20:20"), 11: ("20:30", "21:20"), 12: ("21:30", "22:20"),
}

# HSV 黄金角 137.508°
GOLDEN_ANGLE = 137.508


def _course_color(course_name: str) -> str:
    """按课程名生成稳定唯一的 HSL 色（黄金角分配）"""
    if not course_name:
        return "hsl(200, 60%, 75%)"
    # 用课程名的 md5 取字节作为种子
    h = int(hashlib.md5(course_name.encode("utf-8")).hexdigest()[:8], 16)
    hue = (h * GOLDEN_ANGLE) % 360   # 0-360°
    # 保持中等饱和度 + 较高亮度（适合浅色背景）
    return f"hsl({hue:.0f}, 65%, 72%)"


def _text_color(hsl_color: str) -> str:
    """根据背景色亮度返回黑色或白色文字（确保可读性）"""
    # 简化：浅色背景 → 黑色字
    # HSL 中 L > 60% 用黑字，否则用白字
    import re
    m = re.search(r"hsl\((\d+),\s*(\d+)%,\s*(\d+)%\)", hsl_color)
    if m:
        light = int(m.group(3))
        return "#1a1a1a" if light > 60 else "#ffffff"
    return "#1a1a1a"


def _week_label(week_type: str, start_week: int, end_week: int) -> str:
    """生成周次标签"""
    if start_week == 1 and end_week == 20:
        range_str = ""
    else:
        range_str = f"{start_week}-{end_week}周"
    if week_type == "odd":
        return f"{range_str}单周" if range_str else "单周"
    elif week_type == "even":
        return f"{range_str}双周" if range_str else "双周"
    return range_str or ""


def _week_color(week_type: str) -> str:
    """根据单双周返回底色（HSL）"""
    if week_type == "odd":
        return "hsl(330, 65%, 78%)"   # 粉色（单周）
    elif week_type == "even":
        return "hsl(210, 60%, 78%)"   # 蓝色（双周）
    else:
        return "hsl(160, 60%, 78%)"   # 绿色（每周）


def render_week_view(
    courses: List[Dict],
    current_week: int = 1,
) -> str:
    """
    渲染 wakeup 风格课表周视图 HTML（紧凑版）

    布局：
        - 左列窄（46px）：节次 + 时间 08:00-08:50（上下排）
        - 顶行：周一-周日
        - 课程块：占满整个节次格（CSS grid-row: span N 真实跨节）
        - 单周 = 粉色 / 双周 = 蓝色 / 每周 = 绿色
        - 本周 = 加粗不透明；非本周 = 淡化（70% opacity）
        - 课程块底部"本周"/"非本周"标签
    """
    if not courses:
        return render_empty_view()

    valid_courses = [c for c in courses if c.get("day_of_week") and 1 <= c["day_of_week"] <= 7]
    sorted_courses = sorted(valid_courses, key=lambda c: (c["day_of_week"], c["start_section"]))

    css = """
    <style>
    .wxu-cv-wrap {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
        background: linear-gradient(135deg, #faf7ff 0%, #fdf2f8 100%);
        border-radius: 16px;
        padding: 14px;
        box-sizing: border-box;
    }
    .wxu-cv-title {
        font-size: 18px;
        font-weight: 700;
        color: #1f1d2c;
        margin-bottom: 4px;
    }
    .wxu-cv-subtitle {
        font-size: 11px;
        color: #8d889e;
        margin-bottom: 10px;
    }
    .wxu-cv-grid {
        display: grid;
        grid-template-columns: 48px repeat(7, minmax(0, 1fr));
        grid-template-rows: 32px repeat(11, minmax(64px, auto));
        gap: 2px;
        background: rgba(255,255,255,0.45);
        border-radius: 10px;
        padding: 4px;
    }
    .wxu-cv-head-time { background: transparent; }
    .wxu-cv-time-col {
        font-size: 9px;
        color: #9d96b0;
        text-align: right;
        padding: 4px 4px 4px 2px;
        line-height: 1.1;
        background: transparent;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        align-items: flex-end;
    }
    .wxu-cv-time-col b {
        display: block;
        color: #4a4560;
        font-size: 13px;
        font-weight: 700;
        margin-bottom: 2px;
    }
    .wxu-cv-time-col .t-mini {
        font-size: 8.5px;
        color: #9d96b0;
        line-height: 1.15;
    }
    .wxu-cv-day-head {
        text-align: center;
        font-size: 12px;
        font-weight: 600;
        color: #6b6580;
        padding: 4px 2px;
        line-height: 1.2;
        background: transparent;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
    }
    .wxu-cv-day-head.today {
        color: #fff;
        background: #8b5cf6;
        border-radius: 6px;
        font-weight: 700;
    }
    .wxu-cv-cell {
        background: rgba(255,255,255,0.3);
        border-radius: 4px;
        min-height: 60px;
    }
    .wxu-cv-block {
        background: var(--bg);
        color: var(--fg);
        border-radius: 6px;
        padding: 6px 7px;
        font-size: 11px;
        line-height: 1.3;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        overflow: hidden;
        cursor: default;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        margin: 1px;
        min-height: 0;
    }
    .wxu-cv-block.not-this-week {
        opacity: 0.45;
        filter: saturate(0.6);
    }
    .wxu-cv-block-name {
        font-weight: 700;
        font-size: 12px;
        word-break: break-word;
        line-height: 1.18;
        overflow: hidden;
    }
    .wxu-cv-block-meta {
        font-size: 10px;
        opacity: 0.9;
        margin-top: 2px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .wxu-cv-block-week {
        font-size: 9.5px;
        opacity: 0.85;
        margin-top: auto;
        padding: 1px 5px;
        background: rgba(255,255,255,0.4);
        border-radius: 3px;
        align-self: flex-start;
        font-weight: 600;
    }
    .wxu-cv-legend {
        margin-top: 12px;
        font-size: 11px;
        color: #6b6580;
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
        align-items: center;
    }
    .wxu-cv-legend-dot {
        width: 12px;
        height: 12px;
        border-radius: 3px;
        display: inline-block;
        margin-right: 4px;
        vertical-align: middle;
    }
    .wxu-cv-summary {
        background: rgba(255,255,255,0.7);
        border-radius: 8px;
        padding: 8px 12px;
        margin-bottom: 10px;
        font-size: 12px;
        color: #4a4560;
    }
    .wxu-cv-summary b { color: #8b5cf6; }
    </style>
    """

    total_courses = len(sorted_courses)
    summary = (
        f'<div class="wxu-cv-summary">'
        f'共 <b>{total_courses}</b> 节课 · 当前第 <b>{current_week}</b> 周'
        '</div>'
    )

    # 准备 grid 占位
    grid_cells: Dict[Tuple[int, int], Optional[Dict]] = {}
    for sec in range(1, 12):
        for d in range(1, 8):
            grid_cells[(sec, d)] = None

    for c in sorted_courses:
        sec = c["start_section"]
        day = c["day_of_week"]
        if not (1 <= sec <= 11 and 1 <= day <= 7):
            continue
        if grid_cells[(sec, day)] is not None:
            continue
        grid_cells[(sec, day)] = c
        for s in range(sec + 1, c["end_section"] + 1):
            if s <= 11 and (s, day) in grid_cells:
                grid_cells[(s, day)] = "__skip__"

    grid_html = ['<div class="wxu-cv-grid">']

    # 第 1 行：表头
    grid_html.append('<div class="wxu-cv-head-time"></div>')
    for d in range(1, 8):
        is_today = (d == current_week)
        cls = "wxu-cv-day-head today" if is_today else "wxu-cv-day-head"
        grid_html.append(f'<div class="{cls}">{DAY_NAMES[d]}</div>')

    # 第 2-12 行：每节
    for sec in range(1, 12):
        t = SECTION_TIMES.get(sec, ("", ""))
        if t[0]:
            time_html = (
                f'<div class="wxu-cv-time-col">'
                f'<b>{sec}</b>'
                f'<span class="t-mini">{t[0]}<br>{t[1]}</span>'
                f'</div>'
            )
        else:
            time_html = f'<div class="wxu-cv-time-col"><b>{sec}</b></div>'
        grid_html.append(time_html)

        for d in range(1, 8):
            cell = grid_cells.get((sec, d))
            if cell is None:
                grid_html.append('<div class="wxu-cv-cell"></div>')
            elif cell == "__skip__":
                continue
            else:
                c = cell
                bg = _week_color(c["week_type"])
                fg = "#1f1d2c"
                week_label = _week_label(c["week_type"], c["start_week"], c["end_week"])
                location = c.get("location", "")
                teacher = c.get("teacher", "")
                is_this_week = c["start_week"] <= current_week <= c["end_week"]
                if c["week_type"] == "odd" and current_week % 2 == 0:
                    is_this_week = False
                if c["week_type"] == "even" and current_week % 2 == 1:
                    is_this_week = False
                not_this_cls = "" if is_this_week else " not-this-week"
                block_html = (
                    f'<div class="wxu-cv-block{not_this_cls}" '
                    f'style="--bg: {bg}; --fg: {fg}; '
                    f'grid-column: {d + 1}; '
                    f'grid-row: {sec + 1} / span {c["end_section"] - c["start_section"] + 1};">'
                    f'<div class="wxu-cv-block-name">{_html_escape(c["course_name"])}</div>'
                    + (f'<div class="wxu-cv-block-meta">@{_html_escape(location)}</div>' if location else "")
                    + (f'<div class="wxu-cv-block-meta">{_html_escape(teacher)}</div>' if teacher else "")
                    + (f'<div class="wxu-cv-block-week">{"本周" if is_this_week else "非本周"} {week_label}</div>')
                    + "</div>"
                )
                grid_html.append(block_html)

    grid_html.append("</div>")

    legend = (
        '<div class="wxu-cv-legend">'
        '<span><span class="wxu-cv-legend-dot" style="background:hsl(160, 60%, 78%);"></span>每周</span>'
        '<span><span class="wxu-cv-legend-dot" style="background:hsl(330, 65%, 78);"></span>单周</span>'
        '<span><span class="wxu-cv-legend-dot" style="background:hsl(210, 60%, 78%);"></span>双周</span>'
        '<span>淡化 = 非本周</span>'
        + (f'<span style="margin-left:auto;">来源: {_html_escape(", ".join(sorted(set(c.get("source", "?") for c in sorted_courses if c.get("source")))))}</span>' if sorted_courses else "")
        + '</div>'
    )

    html = (
        css
        + '<div class="wxu-cv-wrap">'
        + '<div class="wxu-cv-title">📅 我的课表 · 第 '
        + str(current_week)
        + ' 周</div>'
        + summary
        + "".join(grid_html)
        + legend
        + "</div>"
    )

    return html


def render_standalone_html(courses: List[Dict], current_week: int = 1, title: str = "我的课表") -> str:
    """
    生成完整的、独立可双击打开的 HTML 文件（含 <!DOCTYPE> + 全套 CSS）。
    用于用户下载 .html 后离线查看课表。
    """
    body = render_week_view(courses, current_week)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html_escape(title)}</title>
<style>
  body {{ margin: 0; padding: 16px; background: #faf7ff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif; }}
  .wxu-cv-wrap {{ max-width: 1100px; margin: 0 auto; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def render_empty_view() -> str:
    """空状态：未上传课表"""
    return """
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                text-align: center; padding: 40px 20px;
                background: linear-gradient(135deg, #faf7ff 0%, #fdf2f8 100%);
                border-radius: 16px; color: #6b6580;">
        <div style="font-size: 48px; margin-bottom: 12px;">📅</div>
        <div style="font-size: 16px; font-weight: 600; color: #1f1d2c; margin-bottom: 8px;">
            还没有课表数据
        </div>
        <div style="font-size: 13px; line-height: 1.6;">
            请上传教务系统导出的 <b>课表 PDF</b>，或 <b>课表截图</b>（手机 WakeUp 课程表截图也可以）<br>
            系统会自动识别 → 周视图展示在这里
        </div>
    </div>
    """


def render_summary_text(courses: List[Dict], current_week: int = 1) -> str:
    """
    文本汇总（给小U对话用："今天我上啥？" → 注入 prompt）
    """
    if not courses:
        return "用户尚未上传课表数据。"

    today = current_week if 1 <= current_week <= 7 else 1
    today_courses = [c for c in courses if c["day_of_week"] == today]
    today_courses.sort(key=lambda c: c["start_section"])

    lines = [f"用户已上传 {len(courses)} 节课。以下是第{current_week}周（周{DAY_SHORT[today]}）的课表："]
    for c in today_courses:
        time_range = f"{c['start_section']}-{c['end_section']}节"
        location = c.get("location", "")
        teacher = c.get("teacher", "")
        week_label = _week_label(c["week_type"], c["start_week"], c["end_week"])
        meta_parts = [time_range, teacher]
        if location:
            meta_parts.append(f"@{location}")
        if week_label:
            meta_parts.append(week_label)
        lines.append(f"- {c['course_name']} ({' / '.join(meta_parts)})")
    return "\n".join(lines)


# ============ Gradio 编辑器 schema ============
# Gradio Dataframe 列定义（用户手动编辑课程）
EDITOR_COLUMNS = [
    "课程名",        # course_name
    "教师",          # teacher
    "地点",          # location
    "星期",          # day_of_week (1-7)
    "开始节",        # start_section (1-12)
    "结束节",        # end_section (1-12)
    "开始周",        # start_week (1-25)
    "结束周",        # end_week (1-25)
    "周类型",        # week_type: every/odd/even
]

# 周类型选项（Gradio 下拉框）
WEEK_TYPE_OPTIONS = ["every", "odd", "even"]


def courses_to_editor_rows(courses: List[Dict]) -> List[List]:
    """把 db.load_curriculum() 返回的 List[dict] 转成 Dataframe 行"""
    return [
        [
            c.get("course_name", ""),
            c.get("teacher", ""),
            c.get("location", ""),
            c["day_of_week"],
            c["start_section"],
            c["end_section"],
            c["start_week"],
            c["end_week"],
            c.get("week_type", "every"),
        ]
        for c in courses
    ]


def editor_rows_to_courses(rows: List[List]) -> List[Dict]:
    """Dataframe 行转 List[dict]（用户编辑后保存用）"""
    out = []
    for r in rows:
        if not r or not r[0]:  # 跳过空行
            continue
        try:
            out.append({
                "course_name": str(r[0]).strip(),
                "teacher": str(r[1]).strip() if len(r) > 1 else "",
                "location": str(r[2]).strip() if len(r) > 2 else "",
                "day_of_week": int(r[3]) if len(r) > 3 and r[3] else 1,
                "start_section": int(r[4]) if len(r) > 4 and r[4] else 1,
                "end_section": int(r[5]) if len(r) > 5 and r[5] else int(r[4]),
                "start_week": int(r[6]) if len(r) > 6 and r[6] else 1,
                "end_week": int(r[7]) if len(r) > 7 and r[7] else 20,
                "week_type": str(r[8]).strip() if len(r) > 8 and r[8] else "every",
            })
        except (ValueError, TypeError):
            continue
    return out


def render_parsed_preview(parsed: List[Dict], unparsed: List[str]) -> str:
    """解析结果预览（用户确认用）"""
    html = '<div style="font-family: -apple-system, sans-serif; padding: 12px; background: #faf7ff; border-radius: 12px;">'
    html += f'<div style="font-weight: 600; color: #1f1d2c; margin-bottom: 8px;">解析结果: 成功 {len(parsed)} 条'
    if unparsed:
        html += f' / 未识别 {len(unparsed)} 条'
    html += '</div>'

    if parsed:
        html += '<div style="margin-bottom: 12px;"><b style="color: #047857;">✅ 已识别（可保存）</b></div>'
        for p in parsed[:15]:   # 最多 15 条
            html += (
                f'<div style="padding: 6px 10px; margin-bottom: 4px; background: white; '
                f'border-radius: 6px; font-size: 12px; border-left: 3px solid #8b5cf6;">'
                f'<b>{_html_escape(p["course_name"])}</b> · '
                f'{_html_escape(p.get("teacher", "")) or "?"} · '
                f'@{_html_escape(p.get("location", "")) or "?"} · '
                f'周{p["day_of_week"]} 第{p["start_section"]}-{p["end_section"]}节 · '
                f'{_week_label(p["week_type"], p["start_week"], p["end_week"]) or "每周"}'
                '</div>'
            )
        if len(parsed) > 15:
            html += f'<div style="color: #6b6580; font-size: 11px;">... 还有 {len(parsed)-15} 条</div>'

    if unparsed:
        html += '<div style="margin-top: 12px;"><b style="color: #b91c1c;">⚠️ 未识别（建议手动添加）</b></div>'
        for line in unparsed[:5]:
            html += (
                f'<div style="padding: 4px 8px; background: #fef2f2; border-radius: 4px; '
                f'font-size: 11px; font-family: monospace; margin-bottom: 2px;">'
                f'{_html_escape(line)}</div>'
            )
    html += '</div>'
    return html


def render_raw_text_preview(raw_text: str, file_name: str) -> str:
    """OCR 截图识别后的原始文本（截图模式不强解析时显示）"""
    return (
        f'<div style="font-family: -apple-system, sans-serif; padding: 14px; '
        f'background: #fff7ed; border: 1px solid #fb923c; border-radius: 12px;">'
        f'<div style="font-weight: 600; color: #9a3412; margin-bottom: 6px;">'
        f'⚠️ OCR 识别提示（截图识别可能被分成多行）</div>'
        f'<div style="color: #6b6580; font-size: 11px; margin-bottom: 10px;">'
        f'来源: {_html_escape(file_name)} · 截图 OCR 准确率有限，建议手动添加课程（下方表格）</div>'
        f'<pre style="background: #fafafa; padding: 10px; border-radius: 6px; '
        f'font-size: 11px; max-height: 200px; overflow-y: auto; white-space: pre-wrap; '
        f'word-break: break-all;">{_html_escape(raw_text[:2000])}'
        f'{"...(截断)" if len(raw_text) > 2000 else ""}</pre>'
        f'</div>'
    )


def _html_escape(s: str) -> str:
    """HTML 转义"""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#39;"))


# 模块自测
if __name__ == "__main__":
    test_courses = [
        {"course_name": "马克思主义基本原理", "teacher": "刘岚", "location": "D4思政楼201",
         "day_of_week": 2, "start_section": 3, "end_section": 4,
         "start_week": 1, "end_week": 16, "week_type": "odd"},
        {"course_name": "数字信号处理", "teacher": "于淼", "location": "C3敏学楼412",
         "day_of_week": 2, "start_section": 5, "end_section": 6,
         "start_week": 1, "end_week": 16, "week_type": "every"},
        {"course_name": "Python程序设计", "teacher": "陈豪", "location": "C1敏学楼203",
         "day_of_week": 2, "start_section": 9, "end_section": 10,
         "start_week": 1, "end_week": 16, "week_type": "every"},
        {"course_name": "FPGA", "teacher": "单面", "location": "C1敏学楼314",
         "day_of_week": 1, "start_section": 5, "end_section": 6,
         "start_week": 1, "end_week": 16, "week_type": "odd"},
        {"course_name": "现代传感器技术", "teacher": "陈技", "location": "C1敏学楼",
         "day_of_week": 1, "start_section": 5, "end_section": 6,
         "start_week": 1, "end_week": 16, "week_type": "odd"},
    ]
    print("=== render_week_view ===")
    print(render_week_view(test_courses, current_week=2))
    print("\n=== render_summary_text ===")
    print(render_summary_text(test_courses, current_week=2))