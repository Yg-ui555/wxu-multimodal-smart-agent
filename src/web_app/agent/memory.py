"""
用户记忆（SQLite WAL，2026-09-22）
存用户偏好 + 对话历史，per-user cookie 隔离。

设计：
    - 单文件 data/app.db，WAL 模式提升并发读写
    - 两张表：preferences（键值对，存"我叫张三"之类）、history（对话流水）
    - 线程锁防并发写冲突
    - 全函数式 + pathlib + 类型注解

API：
    save(uid, role, content, intent)        # 写一条对话
    load_recent(uid, limit=10)              # 读最近 N 条对话
    set_preference(uid, key, value)         # 写偏好（如 name=张三）
    get_preference(uid, key, default)       # 读偏好
    get_all_preferences(uid)                # 读全部偏好（供 LLM 注入上下文）
"""

import sqlite3
import threading
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

# 项目根目录（agent/ 在 src/web_app/ 下，往上三层）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "app.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL,          -- 'user' / 'assistant'
    content     TEXT NOT NULL,
    intent      TEXT,                   -- 意图标签（可空）
    created_at  TEXT NOT NULL           -- ISO 时间
);
CREATE INDEX IF NOT EXISTS idx_hist_uid_time ON history(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS preferences (
    user_id     TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""

# 全局连接锁（SQLite 写串行）
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    """打开连接 + 启用 WAL + 建表"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10.0)
    # 开启 WAL：读不阻塞写、写不阻塞读，适合 FastAPI 并发
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")  # WAL 下安全且更快
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def save(uid: str, role: str, content: str, intent: Optional[str] = None) -> int:
    """
    写一条对话记录
    Args:
        uid: 用户 ID（cookie）
        role: 'user' 或 'assistant'
        content: 对话内容
        intent: 意图标签（可空）
    Returns: 插入的 row id
    """
    if not uid or not role or not content:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO history(user_id, role, content, intent, created_at) VALUES (?,?,?,?,?)",
                (uid, role, content[:2000], intent, now),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def load_recent(uid: str, limit: int = 10) -> List[Dict]:
    """
    读用户最近 N 条对话（按时间正序，方便喂给 LLM 做多轮上下文）
    """
    if not uid:
        return []
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            # 子查询倒序取 N 条，外层再正序，保证最近的在最后
            cur.execute(
                """
                SELECT role, content, intent, created_at FROM (
                    SELECT role, content, intent, created_at
                    FROM history WHERE user_id = ?
                    ORDER BY created_at DESC LIMIT ?
                ) ORDER BY created_at ASC
                """,
                (uid, limit),
            )
            return [
                {"role": r[0], "content": r[1], "intent": r[2], "ts": r[3]}
                for r in cur.fetchall()
            ]
        finally:
            conn.close()


def set_preference(uid: str, key: str, value: str) -> bool:
    """写/更新一条偏好（如 name=张三）"""
    if not uid or not key:
        return False
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO preferences(user_id, key, value, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (uid, key, str(value)[:500], now),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def get_preference(uid: str, key: str, default: Optional[str] = None) -> Optional[str]:
    """读一条偏好"""
    if not uid or not key:
        return default
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT value FROM preferences WHERE user_id=? AND key=?",
                (uid, key),
            )
            row = cur.fetchone()
            return row[0] if row else default
        finally:
            conn.close()


def get_all_preferences(uid: str) -> Dict[str, str]:
    """读用户全部偏好（供 LLM 上下文注入）"""
    if not uid:
        return {}
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM preferences WHERE user_id=?", (uid,))
            return {r[0]: r[1] for r in cur.fetchall()}
        finally:
            conn.close()


if __name__ == "__main__":
    # 自测
    test_uid = "test-mem-001"
    print("=== 写对话 ===")
    save(test_uid, "user", "你好")
    save(test_uid, "assistant", "你好，我是小U")
    save(test_uid, "user", "我叫张三")
    print("=== 读最近 3 条 ===")
    for h in load_recent(test_uid, 3):
        print(f"  [{h['role']}] {h['content']}")
    print("=== 写偏好 name=张三 ===")
    set_preference(test_uid, "name", "张三")
    set_preference(test_uid, "name", "张三丰")  # 覆盖
    print("=== 读偏好 ===")
    print("  name =", get_preference(test_uid, "name"))
    print("  全部:", get_all_preferences(test_uid))
