"""
我的课表（2026-09-21）
允许用户上传课表 PDF / 截图 → OCR 识别 → 解析成结构化数据 → 周视图展示
跟小U对话能联动（"今天上啥" → 查 SQLite）

子模块:
    db.py      — SQLite 存储（每用户一份课表）
    parser.py  — PDF + 截图 OCR 课表结构识别
    ui.py      — 周视图 HTML 生成器

数据流:
    用户上传 PDF/截图
        ↓ parser.parse_uploaded_file()
    原始文本（PyMuPDF 或 PaddleOCR）
        ↓ parser.extract_schedule()
    结构化 List[dict]: {course, teacher, location, day, sections, weeks}
        ↓ db.save_curriculum()
    SQLite (data/sqlite/curriculum_<user_id>.db)
        ↓ ui.render_week_view()
    Gradio HTML 周视图（周一-周日 × 第 1-12 节）
"""

from . import db, parser, ui, week, settings, experiment_parser

__all__ = ["db", "parser", "ui", "week", "settings", "experiment_parser"]