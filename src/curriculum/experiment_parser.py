"""
实验课表解析器（2026-10-09 新增）
解析学校实验课预约系统的导出表（Excel/CSV/截图），按用户班级自动过滤。

设计目标:
    1. 支持 Excel (.xlsx / .xls) - 表格导出
    2. 支持 CSV (.csv)
    3. 支持截图 (.png / .jpg) - 走 OCR + 正则
    4. 智能判断"我的班级"是否在 class_str 字段里
       - "24电信1, 24电信2, 24电信3" -> 拆成 3 段,匹配 24电信X
       - "24电信1, 24电信2, 24电信3(第2批)" -> 拆 + 批次匹配
"""
import re
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple

log = logging.getLogger("curriculum.experiment_parser")

# 周次文字 → 数字
WEEK_DAY_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7,
}


def _parse_section_range(s: str) -> Tuple[int, int]:
    """从 "1-4" / "7-8" / "5-6" 里提取 (start, end) 节次

    Args:
        s: "1-4" / "7-8" / "5" / "1-4(08:00-11:50)"
    Returns:
        (start_section, end_section)
    """
    if not s:
        return (0, 0)
    # 提取 "数字-数字"
    m = re.search(r"(\d+)\s*[-—~]\s*(\d+)", str(s))
    if m:
        return (int(m.group(1)), int(m.group(2)))
    # 单个数字
    m = re.search(r"第?\s*(\d+)\s*节?", str(s))
    if m:
        n = int(m.group(1))
        return (n, n)
    return (0, 0)


def _class_in_user_class(class_str: str, user_class: str) -> bool:
    """判断 class_str 字段里是否包含 user_class 班级

    Args:
        class_str: 课程表里的"班级"字段
            例: "24电信1, 24电信2, 24电信3(第2批)" / "24电信1, 24电信2"
        user_class: 用户自己的班级名
            例: "24电信2" / "电信2201"
    Returns:
        True = 命中(这节课有自己班), False = 没命中
    """
    if not class_str or not user_class:
        return False
    # 直接字符串包含
    if user_class in class_str:
        return True
    # 兼容: "电信2201" vs "24电信1" - 这种不通用,让用户自己设置成一致
    return False


def parse_experiment_table_rows(rows: List[Dict], user_class: str = "") -> List[Dict]:
    """
    把从 Excel/CSV 读出的行 dict 列表,转成标准 curriculum dict

    期望的列名(任一格式都行,大小写不敏感):
        周次 / week
        星期 / day / dow
        时间 / time / datetime  (格式 "20260911" 或 "2026-09-11")
        节次/时间 / section_time  (格式 "1-4(08:00-11:50)")
        班级名称 / class / class_name
        课程名称 / course / course_name  (这一列可能叫"项目名称"或"课程名称")
        实验室 / lab / location
        教师 / teacher

    Args:
        rows: 从 Excel/CSV 读出的 List[Dict] (每行 key 不统一,我们做模糊匹配)
        user_class: 用户班级(用于过滤)
    Returns:
        标准 curriculum dict 列表(已过滤 + 转格式)
    """
    if not rows:
        return []

    # 找列名(模糊匹配)
    def find_col(*aliases):
        """从行 dict 找匹配的列(key)"""
        for r in rows[:3]:  # 只看前 3 行
            for k in r.keys():
                k_norm = str(k).strip().lower()
                for a in aliases:
                    if a.lower() in k_norm:
                        return k
        return None

    col_week = find_col("周次", "week")
    col_day = find_col("星期", "day", "dow")
    col_time = find_col("时间", "time", "date")
    col_section = find_col("节次", "section", "节次/时间")
    col_class = find_col("班级", "class")
    col_course = find_col("课程", "course", "项目", "subject")
    col_lab = find_col("实验室", "lab", "location", "room")
    col_teacher = find_col("教师", "teacher", "faculty")

    if not col_day or not col_section:
        log.warning(f"表缺关键列: day={col_day} section={col_section}")
        return []

    results = []
    for r in rows:
        # 1. 解析星期
        day_raw = str(r.get(col_day, "")).strip()
        day_num = None
        if day_raw.isdigit():
            day_num = int(day_raw) if 1 <= int(day_raw) <= 7 else None
        else:
            for k, v in WEEK_DAY_MAP.items():
                if k in day_raw:
                    day_num = v
                    break
        if day_num is None:
            continue

        # 2. 解析节次
        sec_start, sec_end = _parse_section_range(str(r.get(col_section, "")))
        if sec_start == 0:
            continue

        # 3. 班级过滤
        class_str = str(r.get(col_class, "")).strip() if col_class else ""
        if user_class and class_str:
            if not _class_in_user_class(class_str, user_class):
                continue

        # 4. 周次(单条课表就是 1 周,完整周次信息从 "周次" 列拿)
        start_week = 1
        end_week = 20
        if col_week:
            week_str = str(r.get(col_week, "")).strip()
            # 例: "第 2 周" / "2 周" / "2"
            m = re.search(r"(\d+)", week_str)
            if m:
                w = int(m.group(1))
                start_week = w
                end_week = w

        # 5. 课程名 + 教师 + 实验室
        course_name = str(r.get(col_course, "")).strip() if col_course else ""
        teacher = str(r.get(col_teacher, "")).strip() if col_teacher else ""
        location = str(r.get(col_lab, "")).strip() if col_lab else ""
        if not course_name:
            course_name = "实验课"  # fallback

        # 6. 去除实验室里的 "(房间号)" 后缀,只留名字
        #    "电子工艺实验室2(21201012)" -> "电子工艺实验室2"
        location_clean = re.sub(r"[（(][^）)]*[）)]", "", location).strip()
        if not location_clean:
            location_clean = location

        results.append({
            "course_name": course_name,
            "teacher": teacher,
            "location": location_clean,
            "day_of_week": day_num,
            "start_section": sec_start,
            "end_section": sec_end,
            "start_week": start_week,
            "end_week": end_week,
            "week_type": "every",
        })

    log.info(f"实验课表解析: {len(rows)} 行 -> {len(results)} 条匹配班级 '{user_class}'")

    # ★ 范围合并(2026-10-10 加): 实验课表里同一节课常在连续多周都出现,
    #   例如 "第4周 实验2 + 第6周 实验2",用户看课表时希望看到 1 条范围
    #   ("双周 第4-6周 实验2")而不是 2 条独立行。
    #   合并规则:
    #     - 分组 key = (day, sec_start, sec_end, course, location, teacher)
    #       → 同课程 + 同时间槽 视为同一节课
    #     - 同组内的多行 → 合并成 1 条
    #       start_week = min, end_week = max
    #       week_type: 全奇→odd, 全偶→even, 混合→every
    #     - 跨课程:不同 course_name → 不同分组,不合并(如 "实验2" 不和 "实验3" 合并)
    merged = _merge_continuous_weeks(results)
    log.info(f"范围合并后: {len(results)} 条 -> {len(merged)} 条")
    return merged


def _merge_continuous_weeks(rows: List[Dict]) -> List[Dict]:
    """
    把同一节课(同 day+sec+course+location+teacher)的多个周次合并成范围。

    输入(来自 parse_experiment_table_rows 的每行结果,start_week=end_week=w):
        [
          {course_name: "实验2", day_of_week: 1, start_section: 7, end_section: 8,
           location: "...", teacher: "...", start_week: 4, end_week: 4, week_type: "every"},
          {course_name: "实验2", day_of_week: 1, start_section: 7, end_section: 8,
           location: "...", teacher: "...", start_week: 6, end_week: 6, week_type: "every"},
        ]

    输出(合并后):
        [
          {course_name: "实验2", day_of_week: 1, start_section: 7, end_section: 8,
           location: "...", teacher: "...", start_week: 4, end_week: 6, week_type: "even"},
        ]

    边界情况:
        - 同组内 weeks 全是奇数 → week_type="odd"(只在这个节上)
        - 同组内 weeks 全是偶数 → week_type="even"(只在双周上)
        - 混合奇偶 → week_type="every"(每周都上,例如 1 周 + 2 周都有)
        - 单条 row → start_week == end_week, week_type 由原 row 决定(默认 every,合并时会按 w 的奇偶重新算)
        - 跨课程:不在同一分组 → 不合并,各自保留
    """
    if not rows:
        return []

    # 1. 按"分组 key"聚类
    groups: Dict[Tuple, List[int]] = {}  # key → [week1, week2, ...]
    templates: Dict[Tuple, Dict] = {}   # key → 第一个 row 当模板(用于保留 location/teacher 等)

    for r in rows:
        # ★ 分组 key:把课程名完整当一行——不同实验(实验2 vs 实验3) 自然分开,
        #   不会被错误合并
        key = (
            r["day_of_week"],
            r["start_section"],
            r["end_section"],
            r["course_name"],   # 完整匹配,不取前缀
            r.get("location", ""),
            r.get("teacher", ""),
        )
        if key not in groups:
            groups[key] = []
            templates[key] = r  # 第一个 row 当模板
        # ★ 取 start_week(每条 row 是单周次,start_week == end_week)
        wk = r.get("start_week", r.get("end_week", 0))
        if wk:
            groups[key].append(int(wk))

    # 2. 每组算 week_type + 输出合并后的 row
    merged: List[Dict] = []
    for key, weeks in groups.items():
        weeks_sorted = sorted(set(weeks))  # 去重 + 排序
        if not weeks_sorted:
            continue

        # ★ 奇偶判定:全奇→odd, 单周;全偶→even, 双周;混合→every,每周
        all_odd = all(w % 2 == 1 for w in weeks_sorted)
        all_even = all(w % 2 == 0 for w in weeks_sorted)
        week_type = "odd" if all_odd else ("even" if all_even else "every")

        # 用模板 row,覆盖周次范围
        row = dict(templates[key])
        row["start_week"] = min(weeks_sorted)
        row["end_week"] = max(weeks_sorted)
        row["week_type"] = week_type
        merged.append(row)

    log.debug(f"[merge] {len(rows)} 条 → {len(merged)} 条 (合并掉 {len(rows) - len(merged)} 条重复周次)")
    return merged


def parse_experiment_table(file_path: str, user_class: str = "") -> List[Dict]:
    """
    主入口:从文件解析实验课表

    Args:
        file_path: 文件绝对路径
        user_class: 用户班级(用于过滤)
    Returns:
        标准 curriculum dict 列表
    """
    p = Path(file_path)
    if not p.exists():
        log.error(f"文件不存在: {file_path}")
        return []

    ext = p.suffix.lower()

    if ext in (".xlsx", ".xls"):
        rows = _read_excel(p)
    elif ext == ".csv":
        rows = _read_csv(p)
    elif ext in (".png", ".jpg", ".jpeg"):
        rows = _read_image(p)
    else:
        log.error(f"不支持的格式: {ext}")
        return []

    return parse_experiment_table_rows(rows, user_class=user_class)


def _read_excel(p: Path) -> List[Dict]:
    """读 Excel (openpyxl 读 .xlsx, xlrd 读 .xls)"""
    rows = []
    try:
        if p.suffix.lower() == ".xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(p, read_only=True, data_only=True)
            for sheet in wb.worksheets:
                rows.extend(_sheet_to_rows(sheet))
                break  # 只读第一个 sheet
        else:  # .xls
            import xlrd
            wb = xlrd.open_workbook(str(p))
            sheet = wb.sheet_by_index(0)
            headers = [str(sheet.cell_value(0, c)) for c in range(sheet.ncols)]
            for r in range(1, sheet.nrows):
                row = {headers[c]: sheet.cell_value(r, c) for c in range(sheet.ncols)}
                rows.append(row)
    except Exception as e:
        log.exception(f"读 Excel 失败: {e}")
    return rows


def _sheet_to_rows(sheet) -> List[Dict]:
    """openpyxl sheet 转 List[Dict]"""
    rows = []
    headers = []
    for i, row in enumerate(sheet.iter_rows(values_only=True)):
        if i == 0:
            headers = [str(c) if c is not None else "" for c in row]
            continue
        # 跳过空行
        if not any(c is not None for c in row):
            continue
        d = {headers[j]: row[j] for j in range(len(headers))}
        rows.append(d)
    return rows


def _read_csv(p: Path) -> List[Dict]:
    """读 CSV"""
    rows = []
    try:
        import csv
        with open(p, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except UnicodeDecodeError:
        try:
            with open(p, "r", encoding="gbk", newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except Exception as e:
            log.exception(f"读 CSV 失败(GBK 也不行): {e}")
    except Exception as e:
        log.exception(f"读 CSV 失败: {e}")
    return rows


def _read_image(p: Path) -> List[Dict]:
    """读图片:OCR 抽取文字,再用正则解析"""
    try:
        from src.ocr.ocr_engine import extract_text
        result = extract_text(str(p), conf_threshold=0.5)
        text = (result or {}).get("text", "").strip()
    except Exception as e:
        log.exception(f"OCR 失败: {e}")
        return []

    if not text:
        return []

    log.info(f"OCR 抽到 {len(text)} 字符,正则解析中...")

    # 行模式:每行一条实验课
    # 例: "第 2 周 星期 1 20260914 7-8(15:55-17:35) 24电信1, 24电信2, 24电信3(24电信2) 实验1 嵌入式电子技术综合实验室1(071)(21202071)"
    # 或表格截图(每行多个字段,空格分隔)
    row_pat = re.compile(
        r"第\s*(\d+)\s*周\s+"
        r"星期\s*(\d+|[一二三四五六日天])\s+"
        r"(\d{8})\s+"
        r"(\d+[-—~]\d+)\([^)]+\)\s+"
        r"([^实验]+?)\s+"
        r"(实验[一二三四五六七八九十\d]+)\s+"
        r"(\S+(?:\(\d+\))?(?:\(\d+\))?)\s*"
    )

    rows = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or "周" not in line:
            continue
        m = row_pat.search(line)
        if not m:
            continue
        week, day, _date, section, class_str, course, lab = m.groups()
        # 构造与 Excel 读取一致的行 dict
        rows.append({
            "周次": f"第 {week} 周",
            "星期": day,
            "时间": _date,
            "节次/时间": section,
            "班级名称": class_str.strip().rstrip(","),
            "课程名称": course.strip(),
            "实验室": lab.strip(),
        })

    log.info(f"OCR 解析得到 {len(rows)} 行")
    return rows
