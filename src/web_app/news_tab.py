# ============================================================
# 学院新闻聚合页（Gradio Tab）
# 分类筛选 + 文章卡片 + 详情查看（原文 + 原图）
# 复用小U紫粉主题
# ============================================================

import os
import json
import gradio as gr

from src.news import news_store

# 类别 → emoji + 颜色
CAT_STYLE = {
    "通知": ("📢", "#3b82f6"),
    "活动": ("🎉", "#8b5cf6"),
    "招聘": ("💼", "#f59e0b"),
    "讲座": ("🎤", "#10b981"),
    "新闻": ("📰", "#6366f1"),
    "其他": ("📌", "#6b7280"),
}

IMP_STYLE = {
    "高": ("🔥", "#ef4444"),
    "中": ("⭐", "#f59e0b"),
    "低": ("📝", "#9ca3af"),
}


def _badge(text: str, color: str, bg: str = "") -> str:
    """生成一个小徽章 HTML。"""
    return f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:{bg or color}22;color:{color};font-size:12px;margin-right:6px">{text}</span>'


def _card_html(article: dict) -> str:
    """单篇文章卡片 HTML。"""
    cat = article.get("类别", "其他")
    emoji, color = CAT_STYLE.get(cat, CAT_STYLE["其他"])
    imp = article.get("重要性", "中")
    i_emoji, i_color = IMP_STYLE.get(imp, IMP_STYLE["中"])

    thumb = article.get("缩略图", "")
    title = article.get("标题", "未命名")
    summary = article.get("一句话摘要", "")
    keys = article.get("关键信息", [])
    date = article.get("发布时间", "")
    img_count = article.get("图片数", 0)

    # 关键信息列表（最多 3 条）
    keys_html = ""
    for k in keys[:3]:
        keys_html += f'<li>{k}</li>'
    if len(keys) > 3:
        keys_html += f'<li style="color:#9ca3af">…还有 {len(keys)-3} 条</li>'

    thumb_html = ""
    if thumb:
        # 缩略图用本地文件路径（Gradio 能渲染 file= 路径）
        img_path = os.path.join(article.get("dir", ""), "imgs", thumb)
        thumb_html = f'<img src="file://{img_path}" style="width:100%;height:140px;object-fit:cover;border-radius:8px">'

    return f"""
    <div class="wxu-news-card">
        <div class="wxu-news-thumb">{thumb_html}</div>
        <div class="wxu-news-body">
            <div class="wxu-news-title">{title}</div>
            <div class="wxu-news-meta">
                {_badge(f"{emoji} {cat}", color)}
                {_badge(f"{i_emoji} {imp}", i_color)}
                <span style="font-size:12px;color:#9ca3af">{date} · {img_count}图</span>
            </div>
            <div class="wxu-news-summary">{summary}</div>
            <ul class="wxu-news-keys">{keys_html}</ul>
        </div>
    </div>
    """


def _cards_html(articles: list, category: str = "全部") -> str:
    """所有文章卡片拼成网格 HTML。"""
    if category and category != "全部":
        articles = [a for a in articles if a.get("类别") == category]

    if not articles:
        return '<div style="text-align:center;padding:40px;color:#9ca3af">暂无该类文章</div>'

    cards = "".join(_card_html(a) for a in articles)
    css = """
    <style>
    .wxu-news-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:16px; padding:8px; }
    .wxu-news-card { background:#fff; border:1px solid #f0e6ff; border-radius:12px; overflow:hidden; transition:box-shadow .2s; }
    .wxu-news-card:hover { box-shadow:0 4px 16px rgba(139,92,246,.15); }
    .wxu-news-thumb { width:100%; }
    .wxu-news-body { padding:12px 14px; }
    .wxu-news-title { font-size:16px; font-weight:600; color:#1f2937; margin-bottom:6px; line-height:1.4; }
    .wxu-news-meta { margin-bottom:8px; }
    .wxu-news-summary { font-size:13px; color:#6b7280; line-height:1.5; margin-bottom:8px; }
    .wxu-news-keys { margin:0; padding-left:18px; }
    .wxu-news-keys li { font-size:12px; color:#4b5563; line-height:1.6; }
    </style>
    """
    return f"{css}<div class='wxu-news-grid'>{cards}</div>"


def _detail_md(article: dict) -> str:
    """文章详情 Markdown（供下拉选中后展示）。"""
    if not article:
        return "_从上面选一篇文章看详情_"

    cat = article.get("类别", "其他")
    emoji, _ = CAT_STYLE.get(cat, CAT_STYLE["其他"])
    imp = article.get("重要性", "中")
    i_emoji, _ = IMP_STYLE.get(imp, IMP_STYLE["中"])

    md = f"# {article.get('标题','未命名')}\n\n"
    md += f"{emoji} **{cat}** · {i_emoji} **{imp}** · 📅 {article.get('发布时间','')} · 📷 {article.get('图片数',0)}张图\n\n"
    md += f"> {article.get('一句话摘要','')}\n\n"
    md += "## 关键信息\n\n"
    for k in article.get("关键信息", []):
        md += f"- {k}\n"
    md += f"\n## 原文\n\n来源：{article.get('公众号','')} · [原文链接]({article.get('来源链接','')})\n\n"
    # 原文（去掉头部元信息）
    full = article.get("原文", "")
    if "=" * 40 in full:
        full = full.split("=" * 40, 1)[-1].strip()
    md += full
    return md


def _article_choices(articles: list, category: str = "全部") -> list:
    """生成下拉选项：标题 (类别)。"""
    if category and category != "全部":
        articles = [a for a in articles if a.get("类别") == category]
    return [f"{a.get('标题','未命名')}（{a.get('类别','')}）" for a in articles]


def build_news_tab():
    """构建新闻页 Tab，返回需要绑定的事件组件。"""
    # 先加载索引
    articles = news_store.load_index()

    with gr.Tab(label="📰 学院新闻"):
        gr.HTML('<div class="wxu-section-title"><span class="wxu-live-dot"></span>📰 学院新闻聚合</div>')

        with gr.Row():
            cat_filter = gr.Radio(
                label="按类别筛选",
                choices=["全部", "通知", "活动", "招聘", "讲座", "新闻", "其他"],
                value="全部",
                interactive=True,
                scale=2,
            )
            refresh_btn = gr.Button("🔄 刷新", size="sm", scale=1)

        # 卡片网格
        cards_box = gr.HTML(value=_cards_html(articles, "全部"))

        gr.HTML('<div class="wxu-section-title" style="margin-top:24px">📖 点选看全文 + 原图</div>')
        with gr.Row():
            with gr.Column(scale=1):
                article_pick = gr.Dropdown(
                    label="选择文章",
                    choices=_article_choices(articles, "全部"),
                    interactive=True,
                )
            with gr.Column(scale=2):
                detail_md = gr.Markdown(value="_从左侧选一篇文章看详情_")
                detail_gallery = gr.Gallery(
                    label="原文图片",
                    columns=3,
                    height=360,
                    show_label=True,
                )

        # ---- 事件绑定 ----
        # 筛选变化 → 刷新卡片 + 文章下拉
        def _on_filter(cat):
            arts = news_store.load_index()
            return _cards_html(arts, cat), gr.update(choices=_article_choices(arts, cat), value=None)

        cat_filter.change(
            fn=_on_filter,
            inputs=cat_filter,
            outputs=[cards_box, article_pick],
        )

        # 选文章 → 显示详情 + 图片
        def _on_pick(pick, cat):
            if not pick:
                return "_从左侧选一篇文章看详情_", []
            arts = news_store.load_index()
            # 按"标题（类别）"匹配
            for a in arts:
                label = f"{a.get('标题','未命名')}（{a.get('类别','')}）"
                if label == pick:
                    # 读原文 + 图片
                    a = news_store.load_article(a["dir"]) or a
                    img_dir = os.path.join(a.get("dir", ""), "imgs")
                    imgs = []
                    if os.path.isdir(img_dir):
                        for name in sorted(a.get("图片列表", [])):
                            imgs.append(os.path.join(img_dir, name))
                    return _detail_md(a), imgs
            return "_没找到这篇文章_", []

        article_pick.change(
            fn=_on_pick,
            inputs=[article_pick, cat_filter],
            outputs=[detail_md, detail_gallery],
        )

        # 刷新按钮 → 重建索引 + 刷新全部
        def _on_refresh(cat):
            arts = news_store.build_index()
            return _cards_html(arts, cat), gr.update(choices=_article_choices(arts, cat), value=None), "_已刷新_"

        refresh_btn.click(
            fn=_on_refresh,
            inputs=cat_filter,
            outputs=[cards_box, article_pick, detail_md],
        )
