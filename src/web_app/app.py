# ============================================================
# ⚠️ 2026-09-22 此文件已废弃（DEPRICATED）
# ============================================================
# 原因：项目从 Gradio 单页迁移到 FastAPI 多页架构（详见 docs/ENGINEERING.md）
# 新入口：src/web_app/fastapi_app.py（端口 7862）
# RAG 核心已抽离到 src/rag/rag_engine.py
#
# 本文件仅保留作为历史代码 + 课程学习参考，**不要启动它**
# 如需查看豆包式多 session 隔离 / Gradio Blocks 布局教学，可继续阅读
#
# 杀旧进程：Stop-Process -Id 6548 -Force   # 7861 端口
# 启新服务：powershell -File start.ps1       # 7862 端口
# ============================================================

# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. Gradio Blocks 布局（gr.Row / gr.Column / gr.Tabs）
# 2. Gradio Chatbot 组件（多轮对话 UI）
# 3. 事件绑定（button.click / submit 提交）
# 4. **多用户 + 多 session 隔离**（每用户单独 dict cache、按 cookie 索引）
# 5. 侧栏 UI（gr.Radio 显示会话列表 + 新对话 / 删除 / 切换）
# 6. 启动配置（server_name / server_port / share）
#
# 💡 进阶方向（写到简历上）：
#    - "豆包"模式：侧栏历史对话 + 主区当前对话，每条 session 独立持久化
#    - 用户识别从 cookie 升级到 OAuth / 微信扫码
#    - LangGraph 替代手搓状态管理
#    - 加 Examples 组件（一键提问示例）
#    - 加 CSS 美化（gr.themes.Glass()）
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

"""
Gradio 网页应用：豆包式多 session 隔离 + 上传文档
- Tab 💬 智能问答：左侧 sessions 列表 + 右侧当前对话（每用户独立 + 每 session 独立）
- Tab 📤 上传文档：自动识别 → 入 bge 向量库

启动命令：python src/web_app/app.py

用户身份（2026-09-17）：
- 浏览器自动生成 UUID 写到 cookie（持久化用户）
- 后端每个用户多个 session；session_id 也写到 cookie 让刷新保持
"""

import os
import sys
import threading

# 将项目根目录添加到路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import gradio as gr
from src.rag.vector_store import VectorStoreManager
from src.rag.retriever import create_retriever
from src.rag.llm_chain import create_qa_system
from src.rag.chat_history import create_chat_manager
from src.yolo import detect_objects, detect_objects_world, format_detections_md, DEFAULT_WORLD_CLASSES
from src.ocr import extract_text, add_ocr_to_vectorstore
from src.web_app.upload_handler import process_uploads
from src.web_app import user_store
from src.web_app.image_assistant import look_at_image, build_context_for_chat
# 2026-09-21 我的课表（PDF/截图 → 周视图）
from src.curriculum import db as cv_db, parser as cv_parser, ui as cv_ui


class RAGApp:
    """RAG 问答应用 + 多用户多 session 隔离（2026-09-17 升级）"""

    def __init__(self):
        self.vectorstore = None
        self.retriever = None
        self.qa_system = None
        # 进程内 cache：user_id → {session_id → ChatHistoryManager 实例}
        # 实例里含 llm/retriever 不能 pickle；state 走 user_store.save_state 落盘
        self._user_cache: dict = {}
        self._user_cache_max_per_user = 50  # 每个 user 最多 50 个活跃 session
        self._cache_lock = threading.Lock()
        self.docs_dir = "data/docs"

    # ============================================================
    # Session 管理
    # ============================================================
    def _ensure_session(self, user_id: str, session_id: str = "") -> str:
        """
        拿一个有效的 session_id。
        - 若 session_id 校验合法且存在 → 返回
        - 否则 user 没 session 就建一个，user 有 session 就取最近 active 的那条
        """
        if user_id not in user_store.list_all_user_ids() + [user_id]:
            pass  # 此步只是容错，下面用 list_sessions 更直接

        sessions = user_store.list_sessions(user_id)
        session_ids = {sid for sid, _ in sessions}

        if session_id and session_id in session_ids:
            return session_id

        if not sessions:
            new_sid = user_store.new_session(user_id, title="默认对话")
            return new_sid or ""

        # 否则用最近 active 的
        return sessions[0][0]

    def _get_or_create_chat_manager(self, user_id: str, session_id: str):
        """拿（或新建 + load 磁盘）某 session 的 ChatHistoryManager。"""
        with self._cache_lock:
            cache = self._user_cache.setdefault(user_id, {})
            cm = cache.get(session_id)
            if cm is not None:
                return cm

            cm = create_chat_manager(
                self.qa_system.llm,
                self.retriever,
                use_simple=False,
            )
            saved = user_store.load_state(user_id, session_id)
            if saved:
                try:
                    cm.load_state_dict(saved)
                except Exception:
                    pass

            cache[session_id] = cm

            # LRU 简单实现：单 user 超过上限就淘汰
            if len(cache) > self._user_cache_max_per_user:
                oldest_sid = next(iter(cache))
                if oldest_sid != session_id:
                    self._flush_user_state_locked(user_id, oldest_sid)
                    cache.pop(oldest_sid, None)
            return cm

    def _flush_user_state_locked(self, user_id: str, session_id: str) -> bool:
        """保存某 session 状态 + 刷新 last_active + 自动 title/preview"""
        cm = self._user_cache.get(user_id, {}).get(session_id)
        if not cm:
            return False
        try:
            state = cm.to_state_dict()
            ok = user_store.save_state(user_id, session_id, state)
            if not ok:
                return False
            # 自动更新 title 与 preview
            meta = {}
            if cm.recent_history:
                first_q = cm.recent_history[0][0].strip()[:24]
                meta["title"] = first_q or "新对话"
                meta["msg_count"] = len(cm.recent_history)
                # preview = 最新一轮的 user query 截前 30 字
                last_q = cm.recent_history[-1][0].strip()[:30]
                meta["preview"] = last_q
            user_store.update_meta(user_id, session_id, meta)
            user_store.touch(user_id, session_id)
            return True
        except Exception:
            return False

    # ============================================================
    # 顶层流式入口：answer_stream（文字 / 图片统一，多 session）
    # ============================================================
    def answer_stream(
        self, message, history: list = None,
        session_id: str = "", request: gr.Request = None, progress=None,
    ):
        """
        流式对话入口。每次 yield 三元组 (chatbot_messages, docs_md, msg_box)：
        1) 首次 yield：立刻回显用户消息 + "正在思考"占位（消除干等）
        2) 之后：LLM 逐 token 更新助手气泡
        3) 末次：完整答案 + 相关文档
        """
        cleared_box = gr.MultimodalTextbox(value=None, interactive=True)
        keep_box = gr.update()  # 流式后续帧不再碰输入框，避免反复重绘丢焦点

        # ---- 解析输入（MultimodalTextbox: {"text":..., "files":[...]}）----
        if isinstance(message, dict):
            text = str(message.get("text", "") or "").strip()
            files = message.get("files") or []
        else:
            text = str(message or "").strip()
            files = []

        image_path = None
        if files:
            first = files[0]
            image_path = first.get("path") if isinstance(first, dict) else str(first)

        user_id = user_store.resolve_user_id(request=request)
        session_id = self._ensure_session(user_id, session_id)
        cm = self._get_or_create_chat_manager(user_id, session_id)

        # 历史以前端传入的 messages 为准（dict 列表）
        base = []
        if isinstance(history, list):
            base = [m for m in history if isinstance(m, dict) and m.get("role") in ("user", "assistant")]

        # 空输入：不处理
        if not text and not image_path:
            yield base, "_⚠️ 请输入您的问题_", cleared_box
            return

        # ==================== 图片分支 ====================
        if image_path and os.path.exists(image_path):
            user_content = ([{"path": image_path}, text] if text else {"path": image_path})
            msgs = base + [{"role": "user", "content": user_content}]
            # 立即回显，避免 YOLO/OCR 识别期间白屏
            yield msgs + [{"role": "assistant", "content": "🔍 正在看这张图，请稍等…"}], "_🔍 正在识别图片…_", cleared_box

            try:
                info = look_at_image(image_path, progress=progress)
                enhanced_question, display_question = build_context_for_chat(info, text)
                annotated = bool(info.get("annotated_path") and info.get("objects"))

                streamed = False
                final_sources, intent = [], ""
                for partial, sources, intent in cm.chat_stream(enhanced_question):
                    streamed = True
                    shown = [{"path": info["annotated_path"]}, partial] if annotated else partial
                    docs_md = self._format_docs_md(sources, intent) if sources else "_✍️ 正在组织回答…_"
                    yield msgs + [{"role": "assistant", "content": shown}], docs_md, keep_box
                    if sources:
                        final_sources = sources
                if not streamed:
                    yield msgs + [{"role": "assistant", "content": "（我没太看懂这张图，可以再拍清楚一点或补一句问题试试）"}], "_（未识别到内容）_", keep_box

                # 内部历史存的是"增强问题"，又长又技术；替换成学生能看懂的一句话
                try:
                    with cm._lock:
                        if cm.recent_history:
                            cm.recent_history[-1] = (display_question, cm.recent_history[-1][1])
                except Exception:
                    pass

                with self._cache_lock:
                    self._flush_user_state_locked(user_id, session_id)

            except Exception as e:
                err_type = type(e).__name__
                err_msg = "❌ 这张图我没看明白，换张清楚点的图，或者直接把想问的字打给我试试。"
                print(f"❌[image {err_type}] {e!r}", flush=True)
                yield msgs + [{"role": "assistant", "content": err_msg}], f"_{err_msg}_", keep_box
            return

        # ==================== 文字分支 ====================
        msgs = base + [{"role": "user", "content": text}]
        # 立即回显 + 占位（检索/重排阶段无法流式，先给反馈）
        yield msgs + [{"role": "assistant", "content": "🤔 正在思考…"}], "_🤔 正在查找资料…_", cleared_box

        try:
            streamed = False
            final_sources, intent = [], ""
            for partial, sources, intent in cm.chat_stream(text):
                streamed = True
                docs_md = self._format_docs_md(sources, intent) if sources else "_✍️ 正在生成回答…_"
                yield msgs + [{"role": "assistant", "content": partial or "…"}], docs_md, keep_box
                if sources:
                    final_sources = sources

            with self._cache_lock:
                self._flush_user_state_locked(user_id, session_id)

            if not streamed:
                yield msgs + [{"role": "assistant", "content": "（未生成答案）"}], "_（未生成答案）_", keep_box

        except Exception as e:
            err_type = type(e).__name__
            err_msg = f"❌ 抱歉，处理出错了（{err_type}）。请刷新页面或换个问题再试。"
            print(f"❌[{err_type}] {e!r}", flush=True)
            yield msgs + [{"role": "assistant", "content": err_msg}], f"_{err_msg}_", keep_box

    def _format_docs_md(self, sources: list, intent: str = "") -> str:
        """把检索到的来源整理成"相关文档"区域的 Markdown。
        公众号文章会展示标题、类别、关键信息和图片。"""
        if not (isinstance(sources, list) and sources):
            if intent == "chat":
                return "_（闲聊 / 系统提示，不需要引用库内文档）_"
            return "_（本次回答未引用库内文档）_"

        lines = []
        seen_titles = set()

        for src in sources:
            if not isinstance(src, dict):
                continue

            # 公众号文章：展示标题+类别+关键信息+图片
            if src.get("type") == "wx_article":
                title = src.get("file_name") or src.get("source", "")
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                cat = src.get("category", "")
                imp = src.get("importance", "中")
                key_info = src.get("key_info", "")
                images = src.get("images", [])

                lines.append(f"**📰 {title}**\n")
                lines.append(f"> 类别：{cat} · 重要性：{imp}\n\n")
                if key_info:
                    lines.append(f"关键信息：{key_info}\n\n")

                # 展示图片（最多 3 张）
                if images:
                    for img in images[:3]:
                        if os.path.exists(img):
                            # Gradio 用 /file= 代理本地文件
                            lines.append(f"![图片](/file={img})\n\n")
                continue

            # 普通文档
            source = str(src.get("source", "")).strip()
            if source:
                file_path = os.path.join(self.docs_dir, source)
                if os.path.exists(file_path):
                    lines.append(f"📄 [{source}]({file_path})\n\n")
                else:
                    lines.append(f"📄 {source}\n\n")

        return "".join(lines) if lines else "_（本次回答未引用库内文档）_"

    def clear_history(self, session_id: str = "", request: gr.Request = None) -> list:
        user_id = user_store.resolve_user_id(request=request)
        session_id = self._ensure_session(user_id, session_id)
        with self._cache_lock:
            cm = self._user_cache.get(user_id, {}).get(session_id)
            if cm is not None:
                cm.clear_history()
            # 同时清磁盘 + 更新 meta
            user_store.save_state(user_id, session_id, cm.to_state_dict() if cm else {"recent_history": [], "old_summary": "", "schema_version": 1})
            user_store.update_meta(user_id, session_id, {"title": "新对话", "preview": "", "msg_count": 0})
        return []

    # ============================================================
    # Session 操作（供 UI 调用）
    # ============================================================
    def list_sessions(self, request: gr.Request = None) -> list:
        """返回 [(label, sid), ...] 用于 gr.Radio choices"""
        user_id = user_store.resolve_user_id(request=request)
        items = user_store.list_sessions(user_id)
        # label = 可读时间 + title 截短
        import time as _t
        def _fmt(sid, meta):
            ts = meta.get("last_active_at", 0)
            t_str = _t.strftime("%m-%d %H:%M", _t.localtime(ts)) if ts else "—"
            title = (meta.get("title") or "新对话")[:24]
            preview = (meta.get("preview") or "")[:18]
            return f"[{t_str}] {title}  ·  {preview}"
        return [(_fmt(sid, meta), sid) for sid, meta in items]

    def create_session(self, request: gr.Request = None) -> str:
        user_id = user_store.resolve_user_id(request=request)
        sid = user_store.new_session(user_id, title="新对话")
        return sid or ""

    def remove_session(self, session_id: str, request: gr.Request = None) -> str:
        """删除某 session；如果删的是当前，自动切到下一条或创建新 session。"""
        if not session_id:
            return ""
        user_id = user_store.resolve_user_id(request=request)
        with self._cache_lock:
            user_cache = self._user_cache.get(user_id, {})
            user_cache.pop(session_id, None)
        user_store.delete_session(user_id, session_id)
        # 自动切：用户可能还在前端打这个 sid，要替换
        sessions = user_store.list_sessions(user_id)
        if not sessions:
            return self.create_session(request=request)
        return sessions[0][0]

    def initialize(self):
        print("正在初始化...")
        manager = VectorStoreManager(persist_directory="data/faiss_db_bge")
        try:
            self.vectorstore = manager.load_vectorstore()
            print("向量库加载成功")
        except FileNotFoundError:
            print("向量库不存在，请先运行 build_vectorstore.py 构建向量库")
            return False

        # 2026-09-18 修复：retriever.hyde_llm 依赖 qa_system.llm，但 qa_system 依赖 retriever，
        # 形成循环依赖。先创建 retriever（hyde_llm=None），再创建 qa_system，最后注入 hyde_llm。
        self.retriever = create_retriever(
            self.vectorstore,
            documents=manager._documents,
            top_k=4,
            score_threshold=0.0,
            use_bm25=True,
            use_reranker=True,
            reranker_model='BAAI/bge-reranker-v2-m3',  # 2026-09-20 启用（首次启动会下载 ~2.27 GB）
            vector_top_k=8,  # 2026-09-20 提速：候选 10→8（实测 5 时 pass_rate 掉到 73%，8 折中）
            bm25_top_k=8,    # 两路各召回 8，RRF 融合后 reranker 精排 8 → top4
            use_hyde=False,  # 2026-09-18：先关闭，等 qa_system 建好再开启
            hyde_llm=None,
        )
        print("混合检索器创建成功（BM25 + 向量 + RRF + BGE-reranker）")

        try:
            self.qa_system = create_qa_system(self.retriever)
            print("问答系统创建成功")
        except ValueError as e:
            print(f"LLM 初始化失败: {e}")
            return False

        # 现在 qa_system 已建好，可以注入 hyde_llm
        # 2026-09-17：HyDE（Hypothetical Document Embedding）— LLM 先写"假设答案"再去搜，recall@4 +15%
        self.retriever.use_hyde = True
        self.retriever.hyde_llm = self.qa_system.llm

        print(f"多用户多 session cache 已就绪（per-user max={self._user_cache_max_per_user}）")
        return True


# ============================================================
# Gradio UI（小U 风格：紫粉主题 + 首页式布局）
# ============================================================
_INJECT_JS = """
<script>
(function() {
  function genUid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }
  let uid = localStorage.getItem('wxu_uid');
  if (!uid) {
    uid = genUid();
    localStorage.setItem('wxu_uid', uid);
  }
  document.cookie = `wxu_uid=${uid}; path=/; max-age=31536000; SameSite=Lax`;
  window.WXU_UID = uid;
})();
</script>
"""

# ============================================================
# 聊天记录格式适配（Gradio 6.x 只支持 messages 格式）
# 内部存储 / 磁盘持久化仍用 [[q, a], ...] 二元组，仅在 UI 边界转换
# ============================================================
def _tuples_to_messages(history):
    """[[q, a], ...] -> [{"role":"user","content":q},{"role":"assistant","content":a}, ...]
    已是 messages 格式则原样返回。"""
    if not history:
        return []
    msgs = []
    for item in history:
        if isinstance(item, dict):
            msgs.append(item)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            q, a = item
            msgs.append({"role": "user", "content": str(q)})
            msgs.append({"role": "assistant", "content": str(a)})
    return msgs


# ============================================================
# 静态数据（阶段 1；后续接 RAG / JSON 配置）
# ============================================================
SCENARIOS = [
    ("📅 我的课表", "今天上啥?下周怎么安排?"),
    ("🏆 奖学金", "奖学金怎么申请?校一等奖学金条件是啥?"),
    ("📝 考试安排", "这学期期末考啥时候?校历给我看看"),
    ("🍜 校园生活", "学校食堂哪个最好吃?周边有啥好玩的?"),
    ("🔔 教务通知", "最近有啥重要通知?奖学金截止时间"),
]
# 2026-09-21 移除：学分进度 / 实习招聘 / 学科答疑（按用户要求删掉）


# ============================================================
# 我的课表 处理器（2026-09-21）
# ============================================================
def _cv_parse_curriculum_file(file_path, request: gr.Request):
    """解析上传的 PDF/截图，填充编辑器 + 预览 + OCR 原文"""
    if not file_path:
        return cv_ui.render_empty_view(), "⚠️ 请先选择文件", gr.update(value=[])
    try:
        result = cv_parser.parse_uploaded_file(file_path)
        rows = cv_ui.courses_to_editor_rows(result["parsed"])
        preview_html = cv_ui.render_parsed_preview(result["parsed"], result["unparsed_lines"])
        stats = result.get("stats", {})
        status_md = (
            f"✅ 已解析 **{stats.get('parsed_count', 0)}** 条 / "
            f"未识别 **{stats.get('unparsed_count', 0)}** 条 / "
            f"原文 **{stats.get('raw_text_lines', 0)}** 行（解析率 {int(stats.get('parse_rate', 0) * 100)}%）"
        )
        if result["file_type"] == "image":
            # 截图模式：附 OCR 原文供用户参考
            raw_html = cv_ui.render_raw_text_preview(result["raw_text"], result["source"])
            return preview_html, raw_html, gr.update(value=rows), status_md
        # PDF 模式：原文太长不展示
        return preview_html, "", gr.update(value=rows), status_md
    except Exception as e:
        return (
            cv_ui.render_empty_view(),
            "",
            gr.update(value=[]),
            f"❌ 解析失败: {type(e).__name__}: {e}",
        )


def _cv_save_curriculum(rows, request: gr.Request):
    """保存编辑后的课表到 SQLite"""
    user_id = request.cookies.get("wxu_uid", "anonymous") if request else "anonymous"
    if not rows:
        return "⚠️ 表格为空", cv_ui.render_empty_view()
    courses = cv_ui.editor_rows_to_courses(rows)
    if not courses:
        return "⚠️ 表格中没有有效数据（请至少填写课程名）", cv_ui.render_empty_view()
    result = cv_db.save_curriculum(user_id, courses, source="用户编辑")
    saved = cv_db.load_curriculum(user_id)
    week_view = cv_ui.render_week_view(saved, current_week=1)
    msg = (
        f"✅ 已保存 **{result['saved']}** 条"
        f"（跳过 {result['skipped']} 条无效"
        + (f" / {len(result['errors'])} 条错误" if result['errors'] else "")
        + "）"
    )
    return msg, week_view


def _cv_load_curriculum(request: gr.Request):
    """从 SQLite 加载当前用户的课表 + 周视图"""
    user_id = request.cookies.get("wxu_uid", "anonymous") if request else "anonymous"
    courses = cv_db.load_curriculum(user_id)
    rows = cv_ui.courses_to_editor_rows(courses)
    week_view = cv_ui.render_week_view(courses, current_week=1) if courses else cv_ui.render_empty_view()
    return gr.update(value=rows), week_view


def _cv_clear_curriculum(request: gr.Request):
    """清空当前用户的课表"""
    user_id = request.cookies.get("wxu_uid", "anonymous") if request else "anonymous"
    deleted = cv_db.clear_curriculum(user_id)
    return gr.update(value=[]), cv_ui.render_empty_view(), f"🗑️ 已清空 {deleted} 条课表"


def _cv_download_html(request: gr.Request):
    """导出当前用户的课表为独立 HTML 文件（双击打开 / 分享）"""
    user_id = request.cookies.get("wxu_uid", "anonymous") if request else "anonymous"
    courses = cv_db.load_curriculum(user_id)
    if not courses:
        return gr.update(value=None, visible=False)
    import tempfile, os
    from datetime import datetime
    html_str = cv_ui.render_standalone_html(courses, current_week=1, title="我的课表")
    # 写到临时目录（Gradio 缓存友好路径）
    tmp_dir = tempfile.gettempdir()
    fname = f"my_schedule_{user_id[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    fpath = os.path.join(tmp_dir, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html_str)
    return gr.update(value=fpath, visible=True)

CLUBS_HTML = """
<div class="wxu-club-grid">
    <div class="wxu-club-card">
        <div class="wxu-club-banner b1">🤖</div>
        <div class="wxu-club-body">
            <div class="wxu-club-name">机器人协会 <span class="wxu-club-badge hot">🔥 超热门</span></div>
            <div class="wxu-club-tags-line"><span>电子设计</span><span>嵌入式</span><span>比赛</span></div>
            <div class="wxu-club-desc">做四足机器人 / 智能车 / 无人机 / 参加 RoboMaster、互联网+ 等比赛。实验室常驻,有学长带。</div>
            <div class="wxu-club-stats"><span>👥 287 人 · 每周三活动</span><span class="wxu-club-recruit">招新中</span></div>
        </div>
    </div>
    <div class="wxu-club-card">
        <div class="wxu-club-banner b2">🎤</div>
        <div class="wxu-club-body">
            <div class="wxu-club-name">主持人社 <span class="wxu-club-badge">校园名嘴</span></div>
            <div class="wxu-club-tags-line"><span>演讲</span><span>辩论</span><span>主持</span></div>
            <div class="wxu-club-desc">校园晚会主持、辩论赛、配音秀。每周有"吐槽大会"内训,练气场。</div>
            <div class="wxu-club-stats"><span>👥 156 人 · 周二晚</span><span class="wxu-club-recruit">招新中</span></div>
        </div>
    </div>
    <div class="wxu-club-card">
        <div class="wxu-club-banner b3">📸</div>
        <div class="wxu-club-body">
            <div class="wxu-club-name">摄影协会 <span class="wxu-club-badge">视觉创作者</span></div>
            <div class="wxu-club-tags-line"><span>风光</span><span>人像</span><span>后期</span></div>
            <div class="wxu-club-desc">周末外拍太湖 / 校园主题创作 / Photoshop & Lightroom 内训,作品有机会上校园公众号。</div>
            <div class="wxu-club-stats"><span>👥 198 人 · 周日外拍</span><span class="wxu-club-recruit">招新中</span></div>
        </div>
    </div>
    <div class="wxu-club-card">
        <div class="wxu-club-banner b4">🎸</div>
        <div class="wxu-club-body">
            <div class="wxu-club-name">街舞社 <span class="wxu-club-badge hot">🔥 燃</span></div>
            <div class="wxu-club-tags-line"><span>Hip-hop</span><span>Breaking</span><span>公演</span></div>
            <div class="wxu-club-desc">零基础也能跳!每周训练 + 月底公演,校园十佳歌手 / 迎新晚会的常客。</div>
            <div class="wxu-club-stats"><span>👥 312 人 · 周四周六</span><span class="wxu-club-recruit">招新中</span></div>
        </div>
    </div>
    <div class="wxu-club-card">
        <div class="wxu-club-banner b5">💻</div>
        <div class="wxu-club-body">
            <div class="wxu-club-name">ACM 算法社 <span class="wxu-club-badge">硬核</span></div>
            <div class="wxu-club-tags-line"><span>算法</span><span>编程竞赛</span><span>刷题</span></div>
            <div class="wxu-club-desc">打 Codeforces / LeetCode 周赛 / 蓝桥杯 / CCPC。氛围硬核,大神带小白,食堂刷题常驻。</div>
            <div class="wxu-club-stats"><span>👥 142 人 · 周五晚</span><span class="wxu-club-recruit full">已满</span></div>
        </div>
    </div>
    <div class="wxu-club-card">
        <div class="wxu-club-banner b6">🏃</div>
        <div class="wxu-club-body">
            <div class="wxu-club-name">晨跑团 <span class="wxu-club-badge">健康活力</span></div>
            <div class="wxu-club-tags-line"><span>晨练</span><span>马拉松</span><span>太湖跑</span></div>
            <div class="wxu-club-desc">早上 6:30 操场集合,绕校园 5 圈 ≈ 3 公里。每月一次太湖长跑,氛围轻松无压力。</div>
            <div class="wxu-club-stats"><span>👥 89 人 · 每天 6:30</span><span class="wxu-club-recruit">随时加入</span></div>
        </div>
    </div>
</div>
"""

CAMPUS_INFO_HTML = """
<div class="wxu-camp-grid">
    <div class="wxu-camp-card">
        <span class="wxu-camp-emoji">🍜</span>
        <div class="wxu-camp-title">食堂红黑榜</div>
        <div class="wxu-camp-desc">问小U:哪个食堂最好吃 / 哪个窗口排队最少 / 哪家最实惠</div>
        <div class="wxu-camp-tags">
            <span class="wxu-camp-tag">学长推荐</span>
            <span class="wxu-camp-tag pink">学生专属</span>
        </div>
        <div class="wxu-ai-insight">💬 在下方对话区提问</div>
    </div>
    <div class="wxu-camp-card">
        <span class="wxu-camp-emoji">🏃</span>
        <div class="wxu-camp-title">校园实用信息</div>
        <div class="wxu-camp-desc">问小U:图书馆开馆时间 / 校园 wifi / 教学楼门禁 / 校车时刻</div>
        <div class="wxu-camp-tags">
            <span class="wxu-camp-tag">实用</span>
            <span class="wxu-camp-tag warm">新生必看</span>
        </div>
        <div class="wxu-ai-insight">💬 在下方对话区提问</div>
    </div>
    <div class="wxu-camp-card">
        <span class="wxu-camp-emoji">🎁</span>
        <div class="wxu-camp-title">隐藏福利</div>
        <div class="wxu-camp-desc">问小U:校医院免费项目 / 学生证折扣 / 体育场地优惠 / 奖学金种类</div>
        <div class="wxu-camp-tags">
            <span class="wxu-camp-tag green">免费</span>
            <span class="wxu-camp-tag">学生专属</span>
        </div>
        <div class="wxu-ai-insight">💬 在下方对话区提问</div>
    </div>
    <div class="wxu-camp-card">
        <span class="wxu-camp-emoji">📍</span>
        <div class="wxu-camp-title">周边攻略</div>
        <div class="wxu-camp-desc">问小U:周末去哪玩 / 太湖骑行路线 / 地铁公交 / 无锡景点</div>
        <div class="wxu-camp-tags">
            <span class="wxu-camp-tag warm">周末</span>
            <span class="wxu-camp-tag pink">出游</span>
        </div>
        <div class="wxu-ai-insight">💬 在下方对话区提问</div>
    </div>
</div>
"""

# 期末复习资料卡片（合规修订 2026-09-21）
# 合规要求：依据《无锡学院关于试卷印制、保管和保存的实施办法（试行）》，
# 试卷（含空白卷、参考答案）属于教学档案，保密管理，不得向学生披露。
# 因此 UI 不展示"真题"、"参考答案"、"往年考卷"、"题库 + 解析"等敏感字眼。
EXAM_RESOURCES = [
    ("📐", "高等数学(下)· 复习提纲", "7 章节重点 · 配套讲义索引", "hot"),
    ("🔌", "电路分析 · 复习要点速查", "覆盖课程大纲 · 同步课堂讲义", "hot"),
    ("💻", "C 语言程序设计 · 复习提纲", "覆盖 2018-2025 教学大纲 · 配套课后习题", "new"),
    ("📡", "物联网通信技术 · 思维导图 + 速记卡", "22 张思维导图 · 156 张速记卡", "new"),
    ("📖", "马原 · 复习笔记(2026 修订版)", "48 页 · 思政教研室审核 · 89 同学在看", ""),
]

def _build_exam_html():
    items = []
    for emoji, name, meta, tag_type in EXAM_RESOURCES:
        tag_html = ""
        if tag_type == "hot":
            tag_html = '<span class="wxu-exam-tag-hot">🔥 热门</span>'
        elif tag_type == "new":
            tag_html = '<span class="wxu-exam-tag-new">NEW</span>'
        items.append(f"""
        <div class="wxu-exam-item">
            <div class="wxu-exam-emoji">{emoji}</div>
            <div class="wxu-exam-info">
                <div class="wxu-exam-name">{name}</div>
                <div class="wxu-exam-meta">{meta}</div>
            </div>
            {tag_html}
        </div>
        """)
    return f"""
    <div class="wxu-exam-card">
        <div class="wxu-exam-list">
            {''.join(items)}
            <div class="wxu-exam-item" style="justify-content:center; background:linear-gradient(135deg,#f3edff,#fdf2f8);">
                <div style="display:flex; align-items:center; gap:8px; color:#8b5cf6; font-size:13px; font-weight:500;">
                    <span>📂</span> 还有 23 份资料,点击展开全部
                </div>
            </div>
        </div>
    </div>
    """


# ============================================================
# 自定义 CSS(紫粉主题)
# ============================================================
_CUSTOM_CSS = """
.gradio-container {
    background: linear-gradient(135deg, #faf7ff 0%, #fdf2f8 50%, #fef3ff 100%) !important;
    font-family: 'Outfit', -apple-system, "PingFang SC", sans-serif !important;
    color: #1f1d2c !important;
    max-width: 1200px !important;
}
.wxu-brand {
    max-width: 1080px;
    margin: 0 auto;
    padding: 24px 24px 8px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.wxu-logo { display: flex; align-items: center; gap: 12px; }
.wxu-logo-mark {
    width: 44px; height: 44px;
    background: linear-gradient(135deg, #8b5cf6, #ec4899);
    border-radius: 14px;
    display: flex; align-items: center; justify-content: center;
    color: white; font-weight: 800; font-size: 19px;
    box-shadow: 0 4px 14px rgba(139,92,246,0.32);
}
.wxu-logo-text h1 {
    font-size: 19px; font-weight: 700; margin: 0;
    background: linear-gradient(135deg, #8b5cf6, #ec4899);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.wxu-logo-text p { font-size: 12px; color: #6b6580; margin: 2px 0 0; }
.wxu-user {
    padding: 6px 14px 6px 6px;
    background: white;
    border: 1px solid #efe9f5;
    border-radius: 999px;
    display: flex; align-items: center; gap: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    font-size: 13px; color: #1f1d2c;
}
.wxu-user-avatar {
    width: 30px; height: 30px; border-radius: 50%;
    background: linear-gradient(135deg, #c4b5fd, #f9a8d4);
    display: flex; align-items: center; justify-content: center;
    color: white; font-size: 13px; font-weight: 600;
}
.wxu-main { max-width: 1080px; margin: 0 auto; padding: 12px 24px 48px; }
.wxu-greeting { text-align: center; padding: 20px 0 28px; }
.wxu-greeting-title {
    font-size: 30px; font-weight: 700; color: #1f1d2c;
    letter-spacing: -0.02em; margin-bottom: 8px;
}
.wxu-greeting-sub { font-size: 15px; color: #6b6580; }
.wxu-search {
    max-width: 600px;
    margin: 24px auto 0;
    padding: 14px 18px;
    background: white;
    border: 1px solid #efe9f5;
    border-radius: 18px;
    box-shadow: 0 4px 20px rgba(139,92,246,0.08);
    display: flex; align-items: center; gap: 12px;
    font-size: 15px; color: #9d96b0;
}
.wxu-section-title {
    font-size: 13px; font-weight: 600; color: #6b6580;
    text-transform: uppercase; letter-spacing: 0.08em;
    margin: 32px 0 14px;
}
.wxu-camp-grid {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
}
.wxu-camp-card {
    background: white; border: 1px solid #efe9f5;
    border-radius: 16px; padding: 18px 16px;
    position: relative; overflow: hidden;
    transition: all 0.2s;
}
.wxu-camp-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.06);
    border-color: #c4b5fd;
}
.wxu-camp-emoji { font-size: 30px; display: block; margin-bottom: 8px; }
.wxu-camp-title { font-size: 14px; font-weight: 600; color: #1f1d2c; margin-bottom: 4px; }
.wxu-camp-desc { font-size: 11.5px; color: #6b6580; line-height: 1.45; margin-bottom: 10px; }
.wxu-camp-tags { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 8px; }
.wxu-camp-tag {
    padding: 2px 8px; border-radius: 999px;
    font-size: 10.5px; font-weight: 500;
    background: #f3edff; color: #8b5cf6;
}
.wxu-camp-tag.pink { background: #fdf2f8; color: #ec4899; }
.wxu-camp-tag.warm { background: #fff7ed; color: #f59e0b; }
.wxu-camp-tag.green { background: #ecfdf5; color: #10b981; }
.wxu-ai-insight {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 9px;
    background: linear-gradient(135deg, #f3edff, #fdf2f8);
    border: 1px solid #c4b5fd;
    border-radius: 999px;
    font-size: 10.5px; color: #8b5cf6; font-weight: 500;
}
.wxu-exam-card {
    background: white; border: 1px solid #efe9f5;
    border-radius: 20px; padding: 22px 24px;
    margin-top: 8px;
}
.wxu-exam-list { display: flex; flex-direction: column; gap: 8px; }
.wxu-exam-item {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 14px; background: #faf7ff;
    border-radius: 12px; transition: all 0.15s;
}
.wxu-exam-item:hover { background: #f3edff; }
.wxu-exam-emoji {
    width: 38px; height: 38px; border-radius: 10px;
    background: white;
    display: flex; align-items: center; justify-content: center;
    font-size: 19px; flex-shrink: 0;
}
.wxu-exam-info { flex: 1; min-width: 0; }
.wxu-exam-name { font-size: 13.5px; font-weight: 600; color: #1f1d2c; }
.wxu-exam-meta { font-size: 11.5px; color: #6b6580; margin-top: 2px; }
.wxu-exam-tag-hot {
    background: linear-gradient(135deg, #f43f5e, #f97316);
    color: white; font-size: 10px; padding: 2px 8px;
    border-radius: 4px; font-weight: 600;
}
.wxu-exam-tag-new {
    background: #ecfdf5; color: #047857;
    font-size: 10px; padding: 2px 8px; border-radius: 4px;
    font-weight: 600;
}
.wxu-club-grid {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px;
}
.wxu-club-card {
    background: white; border: 1px solid #efe9f5;
    border-radius: 18px; overflow: hidden;
    transition: all 0.2s;
}
.wxu-club-card:hover {
    transform: translateY(-3px);
    box-shadow: 0 12px 32px rgba(0,0,0,0.08);
}
.wxu-club-banner {
    height: 78px;
    display: flex; align-items: center; justify-content: center;
    font-size: 38px;
}
.wxu-club-banner.b1 { background: linear-gradient(135deg, #dbeafe, #bfdbfe); }
.wxu-club-banner.b2 { background: linear-gradient(135deg, #fce7f3, #fbcfe8); }
.wxu-club-banner.b3 { background: linear-gradient(135deg, #d1fae5, #a7f3d0); }
.wxu-club-banner.b4 { background: linear-gradient(135deg, #fef3c7, #fde68a); }
.wxu-club-banner.b5 { background: linear-gradient(135deg, #ede9fe, #ddd6fe); }
.wxu-club-banner.b6 { background: linear-gradient(135deg, #fee2e2, #fecaca); }
.wxu-club-body { padding: 14px 16px; }
.wxu-club-name {
    font-size: 15px; font-weight: 700; color: #1f1d2c;
    margin-bottom: 4px;
}
.wxu-club-badge {
    font-size: 10px; padding: 1px 6px;
    background: #f3edff; color: #8b5cf6;
    border-radius: 4px; font-weight: 500;
}
.wxu-club-badge.hot { background: linear-gradient(135deg, #fef3c7, #fde68a); color: #b45309; }
.wxu-club-tags-line {
    font-size: 11px; color: #6b6580;
    margin-bottom: 8px; display: flex; gap: 6px; flex-wrap: wrap;
}
.wxu-club-tags-line span {
    padding: 1px 6px; background: #f6f1fb; border-radius: 4px;
}
.wxu-club-desc {
    font-size: 12.5px; color: #1f1d2c;
    line-height: 1.5; margin-bottom: 10px;
}
.wxu-club-stats {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 11.5px; color: #6b6580;
    padding-top: 8px;
    border-top: 1px dashed #efe9f5;
}
.wxu-club-recruit {
    padding: 3px 10px;
    background: linear-gradient(135deg, #10b981, #059669);
    color: white; border-radius: 999px;
    font-size: 10.5px; font-weight: 600;
}
.wxu-club-recruit.full { background: #f6f1fb; color: #6b6580; }
.wxu-personal-card {
    margin-top: 24px;
    background: linear-gradient(135deg, #f3edff 0%, #fdf2f8 100%);
    border: 1px solid #efe9f5;
    border-radius: 20px;
    padding: 22px 24px;
}
.wxu-personal-title {
    font-size: 16px; font-weight: 700; color: #1f1d2c;
    margin-bottom: 16px;
}
.wxu-scenario-btn button {
    background: white !important;
    border: 1px solid #efe9f5 !important;
    border-radius: 14px !important;
    height: auto !important;
    min-height: 76px !important;
    text-align: left !important;
    padding: 14px 16px !important;
    color: #1f1d2c !important;
    font-weight: 500 !important;
    transition: all 0.2s !important;
    box-shadow: none !important;
}
.wxu-scenario-btn button:hover {
    border-color: #c4b5fd !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 24px rgba(0,0,0,0.06) !important;
}
.wxu-footer {
    text-align: center;
    margin-top: 32px; padding: 20px;
    font-size: 12px; color: #9d96b0;
}
.wxu-filter-row { margin-bottom: 8px; }
.wxu-filter-row .wrap { gap: 8px !important; }
.wxu-filter-row .gr-box { background: white !important; border-color: #efe9f5 !important; border-radius: 999px !important; }

.wxu-section-title-row {
    display: flex; align-items: center; justify-content: space-between;
    margin: 32px 0 14px;
}
.wxu-live-dot {
    display: inline-block;
    width: 8px; height: 8px; border-radius: 50%;
    background: #10b981;
    animation: wxu-pulse 2s ease-in-out infinite;
    margin-right: 6px;
}
@keyframes wxu-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}
@media (max-width: 768px) {
    .wxu-camp-grid { grid-template-columns: repeat(2, 1fr); }
    .wxu-club-grid { grid-template-columns: 1fr; }
}
"""


def build_gradio_interface(app: RAGApp) -> gr.Blocks:
    """小U 风格:首页 + 对话 + 上传(2026-09-18 重构)"""
    with gr.Blocks(
        title="小U · 无锡学院智能助手",
        theme=gr.themes.Soft(),
        css=_CUSTOM_CSS,
    ) as demo:
        gr.HTML(value=_INJECT_JS, visible=False)

        # ============ 顶部品牌区 ============
        gr.HTML("""
        <div class="wxu-brand">
            <div class="wxu-logo">
                <div class="wxu-logo-mark">U</div>
                <div class="wxu-logo-text">
                    <h1>小 U</h1>
                    <p>无锡学院智能助手</p>
                </div>
            </div>
            <div class="wxu-user">
                <div class="wxu-user-avatar">刘</div>
                <span class="wxu-user-name">24305218 · 刘同学</span>
            </div>
        </div>
        <div class="wxu-main">
        """)

        # ============ Tabs ============
        with gr.Tabs():
            # ----- Tab 1: 首页(问候 + 学校特色 + 期末复习 + 社团 + 学期卡 + 对话)-----
            with gr.Tab(label="🏠 首页"):
                # 问候区
                gr.HTML("""
                <div class="wxu-greeting">
                    <div class="wxu-greeting-title">下午好,👋 刘同学</div>
                    <div class="wxu-greeting-sub">今天 ☀️ 25°C 微风不燥,适合出门~ 有什么我能帮你的吗?</div>
                </div>
                """)

                # ===== 对话区(2026-09-20 从底部上移到欢迎区正下方,首屏可直接打字)=====
                # 对话区
                gr.HTML('<div class="wxu-section-title"><span class="wxu-live-dot"></span>💬 和小U聊聊</div>')
                with gr.Row():
                    with gr.Column(scale=1, min_width=240):
                        new_session_btn = gr.Button("➕ 新对话", variant="primary", size="sm")
                        session_list = gr.Radio(
                            label="历史对话",
                            choices=[],
                            value=None,
                            interactive=True,
                            show_label=False,
                        )
                        with gr.Row():
                            delete_session_btn = gr.Button("🗑 删除", size="sm")
                            refresh_session_btn = gr.Button("🔄 刷新", size="sm")
                        session_info = gr.Markdown(
                            value="_选择一个对话,或点 ➕ 新对话_",
                            show_label=False,
                        )

                    with gr.Column(scale=4):
                        current_sid = gr.State("")
                        chatbot = gr.Chatbot(
                            label="对话",
                            height=460,
                            value=[{"role": "assistant", "content": "👋 我是小U。有问题直接问，也可以点下面的 📎 发张图片——通知截图、作业题、实验器材，我都能边看边答。"}],
                        )
                        msg_box = gr.MultimodalTextbox(
                            placeholder="想问什么直接打字；点左侧 📎 还能发图片（通知、作业题、不认识的东西都行），按回车发送",
                            file_types=["image"],
                            file_count="single",
                            show_label=False,
                            scale=7,
                        )
                        with gr.Row():
                            clear_btn = gr.Button("🧹 清空对话", size="sm")
                        gr.Markdown("### 📚 相关文档")
                        docs_output = gr.Markdown(value="_📂 提问后,引用到的资料会显示在这里_")

                # 8 个快捷场景(2 行 × 4 列)
                gr.HTML('<div class="wxu-section-title">✨ 你可能想了解</div>')
                scenario_buttons = []
                with gr.Row():
                    for label, query in SCENARIOS[:4]:
                        btn = gr.Button(label, elem_classes="wxu-scenario-btn", size="sm")
                        # 2026-09-21 特殊标记 "我的课表"（触发专属面板）
                        if "我的课表" in label:
                            scenario_buttons.append((btn, "__CURRICULUM__"))
                        else:
                            scenario_buttons.append((btn, query))
                with gr.Row():
                    for label, query in SCENARIOS[4:]:
                        btn = gr.Button(label, elem_classes="wxu-scenario-btn", size="sm")
                        if "我的课表" in label:
                            scenario_buttons.append((btn, "__CURRICULUM__"))
                        else:
                            scenario_buttons.append((btn, query))

                # 我的课表面板（点击"我的课表"按钮时显示，默认隐藏）
                with gr.Column(visible=False) as curriculum_panel:
                    gr.HTML('<div class="wxu-section-title">📅 我的课表 · 上传 / 编辑 / 查看</div>')
                    gr.HTML(
                        '<div style="font-size:12px;color:#6b6580;margin-bottom:10px;line-height:1.6;">'
                        '📌 上传教务系统导出的 <b>PDF</b>（最准） 或 <b>课表截图</b>（截图 OCR 准确率有限，建议手动修正）'
                        '<br>📌 也可以直接在下方表格里手动添加/编辑课程（推荐）'
                        '</div>'
                    )
                    with gr.Row():
                        cv_file = gr.File(label="选择 PDF / 图片", file_count="single", type="filepath",
                                          file_types=[".pdf", ".png", ".jpg", ".jpeg"])
                        cv_parse_btn = gr.Button("🔍 解析", variant="primary", size="sm")
                        cv_refresh_btn = gr.Button("🔄 重新加载", size="sm")
                        cv_clear_btn = gr.Button("🗑️ 清空", variant="stop", size="sm")
                    cv_status = gr.Markdown()
                    cv_preview = gr.HTML()
                    cv_raw_text = gr.HTML()
                    gr.HTML('<div style="margin:14px 0 6px 0;color:#6b6580;font-size:12px;font-weight:600;">📝 课程列表（手动编辑 → 保存）</div>')
                    cv_editor = gr.Dataframe(
                        headers=["课程名", "教师", "地点", "星期", "开始节", "结束节", "开始周", "结束周", "周类型"],
                        datatype=["str", "str", "str", "number", "number", "number", "number", "number", "str"],
                        row_count=(0, "dynamic"),
                        interactive=True,
                        show_label=False,
                    )
                    with gr.Row():
                        cv_save_btn = gr.Button("💾 保存到我的课表", variant="primary")
                    gr.HTML('<div style="margin:18px 0 8px 0;color:#6b6580;font-size:12px;font-weight:600;">👀 周视图预览</div>')
                    cv_week_view = gr.HTML()
                    # 2026-09-22 下载独立 HTML（用户能双击打开 / 分享）
                    gr.HTML('<div style="margin:14px 0 6px 0;color:#6b6580;font-size:12px;font-weight:600;">📥 下载独立 HTML 网页（双击就能在浏览器打开）</div>')
                    with gr.Row():
                        cv_download_btn = gr.Button("⬇️ 下载周视图 HTML", variant="secondary", size="sm")
                        cv_download_file = gr.File(label="下载文件", interactive=False, visible=False)

                    # 2026-09-21 我的课表按钮事件绑定
                    cv_parse_btn.click(
                        fn=_cv_parse_curriculum_file,
                        inputs=[cv_file],
                        outputs=[cv_preview, cv_raw_text, cv_editor, cv_status],
                    )
                    cv_save_btn.click(
                        fn=_cv_save_curriculum,
                        inputs=[cv_editor],
                        outputs=[cv_status, cv_week_view],
                    )
                    cv_refresh_btn.click(
                        fn=_cv_load_curriculum,
                        inputs=[],
                        outputs=[cv_editor, cv_week_view],
                    )
                    cv_clear_btn.click(
                        fn=_cv_clear_curriculum,
                        inputs=[],
                        outputs=[cv_editor, cv_week_view, cv_status],
                    )
                    # 2026-09-22 下载 HTML 按钮（导出独立网页）
                    cv_download_btn.click(
                        fn=_cv_download_html,
                        inputs=[],
                        outputs=[cv_download_file],
                    )

                # 学校特色
                gr.HTML('<div class="wxu-section-title">🎯 校园生活情报</div>')
                gr.HTML(CAMPUS_INFO_HTML)

                # 期末复习(筛选 + 资料)
                gr.HTML('<div class="wxu-section-title">📚 期末复习 · 智能推荐</div>')
                with gr.Row(elem_classes="wxu-filter-row"):
                    major_dd = gr.Dropdown(
                        choices=["物联网工程", "电子信息", "机械工程", "软件工程", "土木工程"],
                        value="物联网工程",
                        label="专业",
                        show_label=False,
                        scale=1,
                    )
                    grade_dd = gr.Dropdown(
                        choices=["大一", "大二", "大三", "大四"],
                        value="大二",
                        label="年级",
                        show_label=False,
                        scale=1,
                    )
                gr.HTML(_build_exam_html())

                # 社团天地
                gr.HTML('<div class="wxu-section-title">🎭 社团天地 · 找你的组织</div>')
                gr.HTML(CLUBS_HTML)

                # 对话区(2026-09-20 已上移到欢迎区正下方,此处不再重复)

                # ===== 事件绑定 =====
                def _load_session_then_refresh(req: gr.Request, current_value):
                    user_id = user_store.resolve_user_id(request=req)
                    sid = current_value or ""
                    sid = app._ensure_session(user_id, sid)
                    cm = app._get_or_create_chat_manager(user_id, sid)
                    new_history = _tuples_to_messages(cm.recent_history)
                    session_choices = app.list_sessions(request=req)
                    return (
                        new_history, sid,
                        gr.Radio(choices=session_choices, value=sid),
                        f"**当前 sid**:{sid}",
                    )

                def _new_session_action(req: gr.Request):
                    user_id = user_store.resolve_user_id(request=req)
                    sid = app.create_session(request=req)
                    sid = app._ensure_session(user_id, sid)
                    cm = app._get_or_create_chat_manager(user_id, sid)
                    return (
                        [], sid,
                        gr.Radio(choices=app.list_sessions(request=req), value=sid),
                        f"**当前 sid**:{sid}(新对话)",
                    )

                def _delete_session_action(session_id, req: gr.Request):
                    user_id = user_store.resolve_user_id(request=req)
                    if not session_id:
                        new_sid = app.create_session(request=req)
                    else:
                        new_sid = app.remove_session(session_id, request=req)
                    new_sid = app._ensure_session(user_id, new_sid)
                    cm = app._get_or_create_chat_manager(user_id, new_sid)
                    new_history = _tuples_to_messages(cm.recent_history)
                    return (
                        new_history, new_sid,
                        gr.Radio(choices=app.list_sessions(request=req), value=new_sid),
                    )

                def _refresh_session_list(req: gr.Request, current_sid_):
                    sid = current_sid_ or ""
                    sid = app._ensure_session(user_store.resolve_user_id(request=req), sid)
                    return gr.Radio(choices=app.list_sessions(request=req), value=sid)

                # 场景卡 → 填入输入框（MultimodalTextbox 的值是 {"text","files"}）
                # 2026-09-21 特殊："我的课表"按钮改为显示 curriculum_panel（不填 msg_box）
                for btn, query in scenario_buttons:
                    if query == "__CURRICULUM__":
                        btn.click(
                            fn=lambda: gr.update(visible=True),
                            inputs=[],
                            outputs=[curriculum_panel],
                        )
                    else:
                        btn.click(
                            fn=lambda q=query: {"text": q, "files": []},
                            inputs=[],
                            outputs=[msg_box],
                        )

                # session 操作
                session_list.change(
                    fn=_load_session_then_refresh,
                    inputs=[session_list],
                    outputs=[chatbot, current_sid, session_list, session_info],
                )
                new_session_btn.click(
                    fn=_new_session_action,
                    inputs=[],
                    outputs=[chatbot, current_sid, session_list, session_info],
                )
                delete_session_btn.click(
                    fn=_delete_session_action,
                    inputs=[session_list],
                    outputs=[chatbot, current_sid, session_list],
                )
                refresh_session_btn.click(
                    fn=_refresh_session_list,
                    inputs=[current_sid],
                    outputs=[session_list],
                )

                # 发送（文字 / 图片都走这里；回车或点输入框自带的发送按钮）
                # 2026-09-20：改为流式 generator，LLM 逐 token 回显，需要 queue 支持
                def _on_submit(message, history, sid, request: gr.Request, progress=gr.Progress()):
                    yield from app.answer_stream(
                        message, history, session_id=sid, request=request, progress=progress,
                    )

                msg_box.submit(
                    fn=_on_submit,
                    inputs=[msg_box, chatbot, current_sid],
                    outputs=[chatbot, docs_output, msg_box],
                )

                def _clear_with_sid(sid, request: gr.Request):
                    app.clear_history(session_id=sid, request=request)
                    return []
                clear_btn.click(fn=_clear_with_sid, inputs=[current_sid], outputs=[chatbot])

                # on_load
                demo.load(
                    fn=lambda request: (
                        gr.Radio(choices=app.list_sessions(request=request), value=None),
                        app._ensure_session(user_store.resolve_user_id(request=request), ""),
                    ),
                    inputs=[],
                    outputs=[session_list, current_sid],
                )

            # ----- Tab: 上传文档(保持原代码)-----
            with gr.Tab(label="📤 上传文档"):
                with gr.Row():
                    with gr.Column(scale=3):
                        file_input = gr.File(
                            label="选择文档(可多选)",
                            file_count="multiple",
                            file_types=[".txt", ".md", ".pdf", ".docx", ".doc"],
                        )
                        category_input = gr.Textbox(
                            label="文档类别",
                            value="个人资料",
                            placeholder="如:课程笔记 / 学习资料 / 个人收集",
                        )
                        upload_btn = gr.Button("🚀 上传并入库", variant="primary")

                    with gr.Column(scale=7):
                        upload_status = gr.Markdown(value="_等待上传_")

                def _handle_upload(file_paths, category):
                    result = process_uploads(
                        file_paths if isinstance(file_paths, list) else ([file_paths] if file_paths else []),
                        category=category or "默认",
                    )
                    return result["message_md"]

                upload_btn.click(
                    fn=_handle_upload,
                    inputs=[file_input, category_input],
                    outputs=upload_status,
                )

        gr.HTML('</div>')  # close .wxu-main
        gr.HTML("""
        <div class="wxu-footer">
            由 <span style="color:#8b5cf6; font-weight:500;">小U</span> 团队用心打造 · 你的校园生活小帮手 💜
        </div>
        """)
        gr.Markdown(
            "---\n"
            "🔧 **底库**:bge-m3 1024-dim · 29165 chunks · 构建完成于 2026-09-16"
        )

    return demo


def main():
    print("=" * 50)
    print("无锡学院智能助手 - RAG 问答系统")
    print("=" * 50)

    app = RAGApp()
    if not app.initialize():
        print("\n初始化失败，请检查配置后重试")
        return

    print("\n正在启动 Gradio 界面...")
    demo = build_gradio_interface(app)

    # 流式输出（generator 逐 token 回显）依赖 queue
    demo.queue(default_concurrency_limit=4)
    demo.launch(
        server_name="0.0.0.0",
        server_port=7861,
        share=False,
        inbrowser=True,
        # 允许访问公众号文章图片目录，供 /file= 代理显示
        allowed_paths=[
            os.path.join(PROJECT_ROOT, "data", "wx_articles"),
            os.path.join(PROJECT_ROOT, "data", "docs"),
        ],
    )


if __name__ == "__main__":
    main()
