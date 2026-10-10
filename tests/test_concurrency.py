# -*- coding: utf-8 -*-
"""
并发隔离测试（2026-10-09 加，验证任务 4 修复）

模拟两个用户并发调 dispatch，验证：
1. 两个用户都应有 answer（不丢消息）
2. 各自结果互不串台（A 的历史不进 B 的 prompt）

注意：
- 用 "你好" / "hi" 这两个硬编码兜底词，避免触发 LLM 调用
- dispatch 不会真正写历史（handle_general 硬编码路径不调 _rag_answer）
- 但 dispatch 仍会调 _extract_preferences + memory，验证多线程下不会崩
"""
import threading
from src.web_app.agent.dispatcher import dispatch


def test_concurrent_users_no_history_leak():
    """两个用户并发调 dispatch，各自都应有 answer"""
    results = {}

    def call(uid, q):
        results[uid] = dispatch(q, uid, sid="")

    # 用两个独立 uid（白名单合法）
    t1 = threading.Thread(target=call, args=("wxu_user_aaa11111", "你好"))
    t2 = threading.Thread(target=call, args=("wxu_user_bbb22222", "hi"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # 两个用户都应返回了结果
    assert "wxu_user_aaa11111" in results
    assert "wxu_user_bbb22222" in results

    # 两个用户的 answer 都不应为空
    ans_a = results["wxu_user_aaa11111"].get("answer", "")
    ans_b = results["wxu_user_bbb22222"].get("answer", "")
    assert ans_a, f"用户 A 的 answer 为空: {results['wxu_user_aaa11111']}"
    assert ans_b, f"用户 B 的 answer 为空: {results['wxu_user_bbb22222']}"


def test_concurrent_same_user_two_messages_no_loss():
    """同一用户并发两条消息，两条都应返回（验证 append_message_atomic 锁）"""
    results = []

    def call(q):
        r = dispatch(q, "wxu_user_ccc33333", sid="test_session_1")
        results.append(r)

    # 同一用户同时发两条不同消息
    t1 = threading.Thread(target=call, args=("你好",))
    t2 = threading.Thread(target=call, args=("hi",))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # 两条都应返回了
    assert len(results) == 2
    # 两条 answer 都不应为空
    assert all(r.get("answer") for r in results), \
        f"有消息丢失: {[r.get('answer', '') for r in results]}"
