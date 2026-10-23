"""
FastAPI 多页 Web 应用（2026-09-22 改造版）
- 5 个独立页面：首页 / 课表 / 奖学金 / 校园生活 / 上传中心
- AI Agent：意图识别 → dispatcher 路由 → 各模块处理
- 用户记忆：SQLite WAL（agent/memory.py），per-user cookie
- 日志：logs/app.log，按天切割
- PDF 上传：FastAPI BackgroundTasks 异步解析，不阻塞接口

启动：python src/web_app/fastapi_app.py
端口：7862，绑定 0.0.0.0
"""

import os
import sys
import uuid
import json
import logging
import tempfile
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.concurrency import run_in_threadpool  # 2026-09-22 新增：把同步阻塞调用丢线程池

# 2026-09-24 ⑥:API 限流(防滥用 / 防 LLM 配额被打爆)
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# 2026-10-12 加:Prometheus 客户端(无 src 依赖,放最前)
from prometheus_client import CONTENT_TYPE_LATEST

# 让 src 包可被 import
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 加载 .env（LLM_API_KEY 等）
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from src.web_app.agent import memory
from src.web_app.agent import dispatcher
from src.curriculum import db as cv_db
from src.curriculum import ui as cv_ui
from src.curriculum.parser import parse_uploaded_file

# 2026-10-12 加:工程化 —— 缓存 + 监控(在 sys.path 加入后 import)
from src.web_app.answer_cache import answer_cache  # Redis + fakeredis 兜底
from src.web_app.monitoring import (
    render_metrics, observe_cache, observe_request, observe_error,
    CACHE_SKIPPED, track_in_flight,
)  # Prometheus 指标


# ============================================================
# 日志配置：logs/app.log，按天切割，保留 14 天
# ============================================================
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def _setup_logging() -> logging.Logger:
    """统一日志配置：控制台 + 文件（按天切割）"""
    log = logging.getLogger()
    log.setLevel(logging.INFO)
    # 防止重复添加 handler（uvicorn reload 场景）
    if log.handlers:
        return log
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 控制台（2026-09-22 修复：Windows 默认 GBK 会乱码，显式 utf-8）
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    try:
        # Python 3.9+ 支持 stream.reconfigure
        import sys as _sys
        if hasattr(_sys.stdout, "reconfigure"):
            _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    log.addHandler(sh)
    # 文件（按天切割，保留 14 天）
    from logging.handlers import TimedRotatingFileHandler
    fh = TimedRotatingFileHandler(
        str(LOG_DIR / "app.log"),
        when="midnight",        # 每天 0 点切割
        backupCount=14,         # 保留 14 天
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    log.addHandler(fh)
    return log

log = _setup_logging()


TEMPLATES_DIR = Path(__file__).resolve().parent / "pages"
STATIC_DIR = PROJECT_ROOT / "static"

# 后台任务状态：task_id → 结果（线程安全）
# 2026-10-09 修复：原 `_task_results` 全局 dict 永不清理，长时间运行会内存泄漏。
# 改加 TTL（1 小时）+ 条目上限（1000），溢出时清最老的。
_task_results: Dict[str, Dict] = {}
_task_lock = threading.Lock()
_TASK_TTL_SECONDS = 3600  # 1 小时
_TASK_MAX_ENTRIES = 1000


def _task_set(task_id: str, payload: Dict) -> None:
    """记录后台任务结果，自动 TTL + 容量上限"""
    import time as _t
    now = _t.time()
    with _task_lock:
        # 清理过期
        if len(_task_results) > _TASK_MAX_ENTRIES // 2:
            for tid in list(_task_results.keys()):
                if now - _task_results[tid].get("_ts", 0) > _TASK_TTL_SECONDS:
                    _task_results.pop(tid, None)
        _task_results[task_id] = {**payload, "_ts": now}


def _task_get(task_id: str) -> Optional[Dict]:
    """读取后台任务结果，过期返回 None"""
    import time as _t
    with _task_lock:
        v = _task_results.get(task_id)
        if v is None:
            return None
        if _t.time() - v.get("_ts", 0) > _TASK_TTL_SECONDS:
            _task_results.pop(task_id, None)
            return None
        return {k: val for k, val in v.items() if k != "_ts"}


# ============================================================
# 应用初始化
# ============================================================
app = FastAPI(title="无锡学院智能助手 · 多页版", version="1.0")
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# 2026-09-24 ⑥:API 限流器(全局注册,默认按 remote_address 限流)
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

log.info("FastAPI 应用初始化完成")


def _ensure_uid(request: Request) -> tuple[str, bool]:
    """
    从 cookie 拿 uid，没有或不合法则现场生成匿名 UUID。

    2026-10-09 修复：
      1. 原 `request.cookies.get("wxu_uid", "")` 拿到值不校验，攻击者可伪造
         `wxu_uid=../../etc/passwd` 做路径穿越。现在强制过 `is_valid_user_id` 白名单。
      2. 原匿名 fallback `str(uuid.uuid4())` 改用 `user_store.new_anonymous_uid()`，
         生成 `wxu_anon_<24hex>` 形式，便于区分匿名用户（仍满足白名单）。
      3. 原 fallback 走 user_store.resolve_user_id 的 IP+UA hash 共享匿名账户
         （A 用户对话历史进 B 用户 prompt 的隐私 bug），改为每用户独立 UUID。

    返回 (uid, is_new)：is_new=True 表示新生成的匿名 uid，需要 Set-Cookie。
    """
    from src.web_app import user_store
    uid = (request.cookies.get("wxu_uid") or "").strip()
    if uid and user_store.is_valid_user_id(uid):
        return uid, False
    # cookie 不存在或不合法 → 现场生成独立匿名 UUID
    uid = user_store.new_anonymous_uid()
    return uid, True


def _attach_uid_cookie(response, uid: str) -> None:
    """给已构造的 response 写 uid cookie（仅首次访问需要）"""
    response.set_cookie(
        key="wxu_uid", value=uid,
        max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax",
    )


# ============================================================
# Session（对话会话）管理 - 2026-09-24 新增
# ============================================================
# 设计：每个用户可有多个 session（对话会话），消息按 session 隔离
# 存储：data/user_histories/<uid>/index.json（session 元数据）+ chat_<sid>.pkl（消息列表）
# cookie：wxu_sid 记录当前会话,前后端共享
# ============================================================
def _ensure_sid(request: Request, uid: str) -> str:
    """从 cookie 拿 sid,无效则新建一个并设 cookie

    Returns:
        str: 当前 session id(已确保合法存在)
    """
    from src.web_app import user_store
    sid = request.cookies.get("wxu_sid", "").strip()
    if sid and user_store.is_valid_session_id(sid):
        # 验证该 sid 真的属于这个 uid(防止拿到别人的 sid)
        sessions = user_store.list_sessions(uid)
        if any(s == sid for s, _ in sessions):
            return sid
    # 没 cookie 或 sid 不属于该 uid,新建一个
    new_sid = user_store.new_session(uid, title="新对话")
    return new_sid or str(uuid.uuid4()).replace("-", "")[:16]


def _attach_sid_cookie(response, sid: str) -> None:
    """给 response 写 wxu_sid cookie(可选 - 365 天)"""
    response.set_cookie(
        key="wxu_sid", value=sid,
        max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax", path="/",
    )


def _append_session_message(uid: str, sid: str, role: str, content: str):
    """往某 session 的 recent_history 原子追加一条消息 + 同步更新 index.json 元数据

    消息格式:[{"role":"user/assistant","content":"..."}, ...]

    2026-10-09 修复：原实现是 `load_state → 修改 list → save_state` 三步，
    每步各自持 user_store._LOCK 但中间 gap 不持锁——同用户并发两条消息会丢一条
    （A load → B load → A save → B save 覆盖 A）。
    改用 user_store.append_message_atomic：把三步合并到一把 _LOCK 内，保证原子性。
    """
    from src.web_app import user_store
    # 原子追加（load→改→save 一把锁内完成）
    user_store.append_message_atomic(uid, sid, {"role": role, "content": content}, max_messages=200)

    # 同步更新 index.json:msg_count + last_active_at + preview + 自动标题(首条 user)
    state = user_store.load_state(uid, sid) or {}
    history = state.get("recent_history", []) or []
    msg_count = len([h for h in history if isinstance(h, dict) and h.get("role") == "user"])
    preview = (str(content)[:30] + "…") if len(str(content)) > 30 else str(content)
    user_store.touch(uid, sid)
    user_store.update_meta(uid, sid, {"msg_count": msg_count, "preview": preview})

    # 首条 user 消息时,自动用前 16 字做标题(用户可手动改)
    meta_sessions = user_store.list_sessions(uid)
    for s, meta in meta_sessions:
        if s == sid and (not meta.get("title") or meta.get("title") == "新对话"):
            auto_title = (str(content)[:16] + "…") if len(str(content)) > 16 else str(content)
            user_store.update_meta(uid, sid, {"title": auto_title})
            break


def _get_recommend(uid: str, top: int = 4) -> List[str]:
    """基于对话历史给出推荐入口(改用 memory 模块)"""
    recent = memory.load_recent(uid, limit=20)
    if not recent:
        return ["schedule", "campus", "upload"]
    # 统计 user 消息的意图频次
    intent_count: Dict[str, int] = {}
    for h in recent:
        if h["role"] == "user" and h.get("intent"):
            intent_count[h["intent"]] = intent_count.get(h["intent"], 0) + 1
    if not intent_count:
        return ["schedule", "campus", "upload"]
    sorted_intents = sorted(intent_count.items(), key=lambda x: -x[1])[:top]
    # 意图 → 页面
    intent_to_page = {
        "schedule": "schedule",
        "kb": "campus", "news": "campus", "schedule_upload": "upload",
        "image_ocr": "upload", "general": "schedule",
    }
    pages = []
    for intent, _ in sorted_intents:
        p = intent_to_page.get(intent, "schedule")
        if p not in pages:
            pages.append(p)
    # 补齐到 3 个
    for p in ["schedule", "campus", "upload"]:
        if p not in pages and len(pages) < top:
            pages.append(p)
    return pages[:top]


# ============================================================
# 中间件：每次请求记日志
# ============================================================
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = datetime.now()
    response = await call_next(request)
    cost = (datetime.now() - start).total_seconds() * 1000
    log.info(
        "%s %s -> %d (%.0fms) uid=%s",
        request.method, request.url.path,
        response.status_code, cost,
        request.cookies.get("wxu_uid", "-")[:8],
    )
    return response


# ============================================================
# 页面路由（5 个）
# ============================================================
# 2026-09-22 修复：新版 starlette (0.40+) 的 TemplateResponse 签名变了
# 旧：TemplateResponse("name.html", {"request": request, ...})
# 新：TemplateResponse(request, "name.html", {"k": v, ...})
# 旧写法会导致 jinja 把 dict 当 cache key 报错 `unhashable type: 'dict'`

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页：个性化推荐 + AI 对话入口"""
    uid, is_new = _ensure_uid(request)
    response = templates.TemplateResponse(request, "index.html", {
        "user_id": uid,
        "recommend": _get_recommend(uid),
    })
    if is_new:
        _attach_uid_cookie(response, uid)
    log.info("首页访问 uid=%s recommend=%s", uid[:8], response.context.get("recommend"))
    return response


@app.get("/schedule", response_class=HTMLResponse)
async def schedule(request: Request, week: int = 1):
    """课表页（周视图渲染）

    2026-10-09 改造:
        - 不传 week 参数时,根据用户开学日自动算 current_week
        - 传了 week 参数(用户点 W1-W20)就用用户选的
        - 把 settings 也传给模板,前端可以显示 + 修改
    """
    from src.curriculum.settings import get_settings
    from src.curriculum.week import compute_current_week

    uid, is_new = _ensure_uid(request)
    courses = cv_db.load_curriculum(uid)
    settings = get_settings(uid)
    # 自动算当前周(只有用户没显式传 ?week=N 时才用)
    auto_week = compute_current_week(settings["semester_start_date"])
    # 关键判断:用户主动选了周次就尊重用户选择
    user_picked = "week" in str(request.url)
    effective_week = week if user_picked else auto_week
    week_view_html = cv_ui.render_week_view(courses, current_week=effective_week) if courses else cv_ui.render_empty_view()
    response = templates.TemplateResponse(request, "schedule.html", {
        "week_view": week_view_html,
        "course_count": len(courses),
        "current_week": effective_week,
        "auto_week": auto_week,           # 给前端高亮"当前周"用
        "settings": settings,              # 开学日期 + 班级
    })
    if is_new:
        _attach_uid_cookie(response, uid)
    return response


# === 用户设置 API(2026-10-09 新增) ===
@app.get("/api/settings")
async def api_get_settings(request: Request):
    """读当前用户设置(开学日期 + 班级)"""
    from src.curriculum.settings import get_settings
    uid, _ = _ensure_uid(request)
    return get_settings(uid)


@app.post("/api/settings")
async def api_save_settings(request: Request):
    """保存用户设置(开学日期 + 班级)

    body: {"semester_start_date": "2026-09-01", "class_id": 2, "class_name": "电信2201"}
    """
    from src.curriculum.settings import save_settings
    uid, _ = _ensure_uid(request)
    body = await request.json()
    start_date = (body.get("semester_start_date") or "").strip()
    class_id = body.get("class_id")
    class_name = (body.get("class_name") or "").strip() or None

    if not start_date:
        raise HTTPException(status_code=400, detail="semester_start_date 不能为空")

    ok = save_settings(
        uid,
        semester_start_date=start_date,
        class_id=int(class_id) if class_id else None,
        class_name=class_name,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="日期格式错误,应该是 YYYY-MM-DD")

    log.info("用户设置已保存 uid=%s start=%s class=%s", uid[:8], start_date, class_name)
    return {"ok": True}


@app.get("/scholarship")
async def scholarship():
    """奖学金页已合并到首页对话,直接重定向到首页"""
    return RedirectResponse(url="/", status_code=302)


# ============================================================
# 登录 / 退出（2026-09-24 新增）
# ============================================================
# 设计：
#   - GET  /login  → 渲染昵称表单
#   - POST /login  → 校验昵称合法性 → 设 wxu_uid(后端用,UUID,httponly) + wxu_nick(前端显示,URL 编码) → 跳 /
#   - GET  /logout → 清两个 cookie → 跳 /login
#
# cookie 双轨：
#   - wxu_uid : UUID,httponly=True,samesite=lax,365 天   ← 后端数据隔离用
#   - wxu_nick: 昵称(URL 编码后),httponly=False,samesite=lax,30 天 ← 前端 JS decodeURIComponent 显示
#
# 昵称合法字符集：[A-Za-z0-9_\-\u4e00-\u9fff]（支持中文）,长度 1-32
# ============================================================
import re
import urllib.parse
_NICK_PATTERN = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{1,32}$")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """登录页:起个昵称（不填昵称直接访问主页也能用,走 anon fallback）"""
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/login")
async def login_submit(request: Request, nickname: str = Form(...)):
    """提交昵称:校验合法性 → 写 cookie → 跳首页"""
    nick = (nickname or "").strip()
    if not _NICK_PATTERN.match(nick):
        # 重新渲染登录页,带错误提示
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "昵称只能包含中英文/数字/下划线/短横线,长度 1-32 字符"},
            status_code=400,
        )

    # 保留或生成 UUID uid（后端数据隔离用）
    existing_uid = request.cookies.get("wxu_uid", "").strip()
    if not existing_uid or len(existing_uid) > 128:
        existing_uid = str(uuid.uuid4())

    # 中文昵称需要 URL encode 后再放进 Set-Cookie（cookie 标准只支持 latin-1）
    nick_encoded = urllib.parse.quote(nick, safe="")

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key="wxu_uid", value=existing_uid,
        max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax", path="/",
    )
    response.set_cookie(
        key="wxu_nick", value=nick_encoded,
        max_age=60 * 60 * 24 * 30, httponly=False, samesite="lax", path="/",
    )
    log.info("用户登录 nick=%s uid=%s", nick[:8], existing_uid[:8])
    return response


@app.get("/logout")
async def logout():
    """退出登录:清两个 cookie,跳登录页"""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("wxu_uid", path="/")
    response.delete_cookie("wxu_nick", path="/")
    return response


@app.get("/campus", response_class=HTMLResponse)
async def campus(request: Request):
    """校园生活页"""
    uid, is_new = _ensure_uid(request)
    response = templates.TemplateResponse(request, "campus.html", {})
    if is_new:
        _attach_uid_cookie(response, uid)
    return response


@app.get("/upload", response_class=HTMLResponse)
async def upload(request: Request):
    """上传中心页"""
    uid, is_new = _ensure_uid(request)
    response = templates.TemplateResponse(request, "upload.html", {})
    if is_new:
        _attach_uid_cookie(response, uid)
    return response


# ============================================================
# JSON API
# ============================================================
@app.get("/health")
async def health():
    """健康检查（验收项1：返回 {"ok": true} + 缓存状态）"""
    rag = dispatcher._get_rag()
    rag_ready = rag is not None and rag.qa_system is not None
    return {
        "ok": True,
        "ts": datetime.now().isoformat(),
        "rag_ready": rag_ready,
        "use_multihop": bool(getattr(rag, "multi_hop", None)) if rag else False,
        "cache_mode": answer_cache.mode,          # 2026-10-12 加:redis / fakeredis / memory
        "cache_stats": answer_cache.stats(),      # 命中率统计
    }


# 2026-10-12 加:Prometheus 抓取端点
# 用法:Prometheus 配置 scrape_configs 抓 http://<host>:7862/metrics
# 或浏览器直接打开看原始指标
@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus 指标端点(给 Grafana / Alertmanager 抓)"""
    return Response(content=render_metrics(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/schedule")
async def api_schedule(request: Request, week: int = 1):
    """获取当前用户课表 JSON

    会从 user_settings 读取用户的 class_id,用 cv_db.load_curriculum(class_id)
    过滤(公共课(class_id IS NULL) + 我的班级两桶合并返回)。
    若用户未设置 class_id,返回该用户全部(向后兼容)。
    """
    from src.curriculum.settings import get_settings  # 局部导入避免循环

    uid = request.cookies.get("wxu_uid", "anonymous")
    settings = get_settings(uid)
    user_class_id = settings.get("class_id")
    # load_curriculum(class_id=X) -> X 的课 + 公共课(NULL) 都返回
    courses = cv_db.load_curriculum(uid, class_id=user_class_id)
    return {
        "courses": courses,
        "count": len(courses),
        "current_week": week,
        "user_class_id": user_class_id,
        "user_class_name": settings.get("class_name"),
    }


# ---- 后台任务处理函数 ----
def _parse_pdf_task(
    task_id: str,
    uid: str,
    tmp_path: str,
    filename: str,
    kind: str,
    class_id: Optional[int] = None,
):
    """后台解析 PDF（课表 or 通用文档），结果写 _task_results

    Args:
        class_id: 仅 kind="schedule" 生效。把 PDF 课表灌到对应班级桶。
                  None=不按班级分的公共课桶(此时 clear_curriculum 也只清 NULL)
    """
    try:
        log.info("后台任务开始 task=%s kind=%s class_id=%s file=%s",
                 task_id[:8], kind, class_id, filename)
        if kind == "schedule":
            result = parse_uploaded_file(tmp_path)
            courses = result["parsed"]
            if courses:
                # 同班级覆盖更新(只删 class_id 一致的旧数据,公共课不会被误删)
                cv_db.clear_curriculum(uid, class_id=class_id)
                cv_db.save_curriculum(uid, courses, class_id=class_id, source=filename)
            _task_set(task_id, {
                "status": "done",
                "kind": "schedule",
                "parsed": len(courses),
                "parse_rate": result["stats"]["parse_rate"],
                "parse_method": result["parse_method"],
                "finished_at": datetime.now().isoformat(),
            })
            log.info("课表解析完成 task=%s parsed=%d", task_id[:8], len(courses))
        else:
            # 通用文档上传 → 入向量库
            from src.web_app.upload_handler import process_uploads
            res = process_uploads([tmp_path], category="用户上传")
            _task_set(task_id, {
                "status": "done",
                "kind": "document",
                "uploaded": res["uploaded"],
                "chunks_added": res["chunks_added"],
                "status_code": res["status"],
                "message": res["message_md"][:500],
                "finished_at": datetime.now().isoformat(),
            })
            log.info("文档入库完成 task=%s chunks=%d", task_id[:8], res["chunks_added"])
    except Exception as e:
        log.exception("后台任务失败 task=%s", task_id[:8])
        _task_set(task_id, {
            "status": "error",
            "error": str(e),
            "finished_at": datetime.now().isoformat(),
        })
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ===== 实验课表上传接口(2026-10-09 新增) =====
@app.post("/api/upload/experiment_table")
async def api_upload_experiment_table(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """上传实验课表(Excel/CSV/截图),系统按用户班级自动过滤 + 增量入课表

    流程:
        1. 保存到临时文件
        2. 后台解析(Excel 直接读 / CSV 直接读 / 截图走 OCR)
        3. 按 user_settings.class_name 过滤"我的班级"行
        4. 调用 db.add_courses 增量入课表(不去重旧数据)
    """
    from src.curriculum.experiment_parser import parse_experiment_table
    from src.curriculum.settings import get_settings

    uid, _ = _ensure_uid(request)
    settings = get_settings(uid)
    user_class = settings.get("class_name") or ""

    if not file.filename:
        raise HTTPException(status_code=400, detail="未选择文件")

    # 保存到临时文件
    content = await file.read()
    suffix = Path(file.filename).suffix
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(content)
    tmp.close()

    task_id = str(uuid.uuid4())
    _task_set(task_id, {"status": "pending", "kind": "experiment", "filename": file.filename})

    def _run():
        try:
            log.info("实验课表解析开始 task=%s file=%s", task_id[:8], file.filename)
            courses = parse_experiment_table(tmp.name, user_class=user_class)
            result = cv_db.add_courses(uid, courses, class_id=settings.get("class_id"), source=f"实验课表:{file.filename}")
            _task_set(task_id, {
                "status": "done",
                "kind": "experiment",
                "filename": file.filename,
                "user_class": user_class,
                "saved": result["saved"],
                "duplicated": result["duplicated"],
                "skipped": result["skipped"],
                "finished_at": datetime.now().isoformat(),
            })
            log.info("实验课表入库完成 task=%s saved=%d dup=%d", task_id[:8], result["saved"], result["duplicated"])
        except Exception as e:
            log.exception("实验课表入库失败 task=%s", task_id[:8])
            _task_set(task_id, {
                "status": "error",
                "kind": "experiment",
                "error": str(e),
                "finished_at": datetime.now().isoformat(),
            })
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    background_tasks.add_task(_run)
    memory.save(uid, "user", f"上传实验课表:{file.filename}", intent="schedule_upload")
    return {"task_id": task_id, "filename": file.filename, "user_class": user_class}


@app.post("/api/schedule/upload")
async def api_schedule_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """上传 PDF 解析课表（BackgroundTasks 异步，1 秒内返回 task_id）

    class_id 注入逻辑：PDF 课表按用户当前 settings 的班级归类。
    若用户未设置 class_id(class_id=None),则归到"未分班"公共课桶,
    与实验课(add_courses)之间互不干扰。
    """
    from src.curriculum.settings import get_settings  # 局部导入避免循环

    uid = request.cookies.get("wxu_uid", "")
    if not uid:
        raise HTTPException(status_code=401, detail="No user cookie")

    # 拿 settings 里的 class_id,决定 PDF 课表归到哪个桶
    user_settings = get_settings(uid)
    class_id = user_settings.get("class_id")

    content = await file.read()
    tmp = tempfile.NamedTemporaryFile(
        suffix=Path(file.filename).suffix, delete=False
    )
    tmp.write(content)
    tmp.close()
    task_id = str(uuid.uuid4())
    _task_set(task_id, {"status": "pending", "kind": "schedule", "filename": file.filename})
    # 丢后台解析（立即返回）
    background_tasks.add_task(_parse_pdf_task, task_id, uid, tmp.name, file.filename, "schedule", class_id)
    memory.save(uid, "user", f"上传课表:{file.filename}", intent="schedule_upload")
    log.info("课表上传已入队 task=%s uid=%s class_id=%s file=%s size=%d",
             task_id[:8], uid[:8], class_id, file.filename, len(content))
    return {"task_id": task_id, "filename": file.filename, "size": len(content), "class_id": class_id}


@app.post("/api/upload/document")
async def api_upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """通用文档上传 → 后台入向量库（不阻塞，1 秒内返回 task_id）"""
    uid = request.cookies.get("wxu_uid", "")
    if not uid:
        raise HTTPException(status_code=401, detail="No user cookie")

    content = await file.read()
    tmp = tempfile.NamedTemporaryFile(
        suffix=Path(file.filename).suffix, delete=False
    )
    tmp.write(content)
    tmp.close()
    task_id = str(uuid.uuid4())
    _task_set(task_id, {"status": "pending", "kind": "document", "filename": file.filename})
    background_tasks.add_task(_parse_pdf_task, task_id, uid, tmp.name, file.filename, "document")
    memory.save(uid, "user", f"上传文档:{file.filename}", intent="general")
    log.info("文档上传已入队 task=%s uid=%s file=%s size=%d", task_id[:8], uid[:8], file.filename, len(content))
    return {"task_id": task_id, "filename": file.filename, "size": len(content)}


@app.get("/api/upload/status/{task_id}")
async def api_upload_status(task_id: str):
    """查询后台任务状态"""
    result = _task_get(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="task not found or expired")
    return result


# ============================================================
# Session API（2026-09-24 新增：多对话会话管理）
# ============================================================
@app.get("/api/session/list")
async def api_session_list(request: Request):
    """列出当前用户的所有 session"""
    from src.web_app import user_store
    uid = request.cookies.get("wxu_uid", "").strip()
    if not uid:
        return {"sessions": []}
    items = user_store.list_sessions(uid)
    sessions = []
    for sid, meta in items:
        sessions.append({
            "sid": sid,
            "title": meta.get("title", "新对话"),
            "last_active_at": meta.get("last_active_at", 0),
            "created_at": meta.get("created_at", 0),
            "msg_count": meta.get("msg_count", 0),
            "preview": meta.get("preview", ""),
        })
    return {"sessions": sessions}


@app.post("/api/session/new")
async def api_session_new(request: Request):
    """新建一个 session,设 wxu_sid cookie"""
    from src.web_app import user_store
    uid = request.cookies.get("wxu_uid", "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="No user cookie")
    sid = user_store.new_session(uid, title="新对话")
    if not sid:
        raise HTTPException(status_code=500, detail="failed to create session")
    response = JSONResponse({"sid": sid, "title": "新对话"})
    _attach_sid_cookie(response, sid)
    return response


@app.get("/api/session/messages")
async def api_session_messages(request: Request, sid: str = ""):
    """加载某 session 的历史消息"""
    from src.web_app import user_store
    uid = request.cookies.get("wxu_uid", "").strip()
    if not uid or not sid or not user_store.is_valid_session_id(sid):
        return {"messages": []}
    state = user_store.load_state(uid, sid) or {}
    history = state.get("recent_history", []) or []
    # 兼容 tuple 格式
    if history and isinstance(history[0], (list, tuple)):
        converted = []
        for pair in history:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                q, a = pair[0], pair[1]
                if q:
                    converted.append({"role": "user", "content": str(q)})
                if a:
                    converted.append({"role": "assistant", "content": str(a)})
        history = converted
    messages = [
        {"role": h.get("role"), "content": h.get("content")}
        for h in history if isinstance(h, dict) and h.get("role") in ("user", "assistant")
    ]
    return {"messages": messages}


@app.post("/api/session/rename")
async def api_session_rename(request: Request):
    """重命名 session(title),body: {sid, title}"""
    from src.web_app import user_store
    body = await request.json()
    uid = request.cookies.get("wxu_uid", "").strip()
    sid = (body.get("sid") or "").strip()
    title = (body.get("title") or "").strip()[:64]
    if not uid or not sid or not user_store.is_valid_session_id(sid):
        raise HTTPException(status_code=400, detail="invalid sid")
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    ok = user_store.update_meta(uid, sid, {"title": title})
    if not ok:
        raise HTTPException(status_code=500, detail="rename failed")
    return {"ok": True, "title": title}


@app.post("/api/session/switch")
async def api_session_switch(request: Request):
    """切换当前 session,设 wxu_sid cookie,body: {sid}"""
    from src.web_app import user_store
    body = await request.json()
    uid = request.cookies.get("wxu_uid", "").strip()
    sid = (body.get("sid") or "").strip()
    if not uid or not sid or not user_store.is_valid_session_id(sid):
        raise HTTPException(status_code=400, detail="invalid sid")
    # 验证 sid 真的属于该 uid
    if not any(s == sid for s, _ in user_store.list_sessions(uid)):
        raise HTTPException(status_code=404, detail="session not found")
    response = JSONResponse({"ok": True, "sid": sid})
    _attach_sid_cookie(response, sid)
    return response


@app.post("/api/chat")
@limiter.limit("30/minute")  # 2026-09-24 ⑥:每个 IP 每分钟最多 30 次 chat(防 LLM 配额被刷爆)
async def api_chat(request: Request):
    """
    AI Agent 对话入口(2026-09-23 改成真正的 SSE 流式,2026-09-24 加多 session + 多轮 history)

    之前的"流式"是假的:dispatcher.dispatch() 等 LLM 完整生成,然后切成 8 字小段 yield 出去。
    现在用 dispatcher._rag_answer_stream() 走 chain.stream(),LLM 边推理边吐 token,前端边收边渲染。

    sync generator 在 Starlette 里默认在线程池跑,不阻塞 event loop。

    多轮支持(2026-09-24):
      - 从 cookie wxu_sid 拿当前 session,没有则自动新建一个
      - 消息按 sid 隔离(per-session history),存到 user_store chat_<sid>.pkl
      - LLM prompt 注入该 session 的历史 messages,理解指代/追问
      - 第一条 user 消息自动做标题(用户可手动改)
    """
    body = await request.json()
    query = body.get("query", "").strip()
    # 2026-10-09 修复：原 `request.cookies.get("wxu_uid", "anonymous")` 让所有未登录用户
    # 共享同一个 anonymous 账户，A 用户对话历史会进 B 用户 prompt（隐私泄露）。
    # 改用 _ensure_uid：cookie 不存在或不合法时现场生成独立 UUID，
    # 由后续 _attach_uid_cookie + _attach_sid_cookie 写回浏览器。
    uid, is_new = _ensure_uid(request)
    if not query:
        raise HTTPException(status_code=400, detail="query required")

    # 多 session:确保有当前 sid(cookie 无效就自动新建)
    sid = _ensure_sid(request, uid)

    # user 消息双写:memory SQLite(全局 intent 统计)+ per-session pickle(多轮历史)
    memory.save(uid, "user", query, intent="chat")
    _append_session_message(uid, sid, "user", query)
    log.info("chat 收到 uid=%s sid=%s query=%s", uid[:8], sid[:8], query[:50])

    # 1. 同步 intent 检测(快,不动 LLM)
    intent, _ = dispatcher.detect_intent(query)
    dispatcher._extract_preferences(query, uid)

    # 2. sync generator 给 StreamingResponse(在线程池里跑,不阻塞 event loop)
    # 2026-10-12 加:Answer Cache 拦截 + Prometheus 指标收尾
    def event_stream():
        import time as _t
        t0 = _t.time()
        status_holder = {"status": "success"}  # 闭包,finally 里读
        final_text_holder = {"text": ""}        # 闭包,写缓存用

        try:
            with track_in_flight():
                # ===== 2026-10-12 加:Answer Cache 拦截 =====
                # 只对 ReAct 路径(走 LLM)生效,快捷路径(schedule 等)直接走原逻辑
                cacheable = intent not in ("schedule", "schedule_upload", "image_ocr")
                cache_key = answer_cache.make_key(query) if cacheable else None

                if cacheable:
                    if answer_cache.skip_for_personal(query):
                        # 个性化问题("今天/我的")→ 不走缓存
                        CACHE_SKIPPED.labels(reason="personal_query").inc()
                    else:
                        cached = answer_cache.get(cache_key)
                        if cached:
                            # ★ 缓存命中:直接 yield,不走 LLM,延迟 <50ms
                            observe_cache("answer", hit=True)
                            cached_answer = cached["answer"]
                            yield f"data: {json.dumps({'text': cached_answer}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'sources': cached.get('sources', []), 'intent': intent, 'done': True, 'sid': sid, 'cached': True}, ensure_ascii=False)}\n\n"
                            memory.save(uid, "assistant", cached_answer, intent=intent)
                            _append_session_message(uid, sid, "assistant", cached_answer)
                            final_text_holder["text"] = cached_answer
                            log.info("chat cache HIT uid=%s sid=%s intent=%s", uid[:8], sid[:8], intent)
                            return  # ★ 提前结束,跳过整个 dispatch
                        # 缓存未命中
                        observe_cache("answer", hit=False)

                # ===== 原有逻辑(走 LLM) =====
                accumulated = []
                if intent == "schedule":
                    result = dispatcher._format_schedule(uid, query)
                    answer = result["answer"]
                    yield f"data: {json.dumps({'text': answer}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'sources': [], 'intent': intent, 'done': True, 'sid': sid}, ensure_ascii=False)}\n\n"
                    memory.save(uid, "assistant", answer, intent=intent)
                    _append_session_message(uid, sid, "assistant", answer)
                    final_text_holder["text"] = answer
                elif intent == "schedule_upload":
                    answer = "📤 上传课表请去「上传中心」页面,选 PDF 文件即可,我会自动解析存进你的课表。"
                    yield f"data: {json.dumps({'text': answer}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'sources': [], 'intent': intent, 'done': True, 'sid': sid}, ensure_ascii=False)}\n\n"
                    memory.save(uid, "assistant", answer, intent=intent)
                    _append_session_message(uid, sid, "assistant", answer)
                    final_text_holder["text"] = answer
                elif intent == "image_ocr":
                    answer = "🖼️ 看图直接去「上传中心」把图片传上来就行,我来帮你看里面的内容。"
                    yield f"data: {json.dumps({'text': answer}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'sources': [], 'intent': intent, 'done': True, 'sid': sid}, ensure_ascii=False)}\n\n"
                    memory.save(uid, "assistant", answer, intent=intent)
                    _append_session_message(uid, sid, "assistant", answer)
                    final_text_holder["text"] = answer
                else:
                    # 2026-10-09 升级:走 ReAct Agent 路径(LLM 自主决定调哪个 tool)
                    # agent_stream 内部会流式 yield 文本 + "[🔧 正在调 XX]" 工具调用提示
                    for chunk in dispatcher.handle_agent_stream(query, uid, intent, sid=sid):
                        accumulated.append(chunk)
                        yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
                    assistant_text = "".join(accumulated)
                    memory.save(uid, "assistant", assistant_text, intent=intent)
                    _append_session_message(uid, sid, "assistant", assistant_text)
                    yield f"data: {json.dumps({'sources': [], 'intent': intent, 'done': True, 'sid': sid}, ensure_ascii=False)}\n\n"
                    log.info("chat ReAct 流式完成 uid=%s sid=%s intent=%s 答案长=%d", uid[:8], sid[:8], intent, len(assistant_text))
                    final_text_holder["text"] = assistant_text
                    # ★ 2026-10-12 写缓存(只对 ReAct 路径 + 非个性化 + 有内容)
                    if cacheable and not answer_cache.skip_for_personal(query) and assistant_text:
                        answer_cache.set(cache_key, {
                            "answer": assistant_text,
                            "sources": [],
                            "intent": intent,
                        })
                yield "data: [DONE]\n\n"
        except Exception as e:
            # 任何异常都吞掉并标记为 error,不暴露给用户堆栈
            status_holder["status"] = "error"
            observe_error("chat_stream_error")
            log.exception("chat event_stream 异常 uid=%s sid=%s", uid[:8], sid[:8])
            err_msg = "\n\n⚠️ 出错了: " + type(e).__name__
            yield "data: " + json.dumps({"text": err_msg}, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
        finally:
            # ★ 2026-10-12 收尾:Prometheus 记录本次请求(无论成功/失败/缓存命中)
            elapsed = _t.time() - t0
            try:
                observe_request(intent, status_holder["status"], elapsed)
            except Exception:
                pass  # 监控绝不能影响业务

    response = StreamingResponse(event_stream(), media_type="text/event-stream")
    # 把当前 sid 写回 cookie(首次会话/换 sid 时需要)
    # 2026-10-09 加：is_new=True 时也写 uid cookie（首次访问的匿名用户）
    if is_new:
        _attach_uid_cookie(response, uid)
    _attach_sid_cookie(response, sid)
    return response


@app.get("/api/recommend")
async def api_recommend(request: Request):
    """个性化推荐"""
    # 2026-10-09 修复：原 "anonymous" 让所有未登录用户共享同一份推荐
    uid, _ = _ensure_uid(request)
    return {"recommend": _get_recommend(uid)}


# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    # 2026-09-22 修复：Windows 默认 GBK，print emoji 会 UnicodeEncodeError
    # 解决：先 reconfigure stdout 到 utf-8，再 print
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import uvicorn
    print("=" * 60)
    print("🚀 启动 FastAPI 多页版 · 端口 7862")
    print("   首页:     http://localhost:7862/")
    print("   课表:     http://localhost:7862/schedule")
    print("   校园生活: http://localhost:7862/campus")
    print("   上传中心: http://localhost:7862/upload")
    print("   健康检查: http://localhost:7862/health")
    print("=" * 60)
    log.info("FastAPI 启动，端口 7862")

    # 后台预热 RAG 引擎(2026-09-23 加):首次对话不再等 30-60 秒冷启动
    # 触发 dispatcher 单例懒加载:faiss + jieba + BM25 索引 + BGE-reranker 加载 + Ollama LLM 准备
    def _warmup_rag_async():
        def _run():
            try:
                log.info("后台预热 RAG 引擎开始(避免首次对话冷启动)...")
                rag = dispatcher._get_rag()
                if rag is not None:
                    log.info("后台预热 RAG 引擎完成 ✓")
                else:
                    log.warning("后台预热 RAG 引擎返回 None")
            except Exception as e:
                log.warning(f"后台预热 RAG 异常(不影响服务): {e!r}")
        threading.Thread(target=_run, daemon=True, name="rag-warmup").start()
    _warmup_rag_async()

    uvicorn.run(app, host="0.0.0.0", port=7862, log_level="info")
