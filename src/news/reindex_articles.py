# ============================================================
# 把已有摘要的文章重新入向量库
# （只做 FAISS 写入，不删任何文件）
# 用法：python -m src.news.reindex_articles
# ============================================================

import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.news.article_processor import _add_to_vectorstore
from src.news.article_fetcher import ARTICLES_DIR
from src.news import news_store


def reindex_all():
    """把所有有 摘要.json 的文章重新入 FAISS。"""
    if not os.path.isdir(ARTICLES_DIR):
        print("wx_articles 目录不存在")
        return

    articles = news_store.build_index()
    if not articles:
        print("没有已处理的文章")
        return

    print(f"准备把 {len(articles)} 篇文章入向量库…")
    success = 0
    for a in articles:
        folder = a.get("folder", "")
        article_dir = os.path.join(ARTICLES_DIR, folder)
        summary_path = os.path.join(article_dir, "摘要.json")
        txt_path = os.path.join(article_dir, "原文.txt")
        if not os.path.exists(summary_path) or not os.path.exists(txt_path):
            continue

        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)
        with open(txt_path, "r", encoding="utf-8-sig") as f:
            raw = f.read()
        parts = raw.split("=" * 40, 1)
        body = parts[1].strip() if len(parts) > 1 else raw
        title = summary.get("标题", a.get("标题", ""))

        try:
            _add_to_vectorstore(article_dir, title, body, summary)
            print(f"  ✅ {title}（{summary.get('类别','')}）已入库，{summary.get('图片数',0)} 张图")
            success += 1
        except Exception as e:
            print(f"  ❌ {title} 入库失败：{type(e).__name__}: {e}")

    print(f"\n入库完成：{success}/{len(articles)} 篇")


if __name__ == "__main__":
    reindex_all()
