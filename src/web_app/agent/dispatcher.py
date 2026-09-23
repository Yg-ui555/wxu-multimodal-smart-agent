"""
意图路由分发（2026-09-22）
基于 agent.intent 的意图标签，把 query 分发到对应模块：
    schedule         → curriculum 模块（查课表）
    schedule_upload  → 提示去上传页
    scholarship/kb/news/general → RAG 检索
    image_ocr        → 提示用上传功能

同时联动 memory：
    - 检测"我叫XX"模式 → 存偏好
    - 回答时注入用户偏好（如姓名）做个性化

设计：
    - 纯函数 dispatch(query, uid) -> dict
    - RAG 单例懒加载（首次调用才初始化，避免启动卡死）
    - RAG 不可用时降级为关键词回复，不抛异常
"""

import re
import logging
from typing import Dict, Optional

from . import memory
from .intent import detect_intent

log = logging.getLogger("dispatcher")

# ============== 偏好抽取（"我叫张三"→ name=张三） ==============
_NAME_PATTERNS = [
    re.compile(r"我叫([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z\s]{1,9})"),
    re.compile(r"我是([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z\s]{1,9})"),
    re.compile(r"我的名字是([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z\s]{1,9})"),
]


def _extract_preferences(query: str, uid: str) -> Optional[str]:
    """从 query 抽取偏好存入 memory，返回抽取到的姓名（若有）"""
    for pat in _NAME_PATTERNS:
        m = pat.search(query)
        if m:
            name = m.group(1).strip()
            memory.set_preference(uid, "name", name)
            log.info("抽取偏好: uid=%s name=%s", uid[:8], name)
            return name
    return None


# ============== RAG 单例（懒加载） ==============
_rag_instance = None
_rag_init_lock = None


def _get_rag():
    """懒加载 RAGApp 单例（首次调用才初始化向量库/reranker）"""
    global _rag_instance, _rag_init_lock
    if _rag_instance is not None:
        return _rag_instance
    import threading
    if _rag_init_lock is None:
        _rag_init_lock = threading.Lock()
    with _rag_init_lock:
        if _rag_instance is not None:
            return _rag_instance
        try:
            # 2026-09-22 改：从 src.web_app.app.RAGApp 切到 src.rag.rag_engine.RAGEngine
            # 原 RAGApp 强依赖 Gradio，import 会触发 gradio 加载（占 ~1GB 内存）
            from src.rag.rag_engine import RAGEngine
            rag = RAGEngine()
            if rag.initialize():
                _rag_instance = rag
                log.info("RAG 单例初始化成功")
                return _rag_instance
            else:
                log.warning("RAG 初始化失败，降级为关键词回复")
                return None
        except Exception as e:
            log.warning("RAG 加载异常: %s，降级为关键词回复", e)
            return None


def _rag_answer(query: str, uid: str) -> Dict:
    """调 RAG 检索 + 生成答案（qa_system.answer 内部已含检索）"""
    rag = _get_rag()
    if rag is None:
        return {
            "answer": "📚 知识库正在加载中，请稍等几分钟再试，或者换个简单点的问题问我。",
            "sources": [],
        }
    try:
        if rag.qa_system is None:
            return {"answer": "⚠️ 问答系统未就绪", "sources": []}
        # 注入用户偏好做个性化（追加到 query 末尾，LLM 会理解）
        prefs = memory.get_all_preferences(uid)
        pref_hint = f"（用户叫{prefs['name']}，回答时可适当称呼）" if prefs.get("name") else ""
        result = rag.qa_system.answer(f"{query}{pref_hint}")
        return {
            "answer": result.get("answer", "（无回复）"),
            "sources": result.get("sources", []),
        }
    except Exception as e:
        log.exception("RAG 回答异常")
        return {"answer": f"⚠️ 检索出了点问题：{e}", "sources": []}


# ============== 课表处理 ==============
def _format_schedule(uid: str, query: str) -> Dict:
    """查课表并格式化成文字"""
    try:
        from src.curriculum import db as cv_db
        from datetime import datetime
        courses = cv_db.load_curriculum(uid)
        if not courses:
            return {
                "answer": "📅 你还没上传课表哦，去「上传中心」把课表 PDF 传上来，我就能帮你查课了。",
                "sources": [],
            }

        # 判断查的是哪天
        today_dow = datetime.now().isoweekday()  # 1=周一
        target_dow = today_dow
        day_name = "今天"
        if "明天" in query:
            target_dow = today_dow % 7 + 1
            day_name = "明天"
        elif "后天" in query:
            target_dow = (today_dow + 1) % 7 + 1
            day_name = "后天"
        elif "周" in query:
            m = re.search(r"周([一二三四五六日天])", query)
            if m:
                d = m.group(1)
                mapping = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"日":7,"天":7}
                target_dow = mapping.get(d, today_dow)
                day_name = f"周{d}"

        today_courses = [c for c in courses if c["day_of_week"] == target_dow]
        today_courses.sort(key=lambda c: c["start_section"])

        if not today_courses:
            return {"answer": f"📅 {day_name}你没课，可以休息啦！", "sources": []}

        # 当前周次（简化：用开学日算，这里取默认）
        prefs = memory.get_all_preferences(uid)
        greeting = f"{prefs['name']}，" if prefs.get("name") else ""

        lines = [f"📅 {greeting}{day_name}你的课表："]
        for c in today_courses:
            lines.append(f"  第{c['start_section']}-{c['end_section']}节  {c['course_name']}（{c.get('location','?')}）")
        return {"answer": "\n".join(lines), "sources": []}
    except Exception as e:
        log.exception("课表查询异常")
        return {"answer": f"⚠️ 课表查询出错：{e}", "sources": []}


# ============== 各意图处理函数 ==============
def handle_schedule(query: str, uid: str, intent: str) -> Dict:
    return _format_schedule(uid, query)


def handle_schedule_upload(query: str, uid: str, intent: str) -> Dict:
    return {
        "answer": "📤 上传课表请去「上传中心」页面，选 PDF 文件即可，我会自动解析存进你的课表。",
        "sources": [],
    }


def handle_image_ocr(query: str, uid: str, intent: str) -> Dict:
    return {
        "answer": "🖼️ 识别图片请去「上传中心」上传图片，我会用 OCR 提取文字 + 用视觉模型识别物体。",
        "sources": [],
    }


def handle_rag(query: str, uid: str, intent: str) -> Dict:
    return _rag_answer(query, uid)


def handle_general(query: str, uid: str, intent: str) -> Dict:
    # 先看是不是在自我介绍（存偏好）
    name = _extract_preferences(query, uid)
    if name:
        return {
            "answer": f"你好，{name}！记住啦，以后都这么叫你。有什么想问的尽管说～",
            "sources": [],
        }
    # 闲聊 fallback
    if query in ("你好", "hi", "hello", "在吗", "在不在"):
        prefs = memory.get_all_preferences(uid)
        n = prefs.get("name")
        return {"answer": f"你好{n}！我是无锡学院智能助手，可以问我课表、奖学金、食堂、图书馆这些。" if n else "你好！我是无锡学院智能助手，可以问我课表、奖学金、食堂、图书馆这些。", "sources": []}
    return _rag_answer(query, uid)


# ============== 路由表 ==============
ROUTE_TABLE = {
    "schedule": handle_schedule,
    "schedule_upload": handle_schedule_upload,
    "image_ocr": handle_image_ocr,
    "scholarship": handle_rag,
    "kb": handle_rag,
    "news": handle_rag,
    "general": handle_general,
}


def dispatch(query: str, uid: str) -> Dict:
    """
    总入口：识别意图 → 抽取偏好 → 路由分发 → 返回结果
    Returns:
        {"intent": str, "answer": str, "sources": list}
    """
    if not query or not query.strip():
        return {"intent": "general", "answer": "请输入你的问题～", "sources": []}

    intent, conf = detect_intent(query)
    # 任何意图下都先尝试抽偏好（"我叫张三"可能出现在任意句子里）
    _extract_preferences(query, uid)

    handler = ROUTE_TABLE.get(intent, handle_general)
    try:
        result = handler(query, uid, intent)
        result["intent"] = intent
        return result
    except Exception as e:
        log.exception("dispatch 异常")
        return {"intent": intent, "answer": f"⚠️ 出了点问题：{e}", "sources": []}


if __name__ == "__main__":
    # 自测
    logging.basicConfig(level=logging.INFO)
    test_uid = "test-disp-001"
    for q in ["今天上啥", "我叫张三", "你好", "奖学金怎么申请"]:
        r = dispatch(q, test_uid)
        print(f"\nQ: {q}")
        print(f"  intent: {r['intent']}")
        print(f"  answer: {r['answer'][:80]}")
