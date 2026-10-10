"""
课表存储（2026-09-21）
每个用户一份独立 SQLite 文件，按 user_id 隔离（跟 user_store.py 思路一致）

Schema:
    curriculum 表（每行 = 一节课）
        id              INTEGER PRIMARY KEY
        user_id         TEXT     -- UUID，浏览器隔离
        course_name     TEXT     -- 课程名
        teacher         TEXT     -- 教师
        location        TEXT     -- 教室
        day_of_week     INTEGER  -- 1=周一 ... 7=周日
        start_section   INTEGER  -- 开始节次 1-12
        end_section     INTEGER  -- 结束节次 1-12
        start_week      INTEGER  -- 开始周次 1-20
        end_week        INTEGER  -- 结束周次 1-20
        week_type       TEXT     -- 'every' 每周 / 'odd' 单周 / 'even' 双周
        source          TEXT     -- 来自哪个文件
        uploaded_at     TEXT     -- ISO 时间

API:
    get_db_path(user_id) -> Path
    init_db(user_id) -> sqlite3.Connection
    save_curriculum(user_id, courses) -> int  # 返回入库数
    load_curriculum(user_id) -> List[dict]
    clear_curriculum(user_id) -> None
"""
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
import threading
import logging

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_DIR = PROJECT_ROOT / "data" / "sqlite"
DB_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("curriculum.db")


def get_db_path(user_id: str) -> Path:
    """每个用户一份独立 db 文件（UUID 前 16 位避免文件名过长）"""
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in user_id)[:32]
    return DB_DIR / f"curriculum_{safe_id}.db"


# 只放 CREATE TABLE,索引放到 init_db() 里(2026-10-10 改: 索引列必须在迁移后建,否则老 DB 缺列会报错)
SCHEMA = """
CREATE TABLE IF NOT EXISTS curriculum (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    class_id        INTEGER DEFAULT NULL,  -- 2026-10-09 加:班级编号 1/2/3, NULL=未分班(适用于不分班的公共课)
    course_name     TEXT NOT NULL,
    teacher         TEXT,
    location        TEXT,
    day_of_week     INTEGER NOT NULL,
    start_section   INTEGER NOT NULL,
    end_section     INTEGER NOT NULL,
    start_week      INTEGER NOT NULL DEFAULT 1,
    end_week        INTEGER NOT NULL DEFAULT 20,
    week_type       TEXT NOT NULL DEFAULT 'every',
    source          TEXT,
    uploaded_at     TEXT NOT NULL
);
"""


# 简单线程锁（防止同一用户并发写冲突）
_locks = {}
_locks_guard = threading.Lock()


def _get_lock(user_id: str) -> threading.Lock:
    with _locks_guard:
        if user_id not in _locks:
            _locks[user_id] = threading.Lock()
        return _locks[user_id]


def init_db(user_id: str) -> sqlite3.Connection:
    """打开(或创建)用户的课表数据库,自动补齐老 schema 缺的列

    2026-10-10 加 schema migration:
        10/9 加了 class_id 列,但老 DB(9/22-9/23 创建)没有这列。
        CREATE TABLE IF NOT EXISTS 对已存在表无效 → load_curriculum 会
        SELECT class_id 报 "no such column" 错。
        解决:init_db 时用 PRAGMA table_info 检查,缺的 ALTER TABLE 加上,
        幂等(已加的不重复加)。
    """
    db_path = get_db_path(user_id)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.executescript(SCHEMA)

    # ★ Schema migration: 老 DB 没 class_id 列 → 补上(默认 NULL)
    #    补完后老数据的 class_id 都是 NULL,对应"未分班的公共课"语义
    cur = conn.execute("PRAGMA table_info(curriculum)")
    existing_cols = {row[1] for row in cur}

    # ★ 这里列"缺的列 → ALTER 加"的清单;以后再加字段也在此追加
    MIGRATIONS = [
        ("class_id", "INTEGER DEFAULT NULL"),
        # 例:("new_col", "TEXT DEFAULT ''"),  # 2026-XX-XX 加:xxx 字段
    ]
    for col_name, col_def in MIGRATIONS:
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE curriculum ADD COLUMN {col_name} {col_def}")
            log.info(f"[schema migration] {db_path.name}: +{col_name} 列")

    # ★ 索引必须在 migration 之后建 —— 老 DB 缺 class_id 时,
    #    CREATE INDEX idx_user_class ON curriculum(user_id, class_id) 会
    #    抛 "no such column" 异常中断整个 init_db,导致 ALTER 都没机会跑。
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_day ON curriculum(user_id, day_of_week)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_class ON curriculum(user_id, class_id)")

    conn.commit()
    return conn


def save_curriculum(
    user_id: str,
    courses: List[Dict],
    source: str = "用户上传",
    class_id: Optional[int] = None,
) -> Dict:
    """
    批量保存课表条目(清空该用户该班级的旧数据后全量替换)

    Args:
        user_id: 用户 UUID
        courses: List[dict],每项包含:
            course_name, teacher, location, day_of_week,
            start_section, end_section, start_week, end_week, week_type
        source: 课表来源文件名(方便溯源)
        class_id: 班级编号 1/2/3,None 表示不按班级分(全量覆盖)

    Returns:
        dict: {"saved": int, "skipped": int, "errors": [...]}
    """
    if not user_id:
        raise ValueError("user_id 不能为空")

    lock = _get_lock(user_id)
    with lock:
        conn = init_db(user_id)
        try:
            cur = conn.cursor()
            # 全量替换:先删该班级(或全删)的旧数据
            if class_id is not None:
                cur.execute("DELETE FROM curriculum WHERE user_id = ? AND class_id = ?", (user_id, class_id))
            else:
                cur.execute("DELETE FROM curriculum WHERE user_id = ? AND class_id IS NULL", (user_id,))

            saved = 0
            skipped = 0
            errors = []
            now = datetime.now().isoformat(timespec="seconds")

            for c in courses:
                try:
                    # 数据校验
                    if not c.get("course_name"):
                        skipped += 1
                        continue
                    if not (1 <= c.get("day_of_week", 0) <= 7):
                        skipped += 1
                        continue
                    if not (1 <= c.get("start_section", 0) <= 12):
                        skipped += 1
                        continue
                    if c.get("end_section", c.get("start_section", 0)) < c.get("start_section", 0):
                        skipped += 1
                        continue

                    cur.execute(
                        """
                        INSERT INTO curriculum
                          (user_id, class_id, course_name, teacher, location,
                           day_of_week, start_section, end_section,
                           start_week, end_week, week_type,
                           source, uploaded_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
                            class_id,
                            c.get("course_name", "").strip(),
                            c.get("teacher", "").strip(),
                            c.get("location", "").strip(),
                            int(c["day_of_week"]),
                            int(c["start_section"]),
                            int(c.get("end_section", c["start_section"])),
                            int(c.get("start_week", 1)),
                            int(c.get("end_week", 20)),
                            c.get("week_type", "every"),
                            source,
                            now,
                        ),
                    )
                    saved += 1
                except Exception as e:
                    errors.append({"course": c.get("course_name", "?"), "err": str(e)})

            conn.commit()
            return {"saved": saved, "skipped": skipped, "errors": errors}
        finally:
            conn.close()


def load_curriculum(user_id: str, class_id: Optional[int] = None) -> List[Dict]:
    """加载用户课表条目,按 day_of_week + start_section 排序

    Args:
        user_id: 用户 UUID
        class_id: 班级编号,None=返回该用户所有(不分班 + 各班)
    """
    if not user_id:
        return []
    conn = init_db(user_id)
    try:
        cur = conn.cursor()
        if class_id is not None:
            cur.execute(
                """
                SELECT id, class_id, course_name, teacher, location,
                       day_of_week, start_section, end_section,
                       start_week, end_week, week_type, source, uploaded_at
                FROM curriculum
                WHERE user_id = ? AND (class_id = ? OR class_id IS NULL)
                ORDER BY day_of_week, start_section
                """,
                (user_id, class_id),
            )
        else:
            cur.execute(
                """
                SELECT id, class_id, course_name, teacher, location,
                       day_of_week, start_section, end_section,
                       start_week, end_week, week_type, source, uploaded_at
                FROM curriculum
                WHERE user_id = ?
                ORDER BY day_of_week, start_section
                """,
                (user_id,),
            )
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "class_id": r[1],
                "course_name": r[2],
                "teacher": r[3],
                "location": r[4],
                "day_of_week": r[5],
                "start_section": r[6],
                "end_section": r[7],
                "start_week": r[8],
                "end_week": r[9],
                "week_type": r[10],
                "source": r[11],
                "uploaded_at": r[12],
            }
            for r in rows
        ]
    finally:
        conn.close()


def add_courses(
    user_id: str,
    courses: List[Dict],
    class_id: Optional[int] = None,
    source: str = "实验课表上传",
) -> Dict:
    """**增量**加课表条目(不清空旧的,只追加 + 去重)
    用于:实验课表合并、群消息识别后入课表。
    2026-10-09 新增。

    去重策略:同 (day_of_week, start_section, course_name) 视为重复,跳过
    """
    if not user_id:
        return {"saved": 0, "skipped": 0, "duplicated": 0, "errors": []}

    lock = _get_lock(user_id)
    with lock:
        conn = init_db(user_id)
        try:
            cur = conn.cursor()
            # 先查已存在 (user_id, class_id, day, start_section, course_name)
            cur.execute(
                "SELECT day_of_week, start_section, course_name FROM curriculum "
                "WHERE user_id = ? AND (class_id = ? OR (? IS NULL AND class_id IS NULL))",
                (user_id, class_id, class_id),
            )
            existing = {
                (r[0], r[1], r[2]) for r in cur.fetchall()
            }

            saved = 0
            skipped = 0
            duplicated = 0
            errors = []
            now = datetime.now().isoformat(timespec="seconds")

            for c in courses:
                try:
                    if not c.get("course_name"):
                        skipped += 1
                        continue
                    if not (1 <= c.get("day_of_week", 0) <= 7):
                        skipped += 1
                        continue
                    if not (1 <= c.get("start_section", 0) <= 12):
                        skipped += 1
                        continue

                    key = (int(c["day_of_week"]), int(c["start_section"]), c["course_name"].strip())
                    if key in existing:
                        duplicated += 1
                        continue

                    cur.execute(
                        """
                        INSERT INTO curriculum
                          (user_id, class_id, course_name, teacher, location,
                           day_of_week, start_section, end_section,
                           start_week, end_week, week_type, source, uploaded_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
                            class_id,
                            c.get("course_name", "").strip(),
                            c.get("teacher", "").strip(),
                            c.get("location", "").strip(),
                            int(c["day_of_week"]),
                            int(c["start_section"]),
                            int(c.get("end_section", c["start_section"])),
                            int(c.get("start_week", 1)),
                            int(c.get("end_week", 20)),
                            c.get("week_type", "every"),
                            source,
                            now,
                        ),
                    )
                    saved += 1
                except Exception as e:
                    errors.append({"course": c.get("course_name", "?"), "err": str(e)})

            conn.commit()
            return {
                "saved": saved,
                "skipped": skipped,
                "duplicated": duplicated,
                "errors": errors,
            }
        finally:
            conn.close()


def clear_curriculum(user_id: str, class_id: Optional[int] = None) -> int:
    """清空用户课表(可按班级清空)"""
    if not user_id:
        return 0
    lock = _get_lock(user_id)
    with lock:
        conn = init_db(user_id)
        try:
            cur = conn.cursor()
            if class_id is not None:
                cur.execute(
                    "DELETE FROM curriculum WHERE user_id = ? AND class_id = ?",
                    (user_id, class_id),
                )
            else:
                cur.execute("DELETE FROM curriculum WHERE user_id = ?", (user_id,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def query_today(
    user_id: str,
    day_of_week: int,
    section_range: Optional[tuple] = None,
) -> List[Dict]:
    """
    查询某天某节次范围的课（给小U对话联动用）
    Args:
        day_of_week: 1-7
        section_range: (start, end) 可选；不传则返回当天所有课
    """
    all_courses = load_curriculum(user_id)
    result = []
    for c in all_courses:
        if c["day_of_week"] != day_of_week:
            continue
        if section_range:
            s, e = section_range
            # 时间区间有交集
            if c["end_section"] < s or c["start_section"] > e:
                continue
        result.append(c)
    return result


# 模块自测
if __name__ == "__main__":
    test_user = "test-user-001"
    courses = [
        {
            "course_name": "高等数学",
            "teacher": "张教授",
            "location": "教A101",
            "day_of_week": 1,
            "start_section": 1,
            "end_section": 2,
            "start_week": 1,
            "end_week": 16,
            "week_type": "every",
        },
        {
            "course_name": "电路分析",
            "teacher": "李老师",
            "location": "教B203",
            "day_of_week": 3,
            "start_section": 3,
            "end_section": 4,
            "start_week": 1,
            "end_week": 16,
            "week_type": "every",
        },
    ]
    print("=== save ===")
    print(save_curriculum(test_user, courses, source="测试PDF"))
    print("\n=== load ===")
    for c in load_curriculum(test_user):
        print(c)
    print("\n=== query 周一 ===")
    for c in query_today(test_user, day_of_week=1):
        print(c)
    print("\n=== clear ===")
    print(f"Deleted {clear_curriculum(test_user)} rows")