# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. Embedding 原理（文本 → 1024 维向量，余弦相似度）
# 2. OllamaEmbeddings 调用本地 bge-m3 中文向量模型（替代英文 nomic-embed-text）
#    → 解决中文同义词/口语化召回失效（"门电路↔组合逻辑电路"）
# 3. FAISS 向量库的 CRUD（from_documents / add_documents / save_local / load_local）
# 4. 增量保存策略（每批 add_documents 后立即 save_local，避免大批量重建）
# 5. 断点续跑（progress.json 记录 last_completed_batch，重启自动跳过已完成）
# 6. stdout 心跳（PROGRESS: batch X/Y 防止后台监控误判进程 hang）
#
# 💡 面试杀手锏：讲清楚"为什么需要每批保存 + 断点续跑 + 心跳"
#    → 13000+ 块 embedding 需 30 分钟，中途进程被杀如何不丢任何进度
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

"""
FAISS 向量库：将分块文本转为向量，存入本地 FAISS
使用 Ollama 嵌入模型

支持：断点续跑 + 每批保存 + stdout 心跳
"""

import os
import sys
import json
import pickle
import time
import threading
import logging
from datetime import datetime
from typing import List, Optional
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings
from langchain_core.documents import Document

log = logging.getLogger("vector_store")

PROGRESS_FILE_NAME = "build_progress.json"


# ============================================================
# ⚠️ 临时兼容补丁（2026-09-17 注入）
# ============================================================
# 触发条件：venv = pydantic 2.10.6 + langchain_community 0.2.19 + Python 3.8
# 报错信息：FAISS.load_local 时 pydantic v1 compat 的 BaseModel.__setstate__
#          抛 KeyError: '__fields_set__'
# 根因：bge 库是 9/16 用旧版 pydantic v1 写的 pickle，新版 v2 compat shim
#      对 __fields_set__ 字段做了更严格检查
# 临时方案：给缺失字段兜底，确保反序列化能完成
#
# 正式方案：用 Python 3.11 重建一次向量库，pickle 自然就是 pydantic v2 写的，之后可删本段
# 当前状态（2026-10-30）：Python 已经升到 3.11.9，但 data/faiss_db_bge 里的
#   documents.pkl 仍是 9/16 在 Python 3.8 下生成的 —— 所以补丁暂时还不能删。
#   删除前置条件：在 3.11 下跑一次 `python src/rag/build_vectorstore.py` 重建向量库，
#   确认不再抛 __fields_set__ 之后，再把下面这段兼容代码删掉。
# ============================================================
def _compat_patch_for_legacy_pickle():
    """给 pydantic v1 compat 的 BaseModel.__setstate__ 兜底缺字段。幂等。"""
    try:
        from pydantic.v1 import main as _pyd_main
        if getattr(_pyd_main.BaseModel.__setstate__, "_patched_for_v2_legacy_pickle", False):
            return
        _orig_setstate = _pyd_main.BaseModel.__setstate__

        def _patched_setstate(self, state):
            # 旧版 pickle 漏了 __fields_set__ 字段，补一个空集合
            if "__fields_set__" not in state:
                state["__fields_set__"] = set()
            return _orig_setstate(self, state)

        _patched_setstate._patched_for_v2_legacy_pickle = True
        _pyd_main.BaseModel.__setstate__ = _patched_setstate
    except Exception:
        # 升级后删掉本函数时，pydantic.v1 还在 ImportError；静默忽略
        log.warning("pydantic v1 兼容补丁注入失败，旧版 pickle 反序列化可能报错", exc_info=True)
        pass


_compat_patch_for_legacy_pickle()


def _read_progress(persist_directory: str) -> int:
    """读取上次的构建进度，返回已完成的最大批次号（从 0 开始）"""
    path = os.path.join(persist_directory, PROGRESS_FILE_NAME)
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("last_completed_batch", 0))
    except Exception as e:
        log.warning(f"读取构建进度失败，按 0 处理: {path} ({e})")
        return 0


def _write_progress(persist_directory: str, batch_no: int,
                    total_batches: Optional[int] = None,
                    total_chunks: Optional[int] = None,
                    done: bool = False) -> None:
    """写入构建进度"""
    path = os.path.join(persist_directory, PROGRESS_FILE_NAME)
    payload = {
        "last_completed_batch": batch_no,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if total_batches is not None:
        payload["total_batches"] = total_batches
    if total_chunks is not None:
        payload["total_chunks"] = total_chunks
    if done:
        payload["done"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


class VectorStoreManager:
    """FAISS 向量库管理器"""

    def __init__(
        self,
        persist_directory: str = "data/faiss_db_bge",
        embedding_model: str = "bge-m3"
    ):
        """
        初始化向量库管理器
        Args:
            persist_directory: FAISS 持久化目录（bge-m3 1024 维，旧库 768 维不兼容）
            embedding_model: Ollama 嵌入模型名称（默认 bge-m3 中文向量模型）
        """
        self.persist_directory = persist_directory
        self.embedding_model = embedding_model

        # 初始化嵌入模型（使用 Ollama）
        self.embeddings = OllamaEmbeddings(
            model=embedding_model,
            base_url=os.getenv("EMBED_BASE_URL", "http://127.0.0.1:11434")
        )

        # 向量库实例（延迟初始化）
        self._vectorstore = None
        self._documents = None  # 保存原始文档

        # 写锁（2026-09-17：用户上传文档或 daily_crawl 增量时，
        # 多个线程可能并发触发 add_documents，不加锁会导致
        # FAISS 内部状态被并发 add_documents 写坏）
        self._write_lock = threading.RLock()

    @property
    def vectorstore(self) -> Optional[FAISS]:
        """获取向量库实例"""
        return self._vectorstore

    def create_vectorstore(self, documents: List[Document], collection_name: str = "wxu_knowledge") -> FAISS:
        """
        从文档创建向量库（带断点续跑 + 每批保存）
        Args:
            documents: 分块后的文档列表
            collection_name: Collection 名称（用于保存文档）
        Returns:
            FAISS 向量库实例
        """
        if not documents:
            raise ValueError("文档列表为空")

        # 确保目录存在
        os.makedirs(self.persist_directory, exist_ok=True)

        # 分批创建向量库，每批 50 个文档（更频繁的进度反馈）
        batch_size = 50
        all_docs = documents
        total_batches = (len(all_docs) + batch_size - 1) // batch_size

        # 断点续跑：检测上次进度
        last_completed = _read_progress(self.persist_directory)
        faiss_index_path = os.path.join(self.persist_directory, "index.faiss")
        if last_completed > 0 and os.path.exists(faiss_index_path):
            log.info(
                f"🔁 检测到上次进度：已完成 {last_completed}/{total_batches} 批，"
                f"从第 {last_completed + 1} 批继续"
            )
            # 加载已有向量库
            self._vectorstore = FAISS.load_local(
                self.persist_directory,
                self.embeddings,
                allow_dangerous_deserialization=True,
            )
            # 加载已保存的文档
            doc_path = os.path.join(self.persist_directory, "documents.pkl")
            saved_docs = []
            if os.path.exists(doc_path):
                try:
                    with open(doc_path, "rb") as f:
                        saved_docs = pickle.load(f)
                except Exception as e:
                    log.warning(f"断点续跑时 documents.pkl 读取失败，已保存文档降级为空: {doc_path} ({e})")
                    saved_docs = []
            self._documents = saved_docs
            # 写 stdout 心跳，避免后台进程被误判
            print(f"RESUME: from batch {last_completed + 1}/{total_batches}", flush=True)
        else:
            self._vectorstore = None
            self._documents = []
            last_completed = 0
            # 清除旧 progress.json（避免与新数据不一致）
            progress_path = os.path.join(self.persist_directory, PROGRESS_FILE_NAME)
            if os.path.exists(progress_path):
                try:
                    os.remove(progress_path)
                except Exception:
                    pass
            # 写 stdout 心跳
            print(f"START: 0/{total_batches}", flush=True)

        embed_started = time.time()

        # 累计成功批次（修复 2026-09-17：CRITICAL #1
        #   旧代码不管成败都写 done=True，导致 Ollama 中断时残缺库被误标完成，
        #   下次启动直接跳过 → 残缺数据偷偷上线）
        success_count = last_completed

        # 从 last_completed+1 开始
        start_idx = last_completed * batch_size
        for i in range(start_idx, len(all_docs), batch_size):
            batch = all_docs[i:i+batch_size]
            batch_no = i // batch_size + 1
            t0 = time.time()
            log.info(
                f"[阶段 2] 处理批次 {batch_no}/{total_batches} "
                f"({len(batch)} 个文档块) ..."
            )
            try:
                if self._vectorstore is None:
                    self._vectorstore = FAISS.from_documents(
                        documents=batch,
                        embedding=self.embeddings
                    )
                else:
                    self._vectorstore.add_documents(batch)

                # ★ 关键：每批成功后立刻保存（中断不浪费）
                if self._documents is None:
                    self._documents = []
                # 追加本批原始 chunks
                self._documents.extend(batch)
                self._save_vectorstore()
                _write_progress(
                    self.persist_directory,
                    batch_no=batch_no,
                    total_batches=total_batches,
                    total_chunks=len(all_docs),
                )

                success_count += 1  # ← 仅成功路径计数

                dt = time.time() - t0
                # 剩余时间估算（基于本会话进度，不含已加载部分）
                elapsed_this_session = time.time() - embed_started
                done_in_session = batch_no - last_completed
                speed = done_in_session / elapsed_this_session if elapsed_this_session > 0 else 0
                remain_batches = total_batches - batch_no
                remain_sec = remain_batches / speed if speed > 0 else 0
                m, s = divmod(int(remain_sec), 60)
                h, m = divmod(m, 60)
                remain_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

                log.info(
                    f"[阶段 2] 批次 {batch_no}/{total_batches} 完成 "
                    f"({dt:.1f}s)  预计剩余 {remain_str}"
                )

                # ★ stdout 心跳（防后台监控误判为 hang）
                print(
                    f"PROGRESS: batch {batch_no}/{total_batches} "
                    f"chunks={min(i + batch_size, len(all_docs))}/{len(all_docs)} "
                    f"remain={remain_str}",
                    flush=True,
                )
            except Exception as e:
                log.warning(f"[阶段 2] 批次 {batch_no} 处理失败: {e}")
                # 失败也写 stdout 心跳
                print(f"WARN: batch {batch_no} failed: {e}", flush=True)
                # ⚠️ 不增加 success_count；下次启动 _read_progress 会读到旧值继续
                continue

        # 仅当所有批次全部成功才标记 done=True（修复 CRITICAL #1）
        all_succeeded = (success_count == total_batches)
        _write_progress(
            self.persist_directory,
            batch_no=success_count if not all_succeeded else total_batches,
            total_batches=total_batches,
            total_chunks=len(all_docs),
            done=all_succeeded,
        )

        if all_succeeded:
            log.info(f"[阶段 2] 向量库创建完成: {len(self._documents or [])} 个文档块")
            print(f"DONE: {total_batches}/{total_batches} chunks={len(all_docs)}", flush=True)
        else:
            log.warning(
                f"[阶段 2] 向量库未完成: {success_count}/{total_batches} 批成功，"
                f"下次启动从第 {success_count + 1} 批继续"
            )
            print(
                f"PARTIAL: {success_count}/{total_batches} chunks={len(self._documents or [])} "
                f"resume_from={success_count + 1}",
                flush=True,
            )
        return self._vectorstore

    def _save_vectorstore(self):
        """保存向量库到磁盘"""
        if self._vectorstore is not None:
            self._vectorstore.save_local(self.persist_directory)
            # 保存原始文档
            doc_path = os.path.join(self.persist_directory, "documents.pkl")
            with open(doc_path, "wb") as f:
                pickle.dump(self._documents, f)
            log.info(f"向量库已保存到: {self.persist_directory}")

    def load_vectorstore(self, collection_name: str = "wxu_knowledge") -> FAISS:
        """
        加载已存在的向量库
        Args:
            collection_name: Collection 名称（保留参数，未实际使用）
        Returns:
            FAISS 向量库实例
        """
        if not os.path.exists(self.persist_directory):
            raise FileNotFoundError(f"向量库目录不存在: {self.persist_directory}")

        # 加载向量库
        self._vectorstore = FAISS.load_local(
            self.persist_directory,
            self.embeddings,
            allow_dangerous_deserialization=True
        )

        # 加载原始文档（修复 2026-09-17：与 create_vectorstore 路径对称加 try/except）
        doc_path = os.path.join(self.persist_directory, "documents.pkl")
        if os.path.exists(doc_path):
            try:
                with open(doc_path, "rb") as f:
                    self._documents = pickle.load(f)
            except Exception as e:
                log.warning(f"documents.pkl 加载失败，降级为仅向量检索: {e}")
                self._documents = []

        print(f"向量库加载完成: {len(self._documents) if self._documents else 0} 个文档")
        return self._vectorstore

    def add_documents(self, documents: List[Document]) -> None:
        """
        添加新文档到向量库

        修复 2026-09-17：
        - 加 self._write_lock 锁，防并发 add_documents 写坏 FAISS
        - 若 documents 已有 content_hash 字段，callers 自己 dedup；
          这里不再硬过滤（避免双重去重语义出错）
        """
        if not documents:
            return

        with self._write_lock:
            if self._vectorstore is None:
                self.load_vectorstore()

            # 添加文档
            self._vectorstore.add_documents(documents)

            # 更新原始文档列表
            if self._documents is None:
                self._documents = []
            self._documents.extend(documents)

            # 重新保存
            self._save_vectorstore()

            print(f"新增 {len(documents)} 个文档块")

    def similarity_search(self, query: str, k: int = 4) -> List[Document]:
        """
        相似度检索
        Args:
            query: 查询文本
            k: 返回 Top-K 个结果
        Returns:
            相关的文档列表
        """
        if self._vectorstore is None:
            raise RuntimeError("向量库未初始化，请先创建或加载向量库")

        return self._vectorstore.similarity_search(query, k=k)

    def similarity_search_with_score(self, query: str, k: int = 4) -> List[tuple]:
        """
        相似度检索（带分数）
        Args:
            query: 查询文本
            k: 返回 Top-K 个结果
        Returns:
            (文档, 分数) 元组列表
        """
        if self._vectorstore is None:
            raise RuntimeError("向量库未初始化，请先创建或加载向量库")

        return self._vectorstore.similarity_search_with_score(query, k=k)

    def as_retriever(self, search_kwargs: dict = None):
        """
        转换为 LangChain Retriever
        Args:
            search_kwargs: 检索参数，如 {"k": 4}
        Returns:
            Retriever 对象
        """
        if self._vectorstore is None:
            raise RuntimeError("向量库未初始化")

        if search_kwargs is None:
            search_kwargs = {"k": 4}

        return self._vectorstore.as_retriever(search_kwargs=search_kwargs)


def create_vectorstore_from_docs(
    documents: List[Document],
    persist_directory: str = "data/faiss_db_bge",
    collection_name: str = "wxu_knowledge"
) -> FAISS:
    """
    便捷函数：从文档创建向量库
    Args:
        documents: 文档列表
        persist_directory: 持久化目录
        collection_name: Collection 名称
    Returns:
        FAISS 向量库
    """
    manager = VectorStoreManager(persist_directory=persist_directory)
    return manager.create_vectorstore(documents, collection_name)


if __name__ == "__main__":
    # 测试
    from data_loader import load_and_chunk

    print("加载文档...")
    chunks = load_and_chunk("data/docs")

    if chunks:
        print("\n创建向量库 (bge-m3)...")
        vs = create_vectorstore_from_docs(chunks)

        print("\n测试检索...")
        results = vs.similarity_search("图书馆开放时间", k=2)
        for i, doc in enumerate(results):
            print(f"\n--- 结果 {i+1} ---")
            print(doc.page_content[:200])
