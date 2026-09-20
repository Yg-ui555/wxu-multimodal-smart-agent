# -*- coding: utf-8 -*-
"""
HTML 评估报告生成器（无前端依赖，纯 matplotlib + 字符串模板）
"""
import base64
import io
import json
from datetime import datetime
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")  # 无显示后端
    import matplotlib.pyplot as plt
    # 中文乱码 fix
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False


def _bar_chart_png64(baseline_summary: dict, current_summary: dict) -> str:
    """生成 baseline vs current 对比柱状图，返回 base64 PNG data-uri"""
    if not HAVE_MPL:
        return ""

    metrics = [
        ("pass_rate", "通过率", lambda s: s.get("pass_rate", 0) * 100),
        ("recall_at_4_rate", "recall@4", lambda s: s.get("recall_at_4_rate", 0) * 100),
        ("answer_hit_rate", "answer_hit", lambda s: s.get("answer_hit_rate", 0) * 100),
        ("avg_precision", "精度", lambda s: s.get("avg_precision", 0) * 100),
    ]

    fig, axes = plt.subplots(1, 2, figsize=(12, 4))

    # Left: 指标 bar chart
    ax = axes[0]
    labels = [m[1] for m in metrics]
    base_vals = [m[2](baseline_summary or {}) for m in metrics]
    curr_vals = [m[2](current_summary) for m in metrics]
    x = range(len(labels))
    width = 0.35
    ax.bar([i - width/2 for i in x], base_vals, width, label="基线", color="#94a3b8")
    ax.bar([i + width/2 for i in x], curr_vals, width, label="当前", color="#22d3ee")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=15)
    ax.set_ylabel("%")
    ax.set_ylim(0, 105)
    ax.set_title("📊 指标对比（%）")
    for i, (b, c) in enumerate(zip(base_vals, curr_vals)):
        ax.text(i - width/2, b + 1.5, f"{b:.0f}", ha="center", fontsize=8)
        ax.text(i + width/2, c + 1.5, f"{c:.0f}", ha="center", fontsize=8)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    # Right: 响应时长
    ax = axes[1]
    lats = ["p50", "p90", "avg"]
    base_lat = [
        (baseline_summary or {}).get("latency_p50_sec", 0),
        (baseline_summary or {}).get("latency_p90_sec", 0),
        (baseline_summary or {}).get("latency_avg_sec", 0),
    ]
    curr_lat = [
        current_summary.get("latency_p50_sec", 0),
        current_summary.get("latency_p90_sec", 0),
        current_summary.get("latency_avg_sec", 0),
    ]
    x = range(len(lats))
    ax.bar([i - width/2 for i in x], base_lat, width, label="基线", color="#94a3b8")
    ax.bar([i + width/2 for i in x], curr_lat, width, label="当前", color="#f59e0b")
    ax.set_xticks(list(x))
    ax.set_xticklabels(lats)
    ax.set_ylabel("秒")
    ax.set_title("⏱ 响应时长（s）")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _summary_card(cs: dict) -> str:
    s = cs.get("summary", {})
    n = s.get("total", 0)
    p = s.get("passed", 0)
    pr = s.get("pass_rate", 0) * 100
    return f"""
    <div class="summary">
      <div class="card"><h3>{n}</h3><p>总题数</p></div>
      <div class="card"><h3>{p}</h3><p>通过</p></div>
      <div class="card"><h3>{pr:.0f}%</h3><p>通过率</p></div>
      <div class="card"><h3>{s.get("recall_at_4_rate", 0)*100:.0f}%</h3><p>recall@4</p></div>
      <div class="card"><h3>{s.get("answer_hit_rate", 0)*100:.0f}%</h3><p>answer_hit</p></div>
      <div class="card"><h3>{s.get("latency_p50_sec", 0):.1f}s</h3><p>p50 延迟</p></div>
    </div>
    """


def _vs_table(baseline: dict, current: dict) -> str:
    """基线 vs 当前 数值对比表"""
    if not baseline:
        return "<p class='hint'>（无基线数据；首次跑会存为 baseline。）</p>"
    bs = baseline.get("summary", {})
    cs = current.get("summary", {})
    keys = [
        ("pass_rate", "通过率", lambda s: f"{s.get('pass_rate', 0)*100:.1f}%"),
        ("recall_at_4_rate", "recall@4", lambda s: f"{s.get('recall_at_4_rate', 0)*100:.1f}%"),
        ("answer_hit_rate", "answer_hit", lambda s: f"{s.get('answer_hit_rate', 0)*100:.1f}%"),
        ("avg_precision", "精度", lambda s: f"{s.get('avg_precision', 0)*100:.1f}%"),
        ("latency_p50_sec", "p50 (s)", lambda s: f"{s.get('latency_p50_sec', 0):.1f}"),
        ("latency_p90_sec", "p90 (s)", lambda s: f"{s.get('latency_p90_sec', 0):.1f}"),
        ("banned_violations", "禁用词违规", lambda s: f"{s.get('banned_violations', 0)}"),
    ]
    rows = []
    for k, label, fn in keys:
        b = fn(bs)
        c = fn(cs)
        # parse number for arrow
        try:
            bv = float(b.rstrip("%"))
            cv = float(c.rstrip("%"))
            delta = cv - bv
            arrow = "📈" if delta > 0 else ("📉" if delta < 0 else "⏸")
        except Exception:
            arrow = ""
        rows.append(f"<tr><td>{label}</td><td>{b}</td><td>{c}</td><td>{arrow}</td></tr>")
    return f"""
    <table class="vs">
      <thead><tr><th>指标</th><th>基线</th><th>当前</th><th>变化</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def _bad_cases_section(cases: list) -> str:
    """失败案例详细表格"""
    failed = [c for c in cases if not c.get("passed")]
    if not failed:
        return "<p class='ok'>🎉 全部通过！</p>"

    rows = []
    for c in failed:
        kw_html = " ".join(f"<code>{k}</code>" for k in (c.get("expected_keywords") or []))
        bk_html = " ".join(f"<code>{b}</code>" for b in (c.get("banned_keywords") or []))
        ans = (c.get("answer") or "").replace("<", "&lt;").replace(">", "&gt;")
        rows.append(f"""
        <tr>
          <td><b>#{c['id']}</b><br><small>{c.get('category','')} · {c.get('difficulty','')}</small></td>
          <td>
            <div><b>Q:</b> {c.get('question','')}</div>
            <div class="ans"><b>A:</b> <span class="bad">{ans}</span></div>
            <div class="tags">
              {('期望: ' + kw_html) if kw_html else ''}
              {('禁止: ' + bk_html) if bk_html else ''}
              {f"<small>· 命中源: {c.get('sources',[])[0] if c.get('sources') else '无'}</small>" if c.get('sources') else ''}
            </div>
          </td>
          <td>
            <span class="bad">❌ 未通过</span><br>
            <small>recall={c.get('recall@4')} ans_hit={c.get('answer_hit')}</small><br>
            <small>latency={c.get('latency_sec')}s</small>
          </td>
        </tr>
        """)

    return f"""
    <h2>❌ 失败案例（{len(failed)} 条）</h2>
    <table class="cases">
      <thead><tr><th># / 类</th><th>对话详情</th><th>指标</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


HTML_TMPL = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RAG 评估报告 · {saved_at}</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; margin: 0; padding: 24px; background: #0f172a; color: #e2e8f0; }}
  h1 {{ color: #67e8f9; margin: 0 0 8px; }}
  h2 {{ color: #fde68a; margin-top: 32px; }}
  .meta {{ color: #94a3b8; font-size: 13px; margin-bottom: 24px; }}
  .summary {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin: 16px 0 32px; }}
  .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 16px; text-align: center; }}
  .card h3 {{ margin: 0; color: #67e8f9; font-size: 28px; }}
  .card p {{ margin: 4px 0 0; color: #94a3b8; font-size: 12px; }}
  img.chart {{ display: block; margin: 16px auto; max-width: 100%; border-radius: 8px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; background: #1e293b; }}
  th, td {{ padding: 10px 12px; border: 1px solid #334155; text-align: left; vertical-align: top; }}
  th {{ background: #334155; color: #fde68a; }}
  .vs td:nth-child(2), .vs td:nth-child(3) {{ text-align: center; font-family: monospace; }}
  .ans {{ margin: 6px 0; padding: 6px 8px; background: #0f172a; border-radius: 4px; font-size: 13px; }}
  .ans .bad {{ color: #fca5a5; }}
  .tags {{ font-size: 12px; color: #94a3b8; }}
  .ok {{ color: #4ade80; font-size: 16px; padding: 12px; background: #064e3b; border-radius: 8px; }}
  code {{ background: #0f172a; padding: 1px 6px; border-radius: 3px; color: #67e8f9; font-size: 12px; }}
  small {{ color: #64748b; }}
  .bad {{ color: #fca5a5; }}
</style>
</head><body>
<h1>📊 RAG 评估报告</h1>
<div class="meta">
  生成时间：{saved_at} &nbsp;·&nbsp; 库：<code>{vector_db_dir}</code> &nbsp;·&nbsp; 用例：{total} 条
</div>
{summary_card}
<h2>📈 基线 vs 当前对比</h2>
{chart_img_html}
{vs_table_html}
{passed_section_html}
{failed_section_html}
<h2>✅ 通过案例（{passed_count} 条）</h2>
<p class="hint">通过案例摘要（source · latency）</p>
<table><thead><tr><th>#</th><th>题</th><th>命中源</th><th>延迟</th></tr></thead><tbody>
{passed_rows_html}
</tbody></table>
</body></html>
"""


def render_html(baseline: dict, current: dict, out_path: str):
    """生成可分享的 HTML 报告"""
    cs = current
    cases = cs.get("cases", [])
    total = len(cases)
    passed_cases = [c for c in cases if c.get("passed")]
    failed_cases = [c for c in cases if not c.get("passed")]

    chart_png = _bar_chart_png64(
        baseline.get("summary", {}) if baseline else {},
        cs.get("summary", {}),
    )
    chart_img_html = f'<img class="chart" src="data:image/png;base64,{chart_png}" />' if chart_png else "<p class='hint'>matplotlib 未安装，跳过图表</p>"

    passed_rows = "".join(
        f"<tr><td>#{c['id']}</td><td>{c.get('question','')[:60]}</td><td><small>{(c.get('sources') or ['?'])[0]}</small></td><td>{c.get('latency_sec')}s</td></tr>"
        for c in passed_cases
    )

    html = HTML_TMPL.format(
        saved_at=cs.get("saved_at", ""),
        vector_db_dir=cs.get("vector_db_dir", "?"),
        total=total,
        summary_card=_summary_card(cs),
        chart_img_html=chart_img_html,
        vs_table_html=_vs_table(baseline, current),
        passed_section_html="",  # placeholder for legacy
        failed_section_html=_bad_cases_section(cases),
        passed_count=len(passed_cases),
        passed_rows_html=passed_rows,
    )

    Path(out_path).write_text(html, encoding="utf-8")


if __name__ == "__main__":
    # 直接用 dev mode 跑：拿 baseline + current 各一份
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    base = json.load(open(Path(__file__).parent.parent / "tests" / "eval_baseline.json", "r", encoding="utf-8"))
    cur = json.load(open(Path(__file__).parent.parent / "tests" / "eval_current.json", "r", encoding="utf-8"))
    render_html(baseline=base, current=cur, out_path="eval_report.html")
    print("Wrote eval_report.html")
