"""
课表 PDF 坐标解析器 v2（2026-09-22 重大重构 v3）
PyMuPDF 坐标 → 按"星期X"行分天 + 按 (X-Y节) marker 分课程

v3 关键改进：
    1. y_tolerance 从 5 → 30，合并课程名 + 详情到同一 row
    2. 分块策略改用 (X-Y节) marker 替代 x gap
    3. 课程名查找：marker 之前最近的非元数据中文 span
"""

import re
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


SECTION_TIME = [
    "08:00-08:50", "09:00-09:50", "10:10-11:00", "11:10-12:00",
    "13:30-14:20", "14:30-15:20", "15:40-16:30", "16:40-17:30",
    "18:30-19:20", "19:30-20:20", "20:30-21:20",
]
DAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
DAY_NAMES = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ============ 元数据关键词（用于过滤课程名） ============
# 注意：▲ ● ■ ○ △ 这些是 PDF 课程名末尾的标识符，**不是元数据**，要排除
META_KEYWORDS = ["周", "节", "场地", "教师", "教学班", "考核", "学时", "学分", "组成", "选课", "备注", ":"]
META_PATTERN = re.compile(r'^\s*[\d\s、,\-\(\)\.:/]+\s*$')


def extract_pdf_spans(pdf_path: str) -> List[Dict]:
    """提取 spans + page offset 累加"""
    doc = fitz.open(pdf_path)
    spans = []
    page_offset = 0
    for pi, page in enumerate(doc):
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    txt = span["text"].strip()
                    if not txt:
                        continue
                    bbox = span["bbox"]
                    spans.append({
                        "text": txt,
                        "x_center": (bbox[0] + bbox[2]) / 2,
                        "y_center": (bbox[1] + bbox[3]) / 2 + page_offset,
                        "page_idx": pi,
                    })
        page_offset += page.rect.height + 50
    doc.close()
    return spans


def cluster_rows(spans: List[Dict], y_tolerance: float = 30.0) -> List[List[Dict]]:
    """按 y 聚类（容差 30 合并课程名+详情）"""
    if not spans:
        return []
    sorted_spans = sorted(spans, key=lambda s: s["y_center"])
    rows = []
    current_row = [sorted_spans[0]]
    current_y = sorted_spans[0]["y_center"]
    for sp in sorted_spans[1:]:
        if abs(sp["y_center"] - current_y) <= y_tolerance:
            current_row.append(sp)
        else:
            current_row.sort(key=lambda s: s["x_center"])
            rows.append(current_row)
            current_row = [sp]
            current_y = sp["y_center"]
    current_row.sort(key=lambda s: s["x_center"])
    rows.append(current_row)
    return rows


def find_day_rows(rows: List[List[Dict]]) -> List[Tuple[int, List[Dict]]]:
    """识别 "星期X" 行（第一个 span 是 "星期X"）"""
    day_rows = []
    for row in rows:
        first = row[0]
        m = re.fullmatch(r'星期([一二三四五六日天])', first["text"])
        if m:
            day_char = m.group(1)
            day_num = DAY_MAP[day_char]
            day_rows.append((day_num, row))
    return day_rows


def split_row_by_section_marker(course_spans: List[Dict]) -> List[List[Dict]]:
    """
    按 (X-Y节) marker 分块：
    - 找所有 (X-Y节) span 的索引
    - 每个 marker 是一个新课程的"节次起点"
    - 块 = 当前 marker 之前最多 3 个 span（课程名） + marker 后到下一个 marker 的元数据
    """
    markers = []
    for i, sp in enumerate(course_spans):
        if re.search(r'\(\d+(?:-\d+)?节\)', sp["text"]):
            markers.append(i)
    
    if not markers:
        return [course_spans] if course_spans else []
    
    blocks = []
    for mi, idx in enumerate(markers):
        # 块起点：当前 marker 之前最多 3 个 span（找课程名）
        block_start = max(0, idx - 3)
        # 块终点：下一个 marker（不含），或行尾
        next_idx = markers[mi+1] if mi+1 < len(markers) else len(course_spans)
        blocks.append(course_spans[block_start:next_idx])
    
    return blocks


def _is_metadata(txt: str) -> bool:
    """判断文本是否是元数据（非课程名）"""
    if not txt:
        return True
    if META_PATTERN.match(txt):
        return True
    if any(kw in txt for kw in META_KEYWORDS):
        return True
    return False


def extract_course_from_block(block: List[Dict], day_num: int) -> Optional[Dict]:
    """从一块 spans 提取课程"""
    full_text = "".join(sp["text"] for sp in block)
    
    # 1. 节次：找第一个 (X-Y节)
    sec_m = re.search(r'\((\d+)(?:-(\d+))?节\)', full_text)
    if not sec_m:
        return None
    sec_start = int(sec_m.group(1))
    sec_end = int(sec_m.group(2)) if sec_m.group(2) else sec_start
    
    # 2. 课程名：找 marker 之前最近的非元数据中文 span
    course_name = ""
    for i, sp in enumerate(block):
        if re.search(r'\(\d+(?:-\d+)?节\)', sp["text"]):
            # 在 marker 之前找课程名（往前最多 4 个 span）
            for j in range(i - 1, max(-1, i - 5), -1):
                txt = block[j]["text"].strip()
                if not _is_metadata(txt) and len(txt) >= 2:
                    course_name = txt
                    break
            # 如果往前没找到，往后找
            if not course_name:
                for j in range(i + 1, min(len(block), i + 3)):
                    txt = block[j]["text"].strip()
                    if not _is_metadata(txt) and len(txt) >= 2:
                        course_name = txt
                        break
            break
    
    if not course_name:
        return None
    
    # 3. 周次
    week_type = "every"
    if "(单)" in full_text or "单周" in full_text:
        week_type = "odd"
    elif "(双)" in full_text or "双周" in full_text:
        week_type = "even"
    
    start_week, end_week = 1, 20
    range_pat = re.findall(r'(\d+)\s*[-—~]\s*(\d+)\s*周', full_text)
    if range_pat:
        all_starts = [int(s) for s, e in range_pat]
        all_ends = [int(e) for s, e in range_pat]
        start_week = min(all_starts)
        end_week = max(all_ends)
    else:
        single_weeks = []
        for m in re.finditer(r'(\d+)\s*周', full_text):
            single_weeks.append(int(m.group(1)))
        if single_weeks:
            start_week, end_week = min(single_weeks), max(single_weeks)
    
    # 4. 场地
    location = ""
    loc_m = re.search(r'场地:([^/\n]+)', full_text)
    if loc_m:
        location = loc_m.group(1).strip()
    
    # 5. 教师
    teacher = ""
    teacher_m = re.search(r'教师:([^/\n]+)', full_text)
    if teacher_m:
        teacher = teacher_m.group(1).strip()
    
    return {
        "course_name": course_name,
        "teacher": teacher,
        "location": location,
        "day_of_week": day_num,
        "start_section": sec_start,
        "end_section": sec_end,
        "start_week": start_week,
        "end_week": end_week,
        "week_type": week_type,
        "raw_text": full_text[:200],
    }


def parse_pdf_with_coords(pdf_path: str) -> List[Dict]:
    """主入口"""
    spans = extract_pdf_spans(pdf_path)
    if not spans:
        return []
    rows = cluster_rows(spans, y_tolerance=30)
    day_rows = find_day_rows(rows)
    if not day_rows:
        return []
    
    courses = []
    seen = set()
    for day_num, row in day_rows:
        # 排除 "星期X" span 本身
        course_spans = [sp for sp in row if not re.fullmatch(r'星期[一二三四五六日天]', sp["text"])]
        if not course_spans:
            continue
        
        # 按 marker 分块
        blocks = split_row_by_section_marker(course_spans)
        for block in blocks:
            course = extract_course_from_block(block, day_num)
            if course:
                key = (course["day_of_week"], course["start_section"], course["course_name"])
                if key not in seen:
                    seen.add(key)
                    courses.append(course)
    
    return courses


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
        courses = parse_pdf_with_coords(pdf)
        print(f"解析出 {len(courses)} 节课:")
        for c in courses:
            week_str = f"{c['start_week']}-{c['end_week']}周"
            if c['week_type'] == 'odd':
                week_str += "(单)"
            elif c['week_type'] == 'even':
                week_str += "(双)"
            print(f"  周{DAY_NAMES[c['day_of_week']][1]} 第{c['start_section']}-{c['end_section']}节 | {c['course_name'][:20]:20s} | {c['teacher']:8s} | {c['location']:15s} | {week_str}")