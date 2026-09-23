"""
课表结构化解析器（2026-09-21）

支持的输入:
    1. PDF 文件（教务系统导出的表格 PDF 优先）
    2. 图片（手机截图，PaddleOCR 2.7.3）

支持的课表文本模式（高容错正则匹配）:
    - "周X" / "星期X"  → day_of_week
    - "第X-Y节" / "X-Y节" / "X-Y"  → start_section / end_section
    - "X-Y周"  → start_week / end_week
    - "X老师"  → teacher
    - "X楼/Y室/Z教"  → location
    - 剩下的中文片段  → course_name

典型输出格式（每行一条）:
    高等数学; 张老师; 教A101; 1; 1-2; 1-16周; 每周
    电路分析; 李老师; 教B203; 3; 3-4; 1-16周; 每周

设计目标:
    - 80% 常见格式能自动解析
    - 20% 解析不出来的 → 留 raw_text 给前端展示，用户手动修正
"""
import re
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# 让 src 包可被 import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ============ 正则模式 ============
DAY_PAT = re.compile(r'周([一二三四五六日天])|星期([一二三四五六日天])')
SECTION_PAT = re.compile(r'第?\s*(\d+)\s*[-—~]\s*(\d+)\s*节?|第?\s*(\d+)\s*节?')
WEEK_TYPE_PAT = re.compile(r'(单|双)周|每周')
WEEK_RANGE_PAT = re.compile(r'(\d+)\s*[-—~]\s*(\d+)\s*周?')
TEACHER_PAT = re.compile(r'([\u4e00-\u9fa5·]{1,4}\s*老师)')
LOCATION_KEYWORDS = ['楼', '室', '教', '馆']

DAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}

# 节次范围限制（避免出现 "1-100" 这种异常）
MAX_SECTION = 13   # 一节课最多 13 节连上（含自己扎少见课时）
MAX_WEEK = 25      # 一学期最多 25 周


# ============ 单行解析 ============
def parse_schedule_line(line: str) -> Optional[Dict]:
    """
    解析一行课表文本

    Returns:
        解析成功的 dict；失败返回 None
        dict 包含: course_name, teacher, location, day_of_week,
                  start_section, end_section, start_week, end_week, week_type,
                  raw_line（原文，方便排错）
    """
    line = line.strip()
    if len(line) < 4:
        return None

    # 1. 星期
    day_match = DAY_PAT.search(line)
    if not day_match:
        return None
    day_char = day_match.group(1) or day_match.group(2)
    day_of_week = DAY_MAP.get(day_char)
    if not day_of_week:
        return None

    # 2. 节次
    sec_match = SECTION_PAT.search(line)
    if not sec_match:
        return None
    groups = sec_match.groups()
    if groups[2]:  # "第N节" 单节
        start_section = end_section = int(groups[2])
    else:           # "N-M节" 双节
        start_section = int(groups[0])
        end_section = int(groups[1]) if groups[1] else start_section
    if not (1 <= start_section <= MAX_SECTION and 1 <= end_section <= MAX_SECTION):
        return None
    if end_section < start_section:
        start_section, end_section = end_section, start_section

    # 3. 周次范围
    week_match = WEEK_RANGE_PAT.search(line)
    if week_match:
        start_week = int(week_match.group(1))
        end_week = int(week_match.group(2))
        start_week = min(start_week, end_week)
        end_week = max(start_week, end_week)
        if not (1 <= start_week <= MAX_WEEK and 1 <= end_week <= MAX_WEEK):
            start_week, end_week = 1, 20
    else:
        start_week, end_week = 1, 20

    # 4. 单双周
    week_type = "every"
    wt_match = WEEK_TYPE_PAT.search(line)
    if wt_match:
        if wt_match.group(1) == "单":
            week_type = "odd"
        elif wt_match.group(1) == "双":
            week_type = "even"

    # 5. 教师
    teacher_match = TEACHER_PAT.search(line)
    teacher = teacher_match.group(1).strip() if teacher_match else ""

    # 6. 地点（更严格的 pattern：必须含"教/楼/馆"或字母+数字组合）
    location = ""
    # 优先匹配 "教A101" / "教B-203" / "主楼A101" / "体育馆" 这种
    LOCATION_PAT = re.compile(
        r'([\u4e00-\u9fa5]{0,4}?教[\u4e00-\u9fa5A-Za-z]?[\-\d]{1,8})'  # 教X101
        r'|((?:主楼|教学楼|实验楼|图书馆|体育馆|综合楼)[\u4e00-\u9fa5A-Za-z0-9-]{0,8})'  # 楼名
        r'|([\u4e00-\u9fa5]{2,4}楼[\d]{1,4})'  # X楼123
    )
    loc_match = LOCATION_PAT.search(line)
    if loc_match:
        location = (loc_match.group(1) or loc_match.group(2) or loc_match.group(3) or "").strip()

    # 7. 课程名：用 sub 一次性移除所有已识别字段，避免 replace 残留
    cleaned = line
    cleaned = DAY_PAT.sub(' ', cleaned)
    cleaned = SECTION_PAT.sub(' ', cleaned)
    cleaned = WEEK_RANGE_PAT.sub(' ', cleaned)
    cleaned = WEEK_TYPE_PAT.sub(' ', cleaned)
    cleaned = TEACHER_PAT.sub(' ', cleaned)
    cleaned = LOCATION_PAT.sub(' ', cleaned)
    # 移除常见连接词 + 多余空白
    cleaned = re.sub(r'[|;,，；。:\s]+', ' ', cleaned).strip()
    # 取最长中文片段（>=2 字）
    parts = [p.strip() for p in cleaned.split() if p.strip() and len(p.strip()) >= 2]
    course_name = max(parts, key=len) if parts else "未识别课程"

    return {
        "course_name": course_name,
        "teacher": teacher,
        "location": location,
        "day_of_week": day_of_week,
        "start_section": start_section,
        "end_section": end_section,
        "start_week": start_week,
        "end_week": end_week,
        "week_type": week_type,
        "raw_line": line,
    }


# ============ 文本批量解析 ============
def extract_schedule(text: str) -> Tuple[List[Dict], List[str]]:
    """
    从纯文本批量提取课表条目

    Args:
        text: 整段文字（PDF 或 OCR 出的）

    Returns:
        (parsed, unparsed_raw_lines)
        parsed: 解析成功的 List[dict]
        unparsed_raw_lines: 解析失败的行（用户手动编辑）
    """
    parsed: List[Dict] = []
    unparsed: List[str] = []

    # 按行切分（兼容 \r\n / \n / 制表符分隔）
    lines = re.split(r'[\r\n]+', text)
    for line in lines:
        line = line.strip()
        if not line or len(line) < 4:
            continue
        # 跳过表头
        if any(kw in line for kw in ['课程名称', '课程名', '上课时间', '上课地点', '教师', '任课教师']):
            continue
        result = parse_schedule_line(line)
        if result:
            parsed.append(result)
        else:
            # 记录可能含"周"但解析失败的行
            if '周' in line or '节' in line:
                unparsed.append(line)

    return parsed, unparsed


# ============ PDF 文本提取 ============
def extract_pdf_text(pdf_path: str) -> str:
    """PyMuPDF 提取 PDF 全文（按页拼接）"""
    import fitz  # PyMuPDF
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
    doc.close()
    return full_text


# ============ 图片 OCR 文本提取 ============
def extract_image_text(image_path: str, conf_threshold: float = 0.5) -> str:
    """PaddleOCR 提取图片文字"""
    from src.ocr.ocr_engine import extract_text
    result = extract_text(image_path, conf_threshold=conf_threshold)
    return result["text"]


# ============ 主入口：上传文件 → 解析 → 返回结构化数据 ============
def parse_uploaded_file(
    file_path: str,
    conf_threshold: float = 0.5,
) -> Dict:
    """
    主入口：根据文件类型自动选择 PDF 解析（坐标版）或 OCR 识别

    PDF 解析策略:
        1. 优先用坐标解析（parser_coords.parse_pdf_with_coords）
           - 依赖 PyMuPDF 坐标，page 1 上午段 100% 准确
           - page 2/3 下午/晚上段的 day 标签缺失，day=None
        2. 回退到旧版按行正则解析（兼容已知规范文本）

    Returns:
        dict: {
            "source": str,         # 文件名
            "raw_text": str,       # 原始提取的文本（用户可看）
            "parsed": List[dict],  # 解析成功的条目
            "unparsed_lines": List[str],  # 解析失败的行（image OCR 路径才有）
            "file_type": "pdf" | "image",
            "stats": {...}
        }
    """
    from pathlib import Path
    fp = Path(file_path)
    if not fp.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ext = fp.suffix.lower()
    file_type = "pdf" if ext == ".pdf" else "image"
    source_name = fp.name

    if file_type == "pdf":
        # 优先用 pdfplumber 表格识别（借鉴 Al-rimi/Timetable-to-Calendar，page 2/3 也解析）
        try:
            from .parser_v2_pdfplumber import parse_pdf_with_pdfplumber
            parsed = parse_pdf_with_pdfplumber(str(fp))
            # 字段对齐：parser_v2 用 raw_text，UI 用 raw_line
            for c in parsed:
                c["raw_line"] = c.pop("raw_text", "")
            raw_text = extract_pdf_text(str(fp))  # 同时保留 raw_text 给前端展示
            unparsed = []  # pdfplumber 没有 unparsed 概念
            parse_method = "pdfplumber"
        except Exception as e:
            # 回退到坐标解析
            try:
                from .parser_coords import parse_pdf_with_coords
                parsed = parse_pdf_with_coords(str(fp))
                for c in parsed:
                    c["raw_line"] = c.pop("raw_text", "")
                raw_text = extract_pdf_text(str(fp))
                unparsed = []
                parse_method = "coords"
            except Exception as e2:
                # 最后回退到旧版按行正则
                print(f"[parser] pdfplumber + coords 都失败: {e2}，回退到按行正则")
                raw_text = extract_pdf_text(str(fp))
                parsed, unparsed = extract_schedule(raw_text)
                parse_method = "regex"
    else:
        # 图片：OCR + 正则
        raw_text = extract_image_text(str(fp), conf_threshold=conf_threshold)
        parsed, unparsed = extract_schedule(raw_text)
        parse_method = "ocr+regex"

    return {
        "source": source_name,
        "raw_text": raw_text,
        "parsed": parsed,
        "unparsed_lines": unparsed,
        "file_type": file_type,
        "parse_method": parse_method,
        "stats": {
            "raw_text_lines": len([l for l in raw_text.split('\n') if l.strip()]),
            "parsed_count": len(parsed),
            "unparsed_count": len(unparsed),
            "parse_rate": round(len(parsed) / max(1, len(parsed) + len(unparsed)), 2) if (parsed or unparsed) else 0,
        },
    }


# ============ 模块自测 ============
if __name__ == "__main__":
    import json
    # 测试文本（模拟教务系统导出）
    test_text = """
高等数学    张三老师    周一第1-2节    教A101    1-16周
电路分析    李四老师    周三第3-4节    教B203    1-16周
英语        王五老师    周五第5节     教C305    单周
体育        -           周二第11-12节  体育馆    1-16周
操作系统    赵六老师    周四第6-8节    教A401    双周
随机一行没用的文字
    """

    print("=== 文本批量解析 ===")
    parsed, unparsed = extract_schedule(test_text)
    print(f"\n解析成功 {len(parsed)} 条:")
    for p in parsed:
        print(f"  {p['course_name']:8s} | {p['teacher']:8s} | 周{p['day_of_week']} 第{p['start_section']}-{p['end_section']}节 | {p['location']:8s} | {p['start_week']}-{p['end_week']}周 ({p['week_type']})")
    print(f"\n未解析 {len(unparsed)} 条: {unparsed}")