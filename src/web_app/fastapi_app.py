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
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.concurrency import run_in_threadpool  # 2026-09-22 新增：把同步阻塞调用丢线程池

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
_task_results: Dict[str, Dict] = {}
_task_lock = threading.Lock()


# ============================================================
# 应用初始化
# ============================================================
app = FastAPI(title="无锡学院智能助手 · 多页版", version="1.0")
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

log.info("FastAPI 应用初始化完成")


def _ensure_uid(request: Request) -> tuple[str, bool]:
    """
    从 cookie 拿 uid，没有则创建。返回 (uid, is_new)
    set_cookie 放到路由里，response 对象构造之后再写
    """
    uid = request.cookies.get("wxu_uid", "")
    if not uid:
        uid = str(uuid.uuid4())
        return uid, True
    return uid, False


def _attach_uid_cookie(response, uid: str) -> None:
    """给已构造的 response 写 uid cookie（仅首次访问需要）"""
    response.set_cookie(
        key="wxu_uid", value=uid,
        max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax",
    )


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
    """课表页（周视图渲染）"""
    uid, is_new = _ensure_uid(request)
    courses = cv_db.load_curriculum(uid)
    week_view_html = cv_ui.render_week_view(courses, current_week=week) if courses else cv_ui.render_empty_view()
    response = templates.TemplateResponse(request, "schedule.html", {
        "week_view": week_view_html,
        "course_count": len(courses),
        "current_week": week,
    })
    if is_new:
        _attach_uid_cookie(response, uid)
    return response


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
    """健康检查（验收项1：返回 {"ok": true}）"""
    return {"ok": True, "ts": datetime.now().isoformat()}


@app.get("/api/schedule")
async def api_schedule(request: Request, week: int = 1):
    """获取当前用户课表 JSON"""
    uid = request.cookies.get("wxu_uid", "anonymous")
    courses = cv_db.load_curriculum(uid)
    return {"courses": courses, "count": len(courses), "current_week": week}


# ---- 后台任务处理函数 ----
def _parse_pdf_task(task_id: str, uid: str, tmp_path: str, filename: str, kind: str):
    """后台解析 PDF（课表 or 通用文档），结果写 _task_results"""
    try:
        log.info("后台任务开始 task=%s kind=%s file=%s", task_id[:8], kind, filename)
        if kind == "schedule":
            result = parse_uploaded_file(tmp_path)
            courses = result["parsed"]
            if courses:
                cv_db.clear_curriculum(uid)
                cv_db.save_curriculum(uid, courses, source=filename)
            with _task_lock:
                _task_results[task_id] = {
                    "status": "done",
                    "kind": "schedule",
                    "parsed": len(courses),
                    "parse_rate": result["stats"]["parse_rate"],
                    "parse_method": result["parse_method"],
                    "finished_at": datetime.now().isoformat(),
                }
            log.info("课表解析完成 task=%s parsed=%d", task_id[:8], len(courses))
        else:
            # 通用文档上传 → 入向量库
            from src.web_app.upload_handler import process_uploads
            res = process_uploads([tmp_path], category="用户上传")
            with _task_lock:
                _task_results[task_id] = {
                    "status": "done",
                    "kind": "document",
                    "uploaded": res["uploaded"],
                    "chunks_added": res["chunks_added"],
                    "status_code": res["status"],
                    "message": res["message_md"][:500],
                    "finished_at": datetime.now().isoformat(),
                }
            log.info("文档入库完成 task=%s chunks=%d", task_id[:8], res["chunks_added"])
    except Exception as e:
        log.exception("后台任务失败 task=%s", task_id[:8])
        with _task_lock:
            _task_results[task_id] = {
                "status": "error",
                "error": str(e),
                "finished_at": datetime.now().isoformat(),
            }
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/api/schedule/upload")
async def api_schedule_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """上传 PDF 解析课表（BackgroundTasks 异步，1 秒内返回 task_id）"""
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
    with _task_lock:
        _task_results[task_id] = {"status": "pending", "kind": "schedule", "filename": file.filename}
    # 丢后台解析（立即返回）
    background_tasks.add_task(_parse_pdf_task, task_id, uid, tmp.name, file.filename, "schedule")
    memory.save(uid, "user", f"上传课表:{file.filename}", intent="schedule_upload")
    log.info("课表上传已入队 task=%s uid=%s file=%s size=%d", task_id[:8], uid[:8], file.filename, len(content))
    return {"task_id": task_id, "filename": file.filename, "size": len(content)}


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
    with _task_lock:
        _task_results[task_id] = {"status": "pending", "kind": "document", "filename": file.filename}
    background_tasks.add_task(_parse_pdf_task, task_id, uid, tmp.name, file.filename, "document")
    memory.save(uid, "user", f"上传文档:{file.filename}", intent="general")
    log.info("文档上传已入队 task=%s uid=%s file=%s size=%d", task_id[:8], uid[:8], file.filename, len(content))
    return {"task_id": task_id, "filename": file.filename, "size": len(content)}


@app.get("/api/upload/status/{task_id}")
async def api_upload_status(task_id: str):
    """查询后台任务状态"""
    with _task_lock:
        result = _task_results.get(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="task not found")
    return result


@app.post("/api/chat")
async def api_chat(request: Request):
    """
    AI Agent 对话入口(2026-09-23 改成真正的 SSE 流式)

    之前的"流式"是假的:dispatcher.dispatch() 等 LLM 完整生成,然后切成 8 字小段 yield 出去。
    现在用 dispatcher._rag_answer_stream() 走 chain.stream(),LLM 边推理边吐 token,前端边收边渲染。

    sync generator 在 Starlette 里默认在线程池跑,不阻塞 event loop。
    """
    body = await request.json()
    query = body.get("query", "").strip()
    uid = request.cookies.get("wxu_uid", "anonymous")
    if not query:
        raise HTTPException(status_code=400, detail="query required")

    memory.save(uid, "user", query, intent="chat")
    log.info("chat 收到 uid=%s query=%s", uid[:8], query[:50])

    # 1. 同步 intent 检测(快,不动 LLM)
    intent, _ = dispatcher.detect_intent(query)
    dispatcher._extract_preferences(query, uid)

    # 2. sync generator 给 StreamingResponse(在线程池里跑,不阻塞 event loop)
    def event_stream():
        accumulated = []
        if intent == "schedule":
            result = dispatcher._format_schedule(uid, query)
            answer = result["answer"]
            yield f"data: {json.dumps({'text': answer}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'sources': [], 'intent': intent, 'done': True}, ensure_ascii=False)}\n\n"
            memory.save(uid, "assistant", answer, intent=intent)
        elif intent == "schedule_upload":
            answer = "📤 上传课表请去「上传中心」页面,选 PDF 文件即可,我会自动解析存进你的课表。"
            yield f"data: {json.dumps({'text': answer}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'sources': [], 'intent': intent, 'done': True}, ensure_ascii=False)}\n\n"
            memory.save(uid, "assistant", answer, intent=intent)
        elif intent == "image_ocr":
            answer = "🖼️ 识别图片请去「上传中心」上传图片,我会用 OCR 提取文字 + 用视觉模型识别物体。"
            yield f"data: {json.dumps({'text': answer}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'sources': [], 'intent': intent, 'done': True}, ensure_ascii=False)}\n\n"
            memory.save(uid, "assistant", answer, intent=intent)
        else:
            # RAG 路径:真正流式
            for chunk in dispatcher._rag_answer_stream(query, uid):
                if chunk.startswith("__SOURCES__:"):
                    sources_json = chunk[len("__SOURCES__:"):]
                    try:
                        sources = json.loads(sources_json)
                    except Exception:
                        sources = []
                    yield f"data: {json.dumps({'sources': sources, 'intent': intent, 'done': True}, ensure_ascii=False)}\n\n"
                else:
                    accumulated.append(chunk)
                    yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
            memory.save(uid, "assistant", "".join(accumulated), intent=intent)
            log.info("chat 流式完成 uid=%s intent=%s 答案长=%d", uid[:8], intent, sum(len(x) for x in accumulated))
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/recommend")
async def api_recommend(request: Request):
    """个性化推荐"""
    uid = request.cookies.get("wxu_uid", "anonymous")
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
