"""
课表 PDF 解析器 v2（2026-09-22，pdfplumber 方案）
借鉴 Al-rimi/Timetable-to-Calendar 思路，用 PDF 表格线识别
替代 PyMuPDF 坐标方案

策略：
    1. pdfplumber extract_tables(vertical_strategy="lines", horizontal_strategy="lines")
       自动按表格线把页面切成 row × col 矩阵
    2. 找含"周一-周日"列的表头 → 锁定 day col 序号
    4. 每张表（page 1/page 2/page 3）独立处理
       - 有"周一-周日"表头 → 7 day 列锁定
       - 无表头 → 按 col 序号启发式 day 推断（col 2 → 周一, col 3 → 周二 ...）
    5. 每个 (row, col) cell 按 `▲` `(X-Y节)` 双 marker 分块 → 提取课程
"""

import re
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ============ 节次时间表（高校通用，硬编码） ============
SECTION_TIME = [
    "08:00-08:50", "09:00-09:50", "10:10-11:00", "11:10-12:00",
    "13:30-14:20", "14:30-15:20", "15:40-16:30", "16:40-17:30",
    "18:30-19:20", "19:30-20:20", "20:30-21:20",
]
DAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
DAY_NAMES = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ============ Type markers (Al-rimi 思路) ============
TYPE_MARKERS = ["▲", "★", "△", "☆"]


def _clean_cell(cell: str) -> str:
    """清理 cell 文本（保留换行供下游分块）"""
    if not cell:
        return ""
    cell = cell.replace("\r", "\n")
    cell = "\n".join([ln.strip() for ln in cell.split("\n") if ln.strip()])
    return cell


def _find_header_row(rows: List[List[str]]) -> Optional[int]:
    """找含"周一-周日"的表头行索引（参考 Al-rimi find_header_idx）"""
    cn_days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日",
               "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    for i, row in enumerate(rows):
        joined = " ".join(c or "" for c in row)
        day_hits = sum(1 for d in cn_days if d in joined)
        # 表头特征：≥3 个 day 关键词 + 节次/上午/下午关键词
        if day_hits >= 3 and any(kw in joined for kw in ["节", "节次", "上午", "下午", "晚上", "时间段"]):
            return i
    return None


def _identify_day_columns(headers: List[str]) -> Dict[int, int]:
    """
    识别表头中"周一-周日"对应的列索引
    返回: {day_of_week: col_index}
    """
    cn_day_map = {
        "周一": 1, "星期一": 1,
        "周二": 2, "星期二": 2,
        "周三": 3, "星期三": 3,
        "周四": 4, "星期四": 4,
        "周五": 5, "星期五": 5,
        "周六": 6, "星期六": 6,
        "周日": 7, "星期日": 7,
    }
    result = {}
    for col_idx, h in enumerate(headers):
        h = (h or "").strip()
        for key, day_num in cn_day_map.items():
            if key in h and day_num not in result:
                result[day_num] = col_idx
                break
    return result


def _split_cell_by_markers(cell_text: str) -> List[str]:
    """
    按 type_marker (▲/★/△/☆) 把 cell 文本分成多个课程块
    每个 marker 是新课程的起点（Al-rimi split_blocks_smart 思路）
    Block 范围：marker 行 + 之前 1-3 行课程名 + 之后到下一个 marker
    """
    cell_text = _clean_cell(cell_text)
    if not cell_text:
        return []
    lines = [ln for ln in cell_text.split("\n") if ln.strip()]
    if not lines:
        return []

    # 找所有 marker 的索引
    marker_lines = [i for i, ln in enumerate(lines) if any(m in ln for m in TYPE_MARKERS)]
    if not marker_lines:
        return []

    blocks = []
    for mi, marker_idx in enumerate(marker_lines):
        # 从 marker 行向前回溯找课程名（最多 4 行）
        start = marker_idx
        prev_marker = marker_lines[mi - 1] if mi > 0 else -1
        j = marker_idx - 1
        while j > prev_marker and j >= 0 and (marker_idx - j) <= 4:
            txt = lines[j]
            if _is_metadata_line(txt):
                break
            if any(m in txt for m in TYPE_MARKERS):
                break
            start = j
            j -= 1
        # 到下一个 marker 或行末
        end = marker_lines[mi + 1] if mi + 1 < len(marker_lines) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        if block:
            blocks.append(block)
    return blocks


def _is_metadata_line(line: str) -> bool:
    """判断一行是否是元数据（非课程名）"""
    if not line:
        return True
    # 跳过纯数字、纯符号
    if re.match(r'^[\d\s、,\-\(\)\.:/]+$', line):
        return True
    # 跳过含元数据关键词的行
    meta_kws = ["周", "节", "场地", "教师", "教学班", "考核", "学时", "学分", "组成", "选课", "备注", ":"]
    if any(kw in line for kw in meta_kws):
        return True
    return False


def _parse_course_block(block_text: str, fallback_section: Optional[int] = None) -> Optional[Dict]:
    """从单个课程块提取结构化信息"""
    block_text = _clean_cell(block_text)
    if not block_text:
        return None

    # 1. 课程名 = block 内第一个 marker 行的 prefix + 向前 1-3 行找完整名
    lines = [l for l in block_text.split("\n") if l.strip()]
    course_name = ""
    # 找第一个 marker 行
    first_marker_idx = None
    for i, ln in enumerate(lines):
        if any(m in ln for m in TYPE_MARKERS):
            first_marker_idx = i
            break

    if first_marker_idx is not None:
        # 取 marker 行的 prefix
        marker_line = lines[first_marker_idx]
        for m in TYPE_MARKERS:
            if m in marker_line:
                course_name = marker_line.split(m)[0].strip()
                break
        # 向前找更完整的课程名（marker 前 1-3 行，非元数据中文）
        for j in range(first_marker_idx - 1, max(-1, first_marker_idx - 4), -1):
            txt = lines[j]
            if not _is_metadata_line(txt):
                # 拼接课程名（注意中文字符可能跨行被切）
                course_name = txt + course_name

    if not course_name:
        # fallback：找第一行非元数据中文
        for ln in lines:
            if not _is_metadata_line(ln):
                course_name = ln.strip()
                break

    if not course_name or len(course_name) < 2:
        return None

    # 2. 节次（X-Y节）
    sec_m = re.search(r'\((\d+)(?:-(\d+))?节\)', block_text)
    if sec_m:
        start_sec = int(sec_m.group(1))
        end_sec = int(sec_m.group(2)) if sec_m.group(2) else start_sec
    elif fallback_section:
        start_sec = end_sec = fallback_section
    else:
        return None

    # 3. 周次（多段合并：1-6周,8-10周 → start=1, end=10）
    week_type = "every"
    if "(单)" in block_text or "单周" in block_text:
        week_type = "odd"
    elif "(双)" in block_text or "双周" in block_text:
        week_type = "even"

    start_week, end_week = 1, 20
    # 所有 X-Y周 范围
    range_pat = re.findall(r'(\d+)\s*[-—~]\s*(\d+)\s*周', block_text)
    # 单段周次（如 "14周"）
    single_weeks = []
    for m in re.finditer(r'(\d+)\s*周', block_text):
        # 排除范围（如 "1-12周" 里的 1 和 12）
        before = block_text[max(0, m.start() - 1):m.start()]
        if before in ('-', '—', '~', ' '):
            continue
        single_weeks.append(int(m.group(1)))

    if range_pat:
        all_starts = [int(s) for s, e in range_pat]
        all_ends = [int(e) for s, e in range_pat]
        start_week = min(all_starts + single_weeks) if single_weeks else min(all_starts)
        end_week = max(all_ends + single_weeks) if single_weeks else max(all_ends)
    elif single_weeks:
        start_week, end_week = min(single_weeks), max(single_weeks)

    # 4. 场地
    location = ""
    loc_m = re.search(r'场地:([^/\n]+)', block_text)
    if loc_m:
        location = loc_m.group(1).strip()

    # 5. 教师
    teacher = ""
    teacher_m = re.search(r'教师:([^/\n]+)', block_text)
    if teacher_m:
        teacher = teacher_m.group(1).strip()

    return {
        "course_name": course_name,
        "teacher": teacher,
        "location": location,
        "start_section": start_sec,
        "end_section": end_sec,
        "start_week": start_week,
        "end_week": end_week,
        "week_type": week_type,
        "raw_text": block_text[:200],
    }


def parse_table(rows: List[List[str]], day_columns: Dict[int, int],
                has_day_header: bool, table_idx: int) -> List[Dict]:
    """
    从一张表（含 day 列映射）提取所有课程

    Args:
        rows: 表格 row × col 矩阵
        day_columns: {day_of_week: col_index}
        has_day_header: True=page 1 表头明确，False=page 2/3 启发式
        table_idx: 表序号（用于 page 2/3 启发式 day 推断）

    Returns: List[Dict] 课程
    """
    courses = []
    seen = set()

    for row in rows:
        if len(row) < 3:
            continue
        # 节次数字（col 1）
        sec_text = (row[1] or "").strip() if len(row) > 1 else ""
        sec_num = None
        m_sec = re.match(r'^(\d+)$', sec_text)
        if m_sec:
            sec_num = int(m_sec.group(1))
        if not sec_num:
            continue

        for day_num, col_idx in day_columns.items():
            if col_idx >= len(row):
                continue
            cell_text = row[col_idx] or ""
            cell_text = _clean_cell(cell_text)
            if not cell_text:
                continue

            # 按 marker 分块
            blocks = _split_cell_by_markers(cell_text)
            for block in blocks:
                course = _parse_course_block(block, fallback_section=sec_num)
                if course:
                    course["day_of_week"] = day_num
                    key = (day_num, course["start_section"], course["course_name"])
                    if key not in seen:
                        seen.add(key)
                        courses.append(course)

    return courses


def parse_pdf_with_pdfplumber(pdf_path: str) -> List[Dict]:
    """
    Main entry: pdfplumber parse PDF to course list.
    """
    all_courses = []
    seen = set()

    with pdfplumber.open(pdf_path) as pdf:
        for pi, page in enumerate(pdf.pages):
            tables = page.extract_tables({
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
            })
            for ti, raw_table in enumerate(tables):
                if not raw_table:
                    continue
                # 清理所有 cell
                cleaned = [[_clean_cell(c) for c in (row or [])] for row in raw_table]
                # 找表头
                hdr_idx = _find_header_row(cleaned)
                if hdr_idx is not None:
                    # 有表头：用表头锁定 day col
                    headers = cleaned[hdr_idx]
                    day_cols = _identify_day_columns(headers)
                    if not day_cols:
                        continue
                    data_rows = cleaned[hdr_idx + 1:]
                else:
                    # 无表头（page 2/3）：按 col 顺序推断 day
                    # col 2 = 周一, col 3 = 周二, ..., col 8 = 周日
                    n_cols = max(len(r) for r in cleaned) if cleaned else 0
                    day_cols = {}
                    for day_num in range(1, 8):
                        col_idx = 1 + day_num  # col 2 = 周一
                        if col_idx < n_cols:
                            day_cols[day_num] = col_idx
                    if not day_cols:
                        continue
                    data_rows = cleaned

                # 解析这张表的课程
                courses = parse_table(data_rows, day_cols, has_day_header=(hdr_idx is not None), table_idx=ti)
                for c in courses:
                    c["page_idx"] = pi
                    key = (c["day_of_week"] or 0, c["start_section"], c["course_name"], pi)
                    if key not in seen:
                        seen.add(key)
                        all_courses.append(c)

    return all_courses


# ============ 模块自测 ============
if __name__ == "__main__":
    import os
    test_pdfs = [
        r"C:\Users\Lenovo\.minimax\v2\assets\2026\09\22\15-25-27-469-asset_20260922-152527-469_8fabbb0ed8dd_2f766b67-马欣宇(2025-2026-2)课表.pdf",
        r"C:\Users\Lenovo\.minimax\v2\assets\2026\09\22\15-25-27-481-asset_20260922-152527-481_99756e2f2db8_cba9f0ec-马欣宇(2024-2025-1)课表.pdf",
        r"C:\Users\Lenovo\.minimax\v2\assets\2026\09\22\15-25-27-495-asset_20260922-152527-495_c541496278c3_c8df5d06-马欣宇(2024-2025-2)课表.pdf",
        r"C:\Users\Lenovo\.minimax\v2\assets\2026\09\22\15-25-27-511-asset_20260922-152527-511_216fee8583e9_f234b134-马欣宇(2025-2026-1)课表.pdf",
    ]

    for pdf in test_pdfs:
        if not os.path.exists(pdf):
            print(f"[NOT FOUND] {pdf}")
            continue
        print(f"\n{'='*70}")
        print(f"PDF: {os.path.basename(pdf)}")
        print(f"{'='*70}")
        courses = parse_pdf_with_pdfplumber(pdf)
        # 按 page, day, section 排序
        courses.sort(key=lambda c: (c.get("page_idx", 0), c["day_of_week"] or 0, c["start_section"]))
        print(f"解析出 {len(courses)} 节课:")
        for c in courses:
            day_str = f"周{DAY_NAMES[c['day_of_week']][1]}" if c["day_of_week"] else "日?"
            week_str = f"{c['start_week']}-{c['end_week']}周"
            if c['week_type'] == 'odd':
                week_str += "(单)"
            elif c['week_type'] == 'even':
                week_str += "(双)"
            print(f"  P{c.get('page_idx',0)+1} {day_str:>3} 第{c['start_section']}-{c['end_section']}节 | {c['course_name'][:20]:20s} | {c['teacher']:8s} | {c['location']:15s} | {week_str}")