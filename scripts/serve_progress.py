"""
实时构建进度展示服务
启动: python scripts/serve_progress.py
访问: http://localhost:7862
"""
import http.server
import socketserver
import json
import re
import time
from urllib.parse import urlparse

LOG_PATH = "logs/build_vectorstore.log"
PORT = 7862


def format_seconds(sec: int) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m}m{s}s"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def parse_log() -> dict:
    """解析构建日志，返回最新状态"""
    state = {
        "stage": "未开始",
        "stage_label": "等待中",
        "batch_current": 0,
        "batch_total": 0,
        "percent": 0,
        "remaining_seconds": 0,
        "remaining_text": "-",
        "status_text": "等待数据...",
        "last_update": "-",
        "success_count": 0,
        "failed_count": 0,
        "chunks": 0,
        "elapsed_seconds": 0,
    }
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        state["status_text"] = "日志文件尚未生成"
        return state

    # 只解析最近一次启动的日志：找最后一个 "启动时间:" 或 "[阶段 1/2]" 标记
    session_start_idx = 0
    for i, ln in enumerate(lines):
        if "启动时间:" in ln or "[阶段 1/2]" in ln:
            session_start_idx = i
    lines = lines[session_start_idx:]

    # 阶段标记
    stage2_active = False
    stage2_started_at = None
    for line in lines:
        if "阶段 1/2" in line:
            state["stage_label"] = "阶段 1: 加载文档"
        elif "阶段 2/2" in line:
            state["stage_label"] = "阶段 2: 向量化"
            stage2_active = True
            # 取时间戳
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if m:
                stage2_started_at = m.group(1)
        elif "构建完成" in line or "向量库创建完成" in line:
            state["stage_label"] = "✅ 构建完成"
            state["stage"] = "done"
            state["percent"] = 100

        # 阶段 2 批次进度（兼容 HH:MM:SS 和 MM:SS 两种格式）
        m = re.search(r"\[阶段 2\] 批次 (\d+)/(\d+) 完成 \((\d+(?:\.\d+)?)s\)\s*预计剩余 (\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})", line)
        if m:
            state["batch_current"] = int(m.group(1))
            state["batch_total"] = int(m.group(2))
            state["percent"] = round(state["batch_current"] / state["batch_total"] * 100, 1)
            remain = m.group(4)
            parts = remain.split(":")
            if len(parts) == 3:
                h, mi, s = int(parts[0]), int(parts[1]), int(parts[2])
            else:  # MM:SS
                h, mi, s = 0, int(parts[0]), int(parts[1])
            state["remaining_seconds"] = h * 3600 + mi * 60 + s
            state["remaining_text"] = remain
            info = re.search(r"\[阶段 2\] (批次 \d+/\d+ 完成[^\n]*)", line)
            if info:
                state["status_text"] = info.group(1)

        # 加载统计
        m = re.search(r"加载完成: 成功 (\d+) / 失败 (\d+)", line)
        if m:
            state["success_count"] = int(m.group(1))
            state["failed_count"] = int(m.group(2))
        m = re.search(r"生成文本块: (\d+)", line)
        if m:
            state["chunks"] = int(m.group(1))

        # 阶段 2 耗时估算（用于计算 elapsed）
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m and stage2_started_at:
            from datetime import datetime
            try:
                t0 = datetime.strptime(stage2_started_at, "%Y-%m-%d %H:%M:%S")
                t1 = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                state["elapsed_seconds"] = int((t1 - t0).total_seconds())
            except Exception:
                pass

    # 最后一行的时间作为 last_update
    if lines:
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", lines[-1])
        if m:
            state["last_update"] = m.group(1)

    return state


HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>WXU 向量库构建进度</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
     background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);
     min-height:100vh;display:flex;align-items:center;justify-content:center;
     padding:20px;color:#333}
.card{background:#fff;border-radius:16px;padding:36px 40px;
      box-shadow:0 20px 60px rgba(0,0,0,.3);max-width:760px;width:100%}
h1{font-size:24px;margin-bottom:6px}
.subtitle{color:#999;font-size:13px;margin-bottom:24px}
.stage{display:inline-block;padding:6px 14px;border-radius:20px;
       background:#e3f2fd;color:#1976d2;font-size:13px;font-weight:600;margin-bottom:18px}
.stage.done{background:#d4edda;color:#155724}
.progress-bar{background:#f0f0f0;border-radius:14px;height:42px;
              overflow:hidden;position:relative;margin-bottom:8px}
.progress-fill{height:100%;background:linear-gradient(90deg,#4facfe 0%,#00f2fe 100%);
               transition:width .8s ease;display:flex;align-items:center;
               justify-content:flex-end;padding-right:14px;color:#fff;
               font-weight:700;font-size:15px;min-width:60px}
.progress-fill.done{background:linear-gradient(90deg,#43e97b 0%,#38f9d7 100%)}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:22px}
.stat{padding:14px;background:#f8f9fa;border-radius:10px;text-align:center}
.stat-label{color:#888;font-size:12px;margin-bottom:6px}
.stat-value{color:#222;font-size:18px;font-weight:700}
.status-text{background:#f8f9fa;padding:12px 16px;border-radius:8px;
             font-family:"Consolas","Monaco",monospace;font-size:13px;
             color:#555;margin-top:16px;word-break:break-all;
             min-height:42px;display:flex;align-items:center}
.last-update{color:#aaa;font-size:12px;margin-top:14px;text-align:right}
.footer{color:#888;font-size:11px;text-align:center;margin-top:10px}
.pulse{display:inline-block;width:8px;height:8px;border-radius:50%;
       background:#43e97b;margin-right:6px;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
</style>
</head>
<body>
<div class="card">
  <h1>🧠 WXU 向量库构建进度</h1>
  <p class="subtitle">实时监控 <code>logs/build_vectorstore.log</code></p>
  <div class="stage" id="stage">⏳ 加载中...</div>
  <div class="progress-bar">
    <div class="progress-fill" id="fill" style="width:0%">0%</div>
  </div>
  <div class="stats">
    <div class="stat"><div class="stat-label">批次进度</div><div class="stat-value" id="batch">- / -</div></div>
    <div class="stat"><div class="stat-label">预计剩余</div><div class="stat-value" id="remaining">-</div></div>
    <div class="stat"><div class="stat-label">成功/失败</div><div class="stat-value" id="files">-</div></div>
    <div class="stat"><div class="stat-label">总文本块</div><div class="stat-value" id="chunks">-</div></div>
  </div>
  <div class="status-text" id="status">等待数据...</div>
  <div class="last-update" id="last">-</div>
  <div class="footer"><span class="pulse"></span>每 3 秒自动刷新 · 仅本地访问</div>
</div>
<script>
async function tick(){
  try{
    const r=await fetch('/api/progress');
    const d=await r.json();
    const fill=document.getElementById('fill');
    fill.style.width = (d.percent||0)+'%';
    fill.textContent = (d.percent||0)+'%';
    if(d.stage==='done'){fill.classList.add('done');fill.textContent='✅ 100%'}
    const stg=document.getElementById('stage');
    stg.textContent = d.stage_label||'加载中...';
    if(d.stage==='done') stg.classList.add('done');
    document.getElementById('batch').textContent = d.batch_current+' / '+d.batch_total;
    document.getElementById('remaining').textContent = d.remaining_text||'-';
    document.getElementById('files').textContent = d.success_count+' / '+d.failed_count;
    document.getElementById('chunks').textContent = d.chunks||0;
    document.getElementById('status').textContent = d.status_text||'等待数据...';
    document.getElementById('last').textContent = '最后更新: '+(d.last_update||'-');
  }catch(e){console.error(e)}
}
tick(); setInterval(tick, 3000);
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 安静

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/progress":
            data = parse_log()
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def main():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    # 缩短 socket 超时，避免 CloseWait 堆积
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"进度服务: http://localhost:{PORT}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()