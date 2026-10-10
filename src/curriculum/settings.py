"""
用户设置（2026-10-09 新增）
每个用户一份独立设置：开学日期、班级（用于课表周次自动判断 + 班级维度）
存储：data/sqlite/user_settings.db（所有用户共享一份 db，按 user_id 隔离）

字段：
    user_id              TEXT PRIMARY KEY
    semester_start_date  TEXT NOT NULL    -- "2026-09-01" 开学日期
    class_id             INTEGER          -- 班级编号 1/2/3，NULL 表示未设置
    class_name           TEXT             -- 班级名（例"电信2201"），自定义
    updated_at           TEXT             -- ISO 时间

API：
    get_settings(user_id) -> dict         -- 没记录返回默认值（开学日=今天=第1周）
    save_settings(user_id, start_date, class_id=None, class_name=None) -> bool
"""
import sqlite3
import threading
from pathlib import Path
from datetime import date, timedelta
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_DIR = PROJECT_ROOT / "data" / "sqlite"
DB_DIR.mkdir(parents=True, exist_ok=True)

# 全局共享一份 settings.db（按 user_id 区分）
SETTINGS_DB_PATH = DB_DIR / "user_settings.db"
_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id              TEXT PRIMARY KEY,
    semester_start_date  TEXT NOT NULL,
    class_id             INTEGER,
    class_name           TEXT,
    updated_at           TEXT
);
"""


def _conn() -> sqlite3.Connection:
    """打开连接 + 建表"""
    c = sqlite3.connect(str(SETTINGS_DB_PATH), check_same_thread=False, timeout=10.0)
    c.executescript(SCHEMA)
    return c


def get_settings(user_id: str) -> Dict:
    """读 user_settings; 没有就给默认值（开学日=今天=第1周）"""
    with _LOCK:
        c = _conn()
        row = c.execute(
            "SELECT user_id, semester_start_date, class_id, class_name, updated_at "
            "FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        c.close()

    if row:
        return {
            "user_id": row[0],
            "semester_start_date": row[1],
            "class_id": row[2],
            "class_name": row[3],
            "updated_at": row[4],
        }
    # 默认值: 开学日=今天(默认当前就是第 1 周)
    return {
        "user_id": user_id,
        "semester_start_date": date.today().isoformat(),
        "class_id": None,
        "class_name": None,
        "updated_at": None,
    }


def save_settings(user_id: str,
                  semester_start_date: str,
                  class_id: Optional[int] = None,
                  class_name: Optional[str] = None) -> bool:
    """upsert user_settings"""
    if not semester_start_date:
        return False
    # 简单校验日期格式
    try:
        date.fromisoformat(semester_start_date)
    except ValueError:
        return False

    with _LOCK:
        c = _conn()
        now = date.today().isoformat()
        c.execute("""
            INSERT INTO user_settings
                (user_id, semester_start_date, class_id, class_name, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                semester_start_date = excluded.semester_start_date,
                class_id = excluded.class_id,
                class_name = excluded.class_name,
                updated_at = excluded.updated_at
        """, (user_id, semester_start_date, class_id, class_name, now))
        c.commit()
        c.close()
    return True
