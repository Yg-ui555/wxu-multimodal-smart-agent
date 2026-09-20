# ============================================================
# 用户会话持久化（多 session / 多对话版本，2026-09-17 升级）
# ============================================================
# 设计（参考豆包）：
# - 每个 user_id 拥有独立的"对话文件夹"：data/user_histories/<uid>/
# - 里面有 index.json（session 列表 + 标题 + 时间戳）
# - 每次对话 state 写在 chat_<sid>.pkl
# - 上限 200 个 session / user，超出走 FIFO（最久未用的 flush 后驱逐）
#
# 关键 API：
# - list_sessions(user_id) -> [(session_id, meta), ...] 按 last_active_at 倒序
# - new_session(user_id, title=None) -> session_id
# - save_state(user_id, sid, state) -> bool
# - load_state(user_id, sid) -> dict | None
# - delete_session(user_id, sid) -> bool
# - resolve_user_id(request) -> user_id (cookie → 输入 → IP+UA hash)
# ============================================================

import os
import re
import json
import pickle
import time
import threading
import hashlib
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple


_BASE = Path(__file__).resolve().parent.parent.parent / "data" / "user_histories"
_BASE.mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()  # 跨线程写 disk 序列化

# user_id 合法性白名单：仅 [A-Za-z0-9_-]，长度 1-128
_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")
# session_id 同样严格白名单
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def is_valid_user_id(uid: str) -> bool:
    return bool(uid) and bool(_USER_ID_PATTERN.match(uid))


def is_valid_session_id(sid: str) -> bool:
    return bool(sid) and bool(_SESSION_ID_PATTERN.match(sid))


def _user_dir(uid: str) -> Path:
    return _BASE / uid


def _index_path(uid: str) -> Path:
    return _user_dir(uid) / "index.json"


def _state_path(uid: str, sid: str) -> Path:
    return _user_dir(uid) / f"chat_{sid}.pkl"


def _atomic_write_bytes(path: Path, payload: bytes) -> bool:
    """原子写：先写 .tmp 后 rename，避免半文件状态。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(payload)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        return False


def _atomic_write_text(path: Path, text: str) -> bool:
    return _atomic_write_bytes(path, text.encode("utf-8"))


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _read_pickle(path: Path) -> Optional[dict]:
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


# ============================================================
# Public API
# ============================================================
def list_sessions(uid: str) -> List[Tuple[str, dict]]:
    """
    返回该用户所有 session 的 (sid, meta) 列表，按 last_active_at 降序。
    meta 字段：{title, created_at, last_active_at, preview}
    """
    if not is_valid_user_id(uid):
        return []
    p = _index_path(uid)
    if not p.exists():
        return []
    idx = _read_json(p)
    sessions = idx.get("sessions", {})
    items = [(sid, m) for sid, m in sessions.items()]
    items.sort(key=lambda kv: kv[1].get("last_active_at", 0), reverse=True)
    return items


def new_session(uid: str, title: Optional[str] = None) -> Optional[str]:
    """
    新建一个 session（不创建 chat state 文件，待首次 chat 时再懒加载）。
    返回新 sid，失败返回 None。
    """
    if not is_valid_user_id(uid):
        return None
    sid = uuid.uuid4().hex[:16]
    _user_dir(uid).mkdir(parents=True, exist_ok=True)
    now = time.time()
    p = _index_path(uid)
    with _LOCK:
        idx = _read_json(p)
        sessions = idx.setdefault("sessions", {})
        sessions[sid] = {
            "title": (title or "新对话").strip()[:64],
            "created_at": now,
            "last_active_at": now,
            "preview": "",
            "msg_count": 0,
        }
        ok = _atomic_write_text(p, json.dumps(idx, ensure_ascii=False, indent=2))
    return sid if ok else None


def update_meta(uid: str, sid: str, meta_patch: dict) -> bool:
    """更新单个 session 的 meta 字段（title / preview / last_active_at / msg_count）"""
    if not (is_valid_user_id(uid) and is_valid_session_id(sid)):
        return False
    p = _index_path(uid)
    with _LOCK:
        idx = _read_json(p)
        sessions = idx.setdefault("sessions", {})
        if sid not in sessions:
            return False
        sessions[sid].update(meta_patch)
        ok = _atomic_write_text(p, json.dumps(idx, ensure_ascii=False, indent=2))
    return ok


def touch(uid: str, sid: str) -> bool:
    """touch：刷新 last_active_at，并把对应 chat_<sid>.pkl 也存在（懒加载保证）"""
    return update_meta(uid, sid, {"last_active_at": time.time()})


def save_state(uid: str, sid: str, state: dict) -> bool:
    """保存某 session 的对话状态到磁盘"""
    if not (is_valid_user_id(uid) and is_valid_session_id(sid)):
        return False
    _user_dir(uid).mkdir(parents=True, exist_ok=True)
    p = _state_path(uid, sid)
    payload = dict(state)
    payload["_saved_at"] = time.time()
    payload["schema_version"] = state.get("schema_version", 1)
    with _LOCK:
        return _atomic_write_bytes(p, pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))


def load_state(uid: str, sid: str) -> Optional[dict]:
    """加载某 session 的对话状态"""
    if not (is_valid_user_id(uid) and is_valid_session_id(sid)):
        return None
    p = _state_path(uid, sid)
    if not p.exists():
        return None
    with _LOCK:  # 防止 dump 写到一半时读
        return _read_pickle(p)


def delete_session(uid: str, sid: str) -> bool:
    """删除一个 session（同时清 index + chat 文件）"""
    if not (is_valid_user_id(uid) and is_valid_session_id(sid)):
        return False
    with _LOCK:
        p_idx = _index_path(uid)
        if p_idx.exists():
            idx = _read_json(p_idx)
            sessions = idx.get("sessions", {})
            sessions.pop(sid, None)
            _atomic_write_text(p_idx, json.dumps(idx, ensure_ascii=False, indent=2))
        p_chat = _state_path(uid, sid)
        if p_chat.exists():
            try:
                p_chat.unlink()
            except Exception:
                pass
    return True


def delete_user(uid: str) -> bool:
    """完全删除某用户的所有 session（管理用）"""
    if not is_valid_user_id(uid):
        return False
    d = _user_dir(uid)
    if not d.exists():
        return True
    try:
        import shutil
        shutil.rmtree(d)
        return True
    except Exception:
        return False


def list_all_user_ids() -> list:
    return sorted(p.name for p in _BASE.iterdir() if p.is_dir())


# ============================================================
# User resolution（来自 cookie / 输入 / IP+UA hash）
# ============================================================
def resolve_user_id(request=None, fallback_input: str = "") -> str:
    """
    优先级：cookie wxu_uid > 显式 fallback_input > IP+UA hash（临时 anon）
    """
    cookies = getattr(request, "cookies", None) or {}
    uid = (cookies.get("wxu_uid") or "").strip()
    if is_valid_user_id(uid):
        return uid

    fb = (fallback_input or "").strip()
    if is_valid_user_id(fb):
        return fb

    ua = ""
    host = ""
    try:
        if request is not None:
            ua = request.headers.get("user-agent", "") if hasattr(request, "headers") else ""
            host = request.client.host if getattr(request, "client", None) else ""
    except Exception:
        pass
    h = hashlib.md5(f"{host}|{ua}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"anon_{h}"
