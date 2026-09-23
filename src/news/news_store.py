# ============================================================
# 新闻索引模块
# 扫描 data/wx_articles/ 下所有已处理文章，汇总成 index.json
# 供 Gradio 新闻页渲染 + 问答检索
# 跳过没有 摘要.json 的文件夹（未处理/废弃）
# ============================================================

import os
import json
import glob

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARTICLES_DIR = os.path.join(PROJECT_ROOT, "data", "wx_articles")
INDEX_PATH = os.path.join(ARTICLES_DIR, "index.json")


def load_article(folder: str) -> dict | None:
    """加载一篇文章的完整信息（摘要 + meta）。无 摘要.json 返回 None。"""
    summary_path = os.path.join(folder, "摘要.json")
    meta_path = os.path.join(folder, "meta.json")
    txt_path = os.path.join(folder, "原文.txt")
    imgs_dir = os.path.join(folder, "imgs")

    if not os.path.exists(summary_path):
        return None  # 未处理，跳过

    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

    # 缩略图：取第一张图
    thumb = ""
    img_files = sorted(glob.glob(os.path.join(imgs_dir, "*"))) if os.path.isdir(imgs_dir) else []
    all_imgs = [os.path.basename(p) for p in img_files]
    if all_imgs:
        thumb = all_imgs[0]

    # 原文
    full_text = ""
    if os.path.exists(txt_path):
        with open(txt_path, "r", encoding="utf-8-sig") as f:
            full_text = f.read()

    return {
        "folder": os.path.basename(folder),
        "dir": folder,
        "标题": summary.get("标题", meta.get("标题", "")),
        "类别": summary.get("类别", "其他"),
        "一句话摘要": summary.get("一句话摘要", ""),
        "关键信息": summary.get("关键信息", []),
        "重要性": summary.get("重要性", "中"),
        "图片数": summary.get("图片数", meta.get("图片数", 0)),
        "公众号": meta.get("公众号", ""),
        "发布时间": meta.get("发布时间", ""),
        "来源链接": meta.get("来源链接", ""),
        "抓取时间": meta.get("抓取时间", ""),
        "缩略图": thumb,
        "图片列表": all_imgs,
        "原文": full_text,
    }


def build_index() -> list:
    """扫描所有已处理文章，构建索引并写入 index.json。返回文章列表。"""
    if not os.path.isdir(ARTICLES_DIR):
        return []

    articles = []
    for folder in sorted(os.listdir(ARTICLES_DIR)):
        fpath = os.path.join(ARTICLES_DIR, folder)
        if not os.path.isdir(fpath):
            continue
        art = load_article(fpath)
        if art is None:
            continue
        articles.append(art)

    # 按发布时间倒序（新的在前）；没日期的排最后
    def _sort_key(a):
        t = a.get("发布时间", "")
        # 取日期部分排序；未知日期排最后
        return (0, t) if t and t != "未知日期" else (1, "")

    articles.sort(key=_sort_key, reverse=True)

    # 写 index.json（去掉超长的"原文"和"图片列表"节省空间，渲染时按需读）
    slim = []
    for a in articles:
        slim.append({k: v for k, v in a.items() if k not in ("原文",)})
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=2)

    print(f"[news_store] 索引完成：{len(articles)} 篇文章", flush=True)
    return articles


def load_index() -> list:
    """读 index.json。没有就现场构建。"""
    if os.path.exists(INDEX_PATH):
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return build_index()


def get_full_text(folder: str) -> str:
    """按文件夹名读原文全文。"""
    txt = os.path.join(ARTICLES_DIR, folder, "原文.txt")
    if os.path.exists(txt):
        with open(txt, "r", encoding="utf-8-sig") as f:
            return f.read()
    return ""


if __name__ == "__main__":
    arts = build_index()
    for a in arts:
        print(f"  [{a['类别']}] {a['标题']}（{a['图片数']}图）")
