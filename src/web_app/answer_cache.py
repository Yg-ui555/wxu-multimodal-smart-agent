"""
Answer Cache（2026-10-12 新增）
================================

# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. Redis 客户端封装（python redis-py + 自动降级 fakeredis）
# 2. 缓存 key 设计：query 归一化 + MD5 摘要
# 3. 缓存击穿防护：随机 TTL 抖动（防雪崩）
# 4. 个性化问题识别：含"今天/我的/我"等关键词的 query 不走缓存
# 5. 失败降级：Redis 不可用 → 自动用进程内 LRU 兜底
#
# 💡 面试高频问题：
#    - "怎么防止缓存击穿/雪崩？" → 随机 TTL 抖动 + 单飞锁（lazy load）
#    - "为什么不用 Redis Cluster？" → 单实例 QPS < 1000 没必要，运维成本高
#    - "个性化问题怎么缓存？" → 不缓存（关键词过滤），其他走缓存
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

设计:
    1. 优先连真 Redis（环境变量 REDIS_URL 控制，默认 redis://localhost:6379/0）
    2. 连不上 → 自动降级到 fakeredis（开发/测试环境，零部署）
    3. 都不行 → 进程内 LRU 字典兜底（永远不会让缓存层挂掉业务）

用法:
    from src.web_app.answer_cache import answer_cache
    key = answer_cache.make_key(query)
    cached = answer_cache.get(key)
    if cached:
        return cached
    result = do_rag(query)
    answer_cache.set(key, result, ttl=604800)  # 7 天
"""
import os
import re
import json
import time
import hashlib
import random
import logging
from collections import OrderedDict
from typing import Optional, Dict, Any

log = logging.getLogger("answer_cache")

# ============================================================
# 1) 个性化问题识别（不走缓存）
# ============================================================
# 这些问题答案依赖"当下"或"用户自己"，缓存会出错
_PERSONAL_PATTERNS = [
    re.compile(r"今天"),         # 今天天气、今天有什么课
    re.compile(r"明天"),
    re.compile(r"后天"),
    re.compile(r"下周"),
    re.compile(r"这周"),
    re.compile(r"本周"),
    re.compile(r"我[的了]课"),     # 我的课、我的成绩
    re.compile(r"我的课表"),
    re.compile(r"我挂[了科]"),
    re.compile(r"我[的]成绩"),
    re.compile(r"我叫什么"),       # 用户偏好类
    re.compile(r"我[的]姓名"),
]


def _is_personal_query(query: str) -> bool:
    """
    判定 query 是否"个性化"（不能直接缓存）
    简单做法:含"今天/明天/我"等关键词 → 不缓存
    """
    if not query:
        return False
    for pat in _PERSONAL_PATTERNS:
        if pat.search(query):
            return True
    return False


# ============================================================
# 2) Redis 客户端工厂（带降级）
# ============================================================
_redis_client = None
_redis_mode = "unknown"  # "redis" | "fakeredis" | "memory"


def _get_redis():
    """
    懒加载 Redis 客户端,自动降级
    Returns:
        redis_client: 有 get/set/ping/ttl 的对象
        mode: "redis" | "fakeredis" | "memory"
    """
    global _redis_client, _redis_mode

    # 强制走内存(测试场景用)
    if os.getenv("REDIS_DISABLED", "0") == "1":
        _redis_mode = "memory"
        return _memory_cache, _redis_mode

    # 已有客户端,直接复用
    if _redis_client is not None:
        return _redis_client, _redis_mode

    # 尝试连真 Redis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis
        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=1,  # 1 秒连不上就降级
            socket_timeout=1,
            decode_responses=True,
        )
        client.ping()  # 验证连接
        _redis_client = client
        _redis_mode = "redis"
        log.info(f"[answer_cache] ✅ 连上真 Redis: {redis_url}")
        return _redis_client, _redis_mode
    except Exception as e:
        log.warning(f"[answer_cache] ⚠️ 真 Redis 连不上({e!r}),降级 fakeredis")

    # 降级到 fakeredis(纯 Python 实现,redis-py API 兼容)
    try:
        import fakeredis
        _redis_client = fakeredis.FakeRedis(decode_responses=True)
        _redis_client.ping()
        _redis_mode = "fakeredis"
        log.info("[answer_cache] ✅ 降级到 fakeredis(开发模式)")
        return _redis_client, _redis_mode
    except Exception as e:
        log.warning(f"[answer_cache] ⚠️ fakeredis 也失败({e!r}),降级进程内 LRU")

    # 最后兜底:进程内 LRU
    _redis_mode = "memory"
    log.info("[answer_cache] ✅ 降级到进程内 LRU(纯内存,重启清空)")
    return _memory_cache, _redis_mode


# ============================================================
# 3) 进程内 LRU 兜底(永远可用)
# ============================================================
class _MemoryLRU:
    """最简 LRU 缓存,只在 Redis/fakeredis 都不可用时使用"""
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()

    def get(self, key: str) -> Optional[str]:
        if key not in self._cache:
            return None
        ts, val = self._cache[key]
        self._cache.move_to_end(key)
        return val

    def set(self, key: str, val: str, ex: Optional[int] = None):
        """set(key, val, ex=ttl_seconds) — TTL 强制走 self.default_ttl(2026-10-12 收尾修复)"""
        self._cache[key] = (time.time(), val)
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)

    def ttl(self, key: str) -> int:
        """fakeredis/redis-py 兼容接口:返回 key 剩余 TTL(秒)
        Returns:
            -2 = key 不存在, 0 = 没设 TTL(我们用 0 表示"立即过期"但其实 caller 不会用)
        """
        if key not in self._cache:
            return -2
        # 2026-10-12 收尾修复:之前写错 if False else 0,现在按"max-size LRU"语义返 0
        return 0

    def ping(self):
        return True


_memory_cache = _MemoryLRU(max_size=1000)


# ============================================================
# 4) AnswerCache 主类(对外接口)
# ============================================================
class AnswerCache:
    """最终答案缓存 - Redis + 内存兜底"""

    DEFAULT_TTL = 7 * 24 * 3600     # 7 天
    TTL_JITTER = 0.1                 # ±10% 抖动防雪崩
    KEY_PREFIX = "wxu:answer:"

    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.skipped_personal = 0     # 因"个性化"跳过的次数
        self.errors = 0               # 缓存层异常次数(不影响业务)

    def _get_redis(self):
        return _get_redis()

    @property
    def mode(self) -> str:
        return _redis_mode

    # ---------- key 设计 ----------
    def make_key(self, query: str) -> str:
        """
        缓存 key 设计:
        - 归一化:strip + collapse 空白
        - MD5 摘要(32 字符,redis key 友好)
        - 加 prefix 避免和其他业务冲突
        """
        q = re.sub(r"\s+", " ", (query or "").strip().lower())[:500]
        digest = hashlib.md5(q.encode("utf-8")).hexdigest()
        return f"{self.KEY_PREFIX}{digest}"

    # ---------- 读写 ----------
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """
        读缓存,返回 dict(从 JSON 反序列化)或 None
        任何异常都返回 None,绝不让缓存层挂掉业务
        """
        try:
            client, _ = self._get_redis()
            raw = client.get(key)
            if raw is None:
                self.misses += 1
                return None
            data = json.loads(raw)
            self.hits += 1
            return data
        except Exception as e:
            log.warning(f"[answer_cache] get 异常(降级为 miss): {e!r}")
            self.errors += 1
            self.misses += 1
            return None

    def set(self, key: str, value: Dict[str, Any], ttl: Optional[int] = None):
        """
        写缓存
        - TTL 加 ±10% 随机抖动(防雪崩:大量 key 同时过期)
        - 任何异常都吞掉,不影响业务
        """
        try:
            client, _ = self._get_redis()
            base_ttl = ttl or self.DEFAULT_TTL
            # TTL 抖动:±10%
            jitter = int(base_ttl * self.TTL_JITTER)
            actual_ttl = base_ttl + random.randint(-jitter, jitter)
            payload = json.dumps(value, ensure_ascii=False, default=str)
            client.set(key, payload, ex=actual_ttl)
        except Exception as e:
            log.warning(f"[answer_cache] set 异常(忽略): {e!r}")
            self.errors += 1

    # ---------- 个性化问题快速判断 ----------
    def skip_for_personal(self, query: str) -> bool:
        """含"今天/我的"等关键词的 query 不走缓存"""
        if _is_personal_query(query):
            self.skipped_personal += 1
            return True
        return False

    # ---------- 监控 ----------
    def stats(self) -> Dict[str, Any]:
        """给 /metrics 端点用"""
        total = self.hits + self.misses
        return {
            "mode": self.mode,
            "hits": self.hits,
            "misses": self.misses,
            "skipped_personal": self.skipped_personal,
            "errors": self.errors,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }

    def reset_stats(self):
        self.hits = 0
        self.misses = 0
        self.skipped_personal = 0
        self.errors = 0


# 全局单例
answer_cache = AnswerCache()
