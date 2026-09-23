# ============================================================
# 公众号文章抓取模块
# 抓取 → 存盘（链接会过期，当场存）
# 每篇文章一个文件夹：data/wx_articles/<标题_日期>/
#   ├── 原文.txt    正文文字
#   ├── imgs/       全部图片（带 Referer 防盗链）
#   └── meta.json   来源链接/抓取时间/公众号名/发布时间
# ============================================================

import os
import re
import json
import time
import hashlib
from datetime import datetime
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARTICLES_DIR = os.path.join(PROJECT_ROOT, "data", "wx_articles")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def _safe_name(title: str, date: str) -> str:
    """把标题转成安全的文件夹名：去特殊字符、限长。"""
    if not title:
        title = "未命名文章"
    # 只保留中文/英文/数字
    safe = re.sub(r'[^\w一-鿿]', '_', title)
    safe = re.sub(r'_+', '_', safe).strip('_')
    if len(safe) > 40:
        safe = safe[:40]
    date_part = date or "未知日期"
    return f"{safe}_{date_part}"


def _guess_ext(url: str, content_type: str) -> str:
    """从 url 或响应头猜图片扩展名。"""
    ct = (content_type or "").lower()
    if "gif" in ct:
        return ".gif"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    path = urlparse(url).path.lower()
    for ext in (".gif", ".png", ".jpg", ".jpeg", ".webp"):
        if path.endswith(ext):
            return ext
    return ".jpg"  # 默认 jpg


def _ts_to_date(ts: str) -> str:
    """Unix 时间戳 → 可读日期。失败返回原值。"""
    try:
        n = int(ts)
        # 毫秒时间戳就除 1000
        if n > 1e12:
            n = int(n / 1000)
        return datetime.fromtimestamp(n).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts


def _extract_publish_time(soup: BeautifulSoup, html: str) -> str:
    """发布时间经常取不到，多兜几层，取不到就标未知。"""
    # 1. 标准 meta（页面已渲染好的可直接用）
    node = soup.find(id="publish_time")
    if node and node.get_text(strip=True):
        return node.get_text(strip=True)
    # 2. script 里的 var ct = "..."（常是 Unix 时间戳）
    m = re.search(r'var\s+ct\s*=\s*["\'](\d+)["\']', html)
    if m:
        return _ts_to_date(m.group(1))
    # 3. JS create_time 数字
    m = re.search(r'"create_time"\s*:\s*"?(\d{4}[-/]\d{1,2}[-/]\d{1,2}[^"]*)"?', html)
    if m:
        return m.group(1)
    m = re.search(r'"create_time"\s*:\s*(\d{9,})', html)
    if m:
        return _ts_to_date(m.group(1))
    return "未知日期"


def fetch_article(url: str, progress=None) -> dict:
    """
    抓取一篇公众号文章，存到 data/wx_articles/<标题_日期>/。
    返回 {ok, dir, title, img_count, error}。

    progress: 可选 gradio.Progress，用于前端显示进度。
    """
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    def _log(msg):
        print(f"[fetch] {msg}", flush=True)
        if progress is not None:
            try:
                progress(0.5, desc=msg)
            except Exception:
                pass

    try:
        # ---- 1. 抓 HTML ----
        _log("正在下载文章页面…")
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # ---- 2. 取标题/公众号名/发布时间 ----
        title_node = soup.find(id="activity-name")
        title = title_node.get_text(strip=True) if title_node else "未命名文章"

        name_node = soup.find(id="js_name")
        author = name_node.get_text(strip=True) if name_node else "未知公众号"

        publish_time = _extract_publish_time(soup, html)
        # 提取日期部分做文件夹名
        date_part = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', publish_time)
        date_for_dir = date_part.group(1).replace("/", "-") if date_part else "未知日期"

        # ---- 3. 取正文 ----
        content = soup.find(id="js_content")
        if content is None:
            return {"ok": False, "error": "正文 div #js_content 没找到，页面结构可能变了"}
        full_text = content.get_text("\n", strip=True)

        # ---- 4. 建文件夹 ----
        folder_name = _safe_name(title, date_for_dir)
        # 避免重名
        article_dir = os.path.join(ARTICLES_DIR, folder_name)
        suffix = 1
        while os.path.exists(article_dir):
            article_dir = os.path.join(ARTICLES_DIR, f"{folder_name}_{suffix}")
            suffix += 1
        imgs_dir = os.path.join(article_dir, "imgs")
        os.makedirs(imgs_dir, exist_ok=True)

        # ---- 5. 存原文 ----
        txt_path = os.path.join(article_dir, "原文.txt")
        with open(txt_path, "w", encoding="utf-8-sig") as f:
            f.write(f"{title}\n")
            f.write(f"公众号：{author}\n")
            f.write(f"发布时间：{publish_time}\n")
            f.write(f"来源链接：{url}\n")
            f.write("=" * 40 + "\n\n")
            f.write(full_text)

        # ---- 6. 下载图片（带 Referer 防盗链）----
        _log("正在下载图片…")
        img_tags = content.find_all("img")
        img_urls = []
        for im in img_tags:
            src = im.get("data-src") or im.get("src")
            if src and src.startswith("http"):
                img_urls.append(src)
        # 去重保序
        seen = set()
        img_urls = [u for u in img_urls if not (u in seen or seen.add(u))]

        img_paths = []
        for i, img_url in enumerate(img_urls):
            try:
                ir = requests.get(
                    img_url,
                    headers={"User-Agent": UA, "Referer": url},
                    timeout=30,
                )
                if ir.status_code != 200:
                    continue
                ext = _guess_ext(img_url, ir.headers.get("Content-Type", ""))
                fname = f"img_{i+1:02d}{ext}"
                fpath = os.path.join(imgs_dir, fname)
                with open(fpath, "wb") as f:
                    f.write(ir.content)
                img_paths.append({"index": i + 1, "file": fname, "url": img_url})
            except Exception as e:
                _log(f"  图片 {i+1} 下载失败：{type(e).__name__}")
                continue
            # 礼貌延时，别被封
            time.sleep(0.15)

        # ---- 7. 存 meta ----
        meta = {
            "标题": title,
            "公众号": author,
            "发布时间": publish_time,
            "来源链接": url,
            "抓取时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "正文字数": len(full_text),
            "图片数": len(img_paths),
            "图片列表": img_paths,
        }
        with open(os.path.join(article_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        _log(f"完成：{title}（{len(img_paths)} 张图，{len(full_text)} 字）")
        return {
            "ok": True,
            "dir": article_dir,
            "title": title,
            "img_count": len(img_paths),
            "text_len": len(full_text),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    # 自测：抓一篇
    test_url = "https://mp.weixin.qq.com/s/ykJKvISkvZmWAlG-g6w2wA"
    r = fetch_article(test_url)
    print(json.dumps(r, ensure_ascii=False, indent=2))
