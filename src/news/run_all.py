# ============================================================
# 批量抓取 + 处理 8 篇公众号文章
# 抓 → 存盘 → AI 摘要 → 入向量库 → 建索引
# 已处理的会跳过（按来源链接去重）
# ============================================================

import os
import sys
import json
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.news.article_fetcher import fetch_article, ARTICLES_DIR
from src.news.article_processor import process_article
from src.news import news_store

ARTICLE_URLS = [
    "https://mp.weixin.qq.com/s/ykJKvISkvZmWAlG-g6w2wA",
    "https://mp.weixin.qq.com/s/s1aMeylhN6rjcUIvtM6wzg",
    "https://mp.weixin.qq.com/s/Ff2lUXWiJPeHdlRtdsjIzw",
    "https://mp.weixin.qq.com/s/K6Xqtn7dEEulDVdLGKr4Lg",
    "https://mp.weixin.qq.com/s/M_-TyjGFawVVBp9f_IZt5Q",
    "https://mp.weixin.qq.com/s/AbgW318KThnIx5WQj_kZYQ",
    "https://mp.weixin.qq.com/s/FZkkophrkGtJ-iLoTabqHA",
    "https://mp.weixin.qq.com/s/CY7fDPqHD9I2F6JH-NWl0Q",
]


def _find_existing(url: str) -> str | None:
    """看这个链接是否已经抓过且处理过（有 摘要.json）。返回文件夹路径或 None。"""
    if not os.path.isdir(ARTICLES_DIR):
        return None
    for folder in os.listdir(ARTICLES_DIR):
        fpath = os.path.join(ARTICLES_DIR, folder)
        meta_p = os.path.join(fpath, "meta.json")
        summ_p = os.path.join(fpath, "摘要.json")
        if not os.path.exists(meta_p) or not os.path.exists(summ_p):
            continue
        try:
            with open(meta_p, "r", encoding="utf-8") as f:
                if json.load(f).get("来源链接") == url:
                    return fpath
        except Exception:
            continue
    return None


def run_all(force: bool = False):
    """抓 + 处理全部。force=True 时强制重抓已处理的。"""
    total = len(ARTICLE_URLS)
    results = []

    for i, url in enumerate(ARTICLE_URLS, 1):
        print(f"\n{'='*50}")
        print(f"[{i}/{total}] {url}")
        print("=" * 50, flush=True)

        # 跳过已处理（除非 force）
        if not force:
            existing = _find_existing(url)
            if existing:
                print(f"  跳过（已处理）：{os.path.basename(existing)}")
                results.append({"url": url, "ok": True, "skipped": True, "dir": existing})
                continue

        # 抓取
        try:
            fr = fetch_article(url)
        except Exception as e:
            print(f"  ❌ 抓取异常：{type(e).__name__}: {e}")
            results.append({"url": url, "ok": False, "error": f"fetch: {e}"})
            continue

        if not fr.get("ok"):
            print(f"  ❌ 抓取失败：{fr.get('error')}")
            results.append({"url": url, "ok": False, "error": fr.get("error")})
            # 抓失败也继续下一篇，别卡住
            time.sleep(2)
            continue

        print(f"  抓取成功：{fr['title']}（{fr['img_count']}图，{fr['text_len']}字）")

        # 处理
        try:
            pr = process_article(fr["dir"])
        except Exception as e:
            print(f"  ❌ 处理异常：{type(e).__name__}: {e}")
            results.append({"url": url, "ok": False, "error": f"process: {e}", "dir": fr["dir"]})
            continue

        if pr.get("ok"):
            s = pr.get("summary", {})
            print(f"  ✅ 处理完成：[{s.get('类别')}] {s.get('重要性')} / {len(s.get('关键信息', []))} 条关键信息")
            results.append({"url": url, "ok": True, "dir": fr["dir"], "summary": s})
        else:
            print(f"  ❌ 处理失败：{pr.get('error')}")
            results.append({"url": url, "ok": False, "error": pr.get("error"), "dir": fr["dir"]})

        # 礼貌延时，别被微信封
        time.sleep(1.5)

    # 建索引
    print(f"\n{'='*50}")
    print("构建新闻索引…")
    articles = news_store.build_index()
    print(f"索引完成：{len(articles)} 篇文章")

    # 汇总
    ok = sum(1 for r in results if r.get("ok"))
    skip = sum(1 for r in results if r.get("skipped"))
    fail = total - ok
    print(f"\n汇总：成功 {ok}（其中跳过 {skip}），失败 {fail}")
    return results


if __name__ == "__main__":
    force = "--force" in sys.argv
    run_all(force=force)
