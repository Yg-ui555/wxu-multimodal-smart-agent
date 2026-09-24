"""
Query embedding 缓存(2026-09-24 加)

背景:每次问问题都要调 Ollama bge-m3 把 query 编码成向量,CPU 跑要 5-10s
对同一 query,答案基本一致 → 缓存 query 的 embedding 结果

设计:
- key = query 字符串(去前后空格)
- value = numpy array(1024 维)
- TTL = 5 分钟(让热点 query 命中)
- 大小上限 = 1000 条(防内存爆炸,LRU 淘汰)

用法:
    from src.rag.query_cache import query_embedding_cache
    if query_embedding_cache.has(query):
        vec = query_embedding_cache.get(query)
    else:
        vec = encode(query)
        query_embedding_cache.set(query, vec)
"""

import time
import hashlib
import numpy as np
from collections import OrderedDict
from typing import Optional


class QueryEmbeddingCache:
    """LRU + TTL 的 query embedding 缓存"""

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: "OrderedDict[str, tuple[float, np.ndarray]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def _key(self, query: str) -> str:
        """标准化 key(去前后空格 + 小写),长 query 截断"""
        q = (query or "").strip()
        if len(q) > 500:
            q = q[:500]
        return q.lower()

    def has(self, query: str) -> bool:
        """检查是否在缓存(有效期内)"""
        key = self._key(query)
        if key not in self._cache:
            return False
        ts, _ = self._cache[key]
        if time.time() - ts > self.ttl_seconds:
            # 过期,惰性清理
            del self._cache[key]
            return False
        # LRU:命中后移到末尾(最近用)
        self._cache.move_to_end(key)
        return True

    def get(self, query: str) -> Optional[np.ndarray]:
        """获取 embedding(过期返回 None)"""
        key = self._key(query)
        if key not in self._cache:
            self.misses += 1
            return None
        ts, vec = self._cache[key]
        if time.time() - ts > self.ttl_seconds:
            del self._cache[key]
            self.misses += 1
            return None
        self._cache.move_to_end(key)
        self.hits += 1
        return vec.copy()  # 返回副本防外部修改

    def set(self, query: str, vec: np.ndarray):
        """存入缓存(自动 LRU 淘汰)"""
        key = self._key(query)
        self._cache[key] = (time.time(), vec.copy())
        self._cache.move_to_end(key)
        # 超容量淘汰最早最少用的
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)

    def stats(self) -> dict:
        """命中率统计(给监控面板用)"""
        total = self.hits + self.misses
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl_seconds,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }

    def clear(self):
        """清空(调试用)"""
        self._cache.clear()
        self.hits = 0
        self.misses = 0


# 全局单例
query_embedding_cache = QueryEmbeddingCache(max_size=1000, ttl_seconds=300)