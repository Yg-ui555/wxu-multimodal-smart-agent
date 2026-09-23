# ============================================================
# 公众号文章 AI 处理模块
# 输入：一篇已抓取的文章（原文.txt + meta.json）
# 输出：摘要.json + 入 FAISS 向量库（供问答检索）
#
# 摘要.json 结构：
#   {
#     "标题": "...",
#     "类别": "通知/活动/招聘/讲座/其他",
#     "一句话摘要": "...",
#     "关键信息": ["照抄原文的数字/时间/金额/截止/报名方式…"],
#     "重要性": "高/中/低",
#     "图片数": 11
#   }
#
# 关键约束：时间/金额/截止日期等数字必须照抄原文，不许概括。
# ============================================================

import os
import sys
import json
import re

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.documents import Document

# 加载 .env（DeepSeek API key）
env_path = os.path.join(PROJECT_ROOT, ".env")
load_dotenv(env_path, encoding="utf-8-sig")


# ============================================================
# 摘要 Prompt（强约束：数字照抄）
# ============================================================
SUMMARY_PROMPT = """你是一个校园新闻编辑助手。下面是一篇无锡学院公众号文章的正文。
请仔细阅读，然后输出一个 JSON（只输出 JSON，不要任何解释文字）。

JSON 字段要求：
1. "类别"：从这些里选一个最贴切的 —— 通知/活动/招聘/讲座/新闻/其他
   - 通知：学校规定、办事流程、放假安排、奖学金申请等行政类
   - 活动：晚会/比赛/军训/社团/志愿服务等文体类
   - 招聘：校园招聘/实习/兼职
   - 讲座：学术报告/嘉宾分享/公开课
   - 新闻：学校动态/成就报道/会议新闻
   - 其他：以上都不像的
2. "一句话摘要"：20-40 字，说清楚这篇文章讲了什么事。大白话，别拽词。
3. "关键信息"：把学生最关心的信息列成数组，每条一句话。重点抓这些——
   时间（什么时候/截止哪天）、地点、报名方式、金额/名额、联系人、
   注意事项。【重要】所有数字、日期、金额必须照抄原文原话，一个字都不许改不许概括。
   如果文章没涉及这些，就写文章主要讲了什么内容（2-4 条）。
4. "重要性"：高/中/低。
   - 高：和学生近期行动直接相关（要报名/要参加/有截止日期/影响评奖评优）
   - 中：有参考价值但非紧急（活动回顾/学校新闻/成就展示）
   - 低：纯宣传/通用资讯

文章标题：{title}
文章正文：
{body}

输出 JSON："""


# ============================================================
# LLM 调用
# ============================================================
def _get_llm() -> ChatOpenAI:
    """拿一个 DeepSeek ChatOpenAI 实例。"""
    return ChatOpenAI(
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        openai_api_key=os.getenv("LLM_API_KEY"),
        openai_api_base=os.getenv("LLM_BASE_URL"),
        temperature=0.1,  # 摘要要稳定，温度调低
    )


def _parse_json_response(text: str) -> dict:
    """从 LLM 回答里抠出 JSON。容错：去 ```json 包裹、找花括号。"""
    if not text:
        return {}
    # 去 markdown 代码块
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    # 找第一对花括号
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except Exception:
        return {}


# ============================================================
# 主入口：处理一篇文章
# ============================================================
def process_article(article_dir: str, progress=None) -> dict:
    """
    处理一篇已抓取的文章：生成摘要.json + 入向量库。
    article_dir: fetch_article 返回的 dir 路径。
    返回 {ok, summary, error}。
    """
    def _log(msg):
        print(f"[process] {msg}", flush=True)
        if progress is not None:
            try:
                progress(0.5, desc=msg)
            except Exception:
                pass

    txt_path = os.path.join(article_dir, "原文.txt")
    meta_path = os.path.join(article_dir, "meta.json")

    if not os.path.exists(txt_path):
        return {"ok": False, "error": f"找不到原文.txt：{article_dir}"}

    # ---- 读原文 + meta ----
    with open(txt_path, "r", encoding="utf-8-sig") as f:
        raw = f.read()
    # 原文.txt 头部有标题/公众号等元信息，正文在 === 分隔后
    parts = raw.split("=" * 40, 1)
    body = parts[1].strip() if len(parts) > 1 else raw

    title = "未命名文章"
    img_count = 0
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        title = meta.get("标题", title)
        img_count = meta.get("图片数", 0)

    # 正文太短（视频类/纯图片）：照样处理，关键信息会少
    _log(f"正在用 AI 总结：{title}（{len(body)} 字）…")

    # ---- 调 DeepSeek 生成摘要 ----
    try:
        llm = _get_llm()
        prompt = SUMMARY_PROMPT.format(title=title, body=body[:6000])  # 截断防超长
        resp = llm.invoke(prompt)
        summary = _parse_json_response(resp.content if hasattr(resp, "content") else str(resp))
    except Exception as e:
        return {"ok": False, "error": f"LLM 调用失败：{type(e).__name__}: {e}"}

    if not summary:
        return {"ok": False, "error": "LLM 没返回有效 JSON"}

    # 补全字段
    summary.setdefault("标题", title)
    summary.setdefault("类别", "其他")
    summary.setdefault("一句话摘要", "")
    summary.setdefault("关键信息", [])
    summary.setdefault("重要性", "中")
    summary["图片数"] = img_count

    # ---- 存 摘要.json ----
    summary_path = os.path.join(article_dir, "摘要.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    _log(f"摘要完成：{summary.get('类别')} / {summary.get('重要性')} / {len(summary.get('关键信息', []))} 条关键信息")

    # ---- 入向量库（供问答检索）----
    try:
        _add_to_vectorstore(article_dir, title, body, summary)
        _log("已入向量库，可问答")
    except Exception as e:
        # 入库失败不阻塞，摘要已经存了
        _log(f"入向量库失败（不影响摘要）：{type(e).__name__}: {e}")

    return {"ok": True, "summary": summary}


# ============================================================
# 入向量库
# ============================================================
_VSM = None  # 全局单例，避免每篇都重新加载 FAISS


def _get_vsm():
    """VectorStoreManager 单例（首次加载 FAISS 较慢）。"""
    global _VSM
    if _VSM is None:
        from src.rag.vector_store import VectorStoreManager
        from src.rag.data_loader import TextChunker
        _VSM = VectorStoreManager(persist_directory="data/faiss_db_bge")
        _VSM.load_vectorstore()
        _VSM._chunker = TextChunker(chunk_size=800, overlap=150)
    return _VSM


def _add_to_vectorstore(article_dir: str, title: str, body: str, summary: dict):
    """把这篇文章做成 Document，切片后入 FAISS。
    metadata 里存图片路径、重要性、类别，供问答时展示图片和优先级排序。
    """
    vsm = _get_vsm()

    # 组装检索文本：标题 + 类别 + 摘要 + 关键信息 + 原文
    category = summary.get("类别", "")
    one_liner = summary.get("一句话摘要", "")
    keys = "；".join(summary.get("关键信息", []))
    importance = summary.get("重要性", "中")
    content = f"【{title}】\n类别：{category}\n摘要：{one_liner}\n关键信息：{keys}\n\n正文：\n{body}"

    # 收集所有图片的绝对路径（供回答时展示）
    imgs_dir = os.path.join(article_dir, "imgs")
    img_paths = []
    if os.path.isdir(imgs_dir):
        for name in sorted(os.listdir(imgs_dir)):
            img_paths.append(os.path.join(imgs_dir, name))

    doc = Document(
        page_content=content,
        metadata={
            "source": os.path.basename(article_dir),
            "file_name": title,
            "type": "wx_article",
            "category": category,
            "importance": importance,
            "article_dir": article_dir,
            # 图片路径用 ; 分隔（metadata 只支持简单类型）
            "images": ";".join(img_paths),
            "image_count": len(img_paths),
            "key_info": keys,
        },
    )
    chunks = vsm._chunker.split_documents([doc])
    vsm.add_documents(chunks)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        d = sys.argv[1]
    else:
        # 自测：处理第一篇
        d = os.path.join(
            PROJECT_ROOT, "data", "wx_articles",
            "全片上线_2026江苏高校开学第一课_2026-09-22",
        )
    r = process_article(d)
    print(json.dumps(r, ensure_ascii=False, indent=2))
