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

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_DIR = PROJECT_ROOT / "data" / "sqlite"
DB_DIR.mkdir(parents=True, exist_ok=True)


def get_db_path(user_id: str) -> Path:
    """每个用户一份独立 db 文件（UUID 前 16 位避免文件名过长）"""
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in user_id)[:32]
    return DB_DIR / f"curriculum_{safe_id}.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS curriculum (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    course_name     TEXT NOT NULL,
    teacher         TEXT,
    location        TEXT,
    day_of_week     INTEGER NOT NULL,  -- 1=周一 ... 7=周日
    start_section   INTEGER NOT NULL,
    end_section     INTEGER NOT NULL,
    start_week      INTEGER NOT NULL DEFAULT 1,
    end_week        INTEGER NOT NULL DEFAULT 20,
    week_type       TEXT NOT NULL DEFAULT 'every',  -- 'every' / 'odd' / 'even'
    source          TEXT,
    uploaded_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_day ON curriculum(user_id, day_of_week);
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
    """打开（或创建）用户的课表数据库"""
    db_path = get_db_path(user_id)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def save_curriculum(
    user_id: str,
    courses: List[Dict],
    source: str = "用户上传",
) -> Dict:
    """
    批量保存课表条目（清空旧数据后全量替换）

    Args:
        user_id: 用户 UUID
        courses: List[dict]，每项包含:
            course_name, teacher, location, day_of_week,
            start_section, end_section, start_week, end_week, week_type
        source: 课表来源文件名（方便溯源）

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
            # 全量替换：先删旧数据
            cur.execute("DELETE FROM curriculum WHERE user_id = ?", (user_id,))

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
                          (user_id, course_name, teacher, location,
                           day_of_week, start_section, end_section,
                           start_week, end_week, week_type,
                           source, uploaded_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
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


def load_curriculum(user_id: str) -> List[Dict]:
    """加载用户全部课表条目，按 day_of_week + start_section 排序"""
    if not user_id:
        return []
    conn = init_db(user_id)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, course_name, teacher, location,
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
                "course_name": r[1],
                "teacher": r[2],
                "location": r[3],
                "day_of_week": r[4],
                "start_section": r[5],
                "end_section": r[6],
                "start_week": r[7],
                "end_week": r[8],
                "week_type": r[9],
                "source": r[10],
                "uploaded_at": r[11],
            }
            for r in rows
        ]
    finally:
        conn.close()


def clear_curriculum(user_id: str) -> int:
    """清空用户课表（删除前提醒）"""
    if not user_id:
        return 0
    lock = _get_lock(user_id)
    with lock:
        conn = init_db(user_id)
        try:
            cur = conn.cursor()
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