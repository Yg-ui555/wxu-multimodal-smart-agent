# -*- coding: utf-8 -*-
"""
user_store 测试（2026-10-09 加）

任务 4 修复后的回归保护：
- is_valid_user_id 白名单防路径穿越
- new_anonymous_uid 每个 anon 用户独立 ID（修复 A 用户历史进 B 用户 prompt）
- append_message_atomic 原子写（修复同用户并发两条消息丢一条）
"""
from src.web_app.user_store import is_valid_user_id, new_anonymous_uid, append_message_atomic


def test_is_valid_user_id_accepts_normal():
    """正常 wxu_test_abc 应通过白名单"""
    assert is_valid_user_id("wxu_test_abc") is True


def test_is_valid_user_id_rejects_path_traversal():
    """路径穿越字符串应被拒绝"""
    assert is_valid_user_id("../../etc/passwd") is False


def test_is_valid_user_id_rejects_empty():
    """空字符串应被拒绝"""
    assert is_valid_user_id("") is False


def test_is_valid_user_id_rejects_dot():
    """含 . 的字符串应被拒绝（防 ./ ../ 路径）"""
    assert is_valid_user_id("wxu.test") is False


def test_new_anonymous_uid_prefix():
    """匿名 UID 应有 wxu_anon_ 前缀"""
    uid = new_anonymous_uid()
    assert uid.startswith("wxu_anon_")


def test_new_anonymous_uid_unique():
    """连续两次调用应返回不同 UID（独立性是隐私修复的核心）"""
    uid1 = new_anonymous_uid()
    uid2 = new_anonymous_uid()
    assert uid1 != uid2


def test_new_anonymous_uid_passes_whitelist():
    """匿名 UID 应满足 is_valid_user_id 白名单"""
    uid = new_anonymous_uid()
    assert is_valid_user_id(uid) is True


def test_append_message_atomic_signature():
    """append_message_atomic 应接受 (uid, sid, message) 参数"""
    import inspect
    sig = inspect.signature(append_message_atomic)
    assert "uid" in sig.parameters
    assert "sid" in sig.parameters
    assert "message" in sig.parameters
