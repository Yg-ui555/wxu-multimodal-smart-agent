"""
URL 内容抓取脚本
用法: python scripts/scrape_url.py <url1> <url2> ...
功能：抓取网页，提取正文，保存为 .txt 到 data/docs/学校通知/

适合场景:
- 教务处通知 (https://jwc.cwxu.edu.cn/info/...)
- 学工处通知 (https://xgc.cwxu.edu.cn/info/...)
- 任何 HTML 内容页
"""
import sys
import os
import re
import urllib.request
from html.parser import HTMLParser
from datetime import datetime


class TextExtractor(HTMLParser):
    """HTML 文本提取器：去除脚本/样式，保留正文"""

    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.skip_tags = {'script', 'style', 'meta', 'link', 'noscript'}
        self.skip_depth = 0
        self.in_body = False
        self.current_tag_stack = []

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.skip_depth += 1
        elif tag in ('p', 'div', 'br', 'tr', 'h1', 'h2', 'h3', 'li', 'td'):
            self.text_parts.append('\n')
        if tag == 'body':
            self.in_body = True
        self.current_tag_stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.skip_tags and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag in ('p', 'div', 'tr', 'h1', 'h2', 'h3', 'li'):
            self.text_parts.append('\n')
        if self.current_tag_stack and self.current_tag_stack[-1] == tag:
            self.current_tag_stack.pop()

    def handle_data(self, data):
        if self.skip_depth == 0 and self.in_body:
            text = data.strip()
            if text:
                self.text_parts.append(text)

    def get_text(self):
        # 合并连续空白
        text = ''.join(self.text_parts)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()


def fetch_url(url, timeout=15, retries=3, backoff=1.5, inter_request_delay=0.3):
    """下载 URL 内容(2026-09-20 加 retry,2026-09-24 加 anti-SSL-disconnect 防御)

    Args:
        url: 目标 URL
        timeout: 单次超时(秒)
        retries: 重试次数
        backoff: 基础退避倍数(SSL 错误时 ×2)
        inter_request_delay: 每次请求前的固定延迟(秒),防止高频触发 SSL 断开
            2026-09-24 加:抓教务处历史页 (cwxu.edu.cn/info/1035/46xx.htm) 时,
            无延迟并发请求 → 学校服务器 SSL 断开防御 (UNEXPECTED_EOF_WHILE_READING),
            85 个 URL 失败。设 0.3s 后总抓取时间 +15s,SSL 失败率大幅降低。
    """
    import time
    import ssl
    import socket
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 WXUBot/1.0'
    }
    # 2026-09-24 加:每次请求前的全局延迟,避免高频触发 SSL 断开
    time.sleep(inter_request_delay)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                # 状态码校验(urllib 默认 4xx/5xx 会抛 HTTPError,但显式更稳)
                if resp.status >= 400:
                    raise urllib.error.HTTPError(url, resp.status, 'HTTP Error', resp.headers, None)
                # 编码探测
                content_type = resp.headers.get('Content-Type', '')
                body = resp.read()
                if 'charset=gb' in content_type.lower() or 'charset=gbk' in content_type.lower():
                    return body.decode('gbk', errors='ignore')
                if 'charset=utf' in content_type.lower():
                    return body.decode('utf-8', errors='ignore')
                # 默认 utf-8，失败回退 gbk
                for enc in ('utf-8', 'gbk'):
                    try:
                        return body.decode(enc)
                    except UnicodeDecodeError:
                        continue
                return body.decode('utf-8', errors='ignore')
        except (ssl.SSLError, socket.timeout, ConnectionError, TimeoutError) as e:
            # 2026-09-24 SSL/连接错误特判:backoff 加倍(避免雪崩),日志标 [SSL]
            delay = (backoff ** attempt) * 2
            if attempt == retries - 1:
                print(f"  FAIL fetch_url [SSL/Conn {retries}x retry, backoff x2]: {url} - {e}")
                return None
            print(f"  retry {attempt + 1}/{retries - 1} [SSL backoff {delay:.1f}s]: {url} - {type(e).__name__}")
            time.sleep(delay)
        except Exception as e:
            # 其他错误:标准 backoff
            if attempt == retries - 1:
                print(f"  FAIL fetch_url ({retries}x retry): {url} - {e}")
                return None
            print(f"  retry {attempt + 1}/{retries - 1}: {url} - {type(e).__name__}")
            time.sleep(backoff ** attempt)
    return None


def extract_title(html):
    """从 HTML 提取标题"""
    m = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return 'untitled'


def extract_text(html):
    """提取正文文本（用正则先定位 vsb_content 区域，再用 HTMLParser 清理）"""
    import re
    # 1. 提取核心内容区域（学校网站通常用 vsb_content 包裹正文）
    content_patterns = [
        r'<div[^>]*id="vsb_content[^"]*"[^>]*>(.*?)</div>\s*<div',
        r'<div[^>]*class="v_news_content"[^>]*>(.*?)</div>',
        r'<div[^>]*id="content"[^>]*>(.*?)</div>\s*<div[^>]*class="footer',
        r'<article[^>]*>(.*?)</article>',
        r'<main[^>]*>(.*?)</main>',
    ]
    inner = html
    for pat in content_patterns:
        m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
        if m:
            inner = m.group(1)
            break

    # 2. 移除 script / style / 注释
    inner = re.sub(r'<script[^>]*>.*?</script>', '', inner, flags=re.DOTALL | re.IGNORECASE)
    inner = re.sub(r'<style[^>]*>.*?</style>', '', inner, flags=re.DOTALL | re.IGNORECASE)
    inner = re.sub(r'<!--.*?-->', '', inner, flags=re.DOTALL)

    # 3. 替换块级标签为换行
    inner = re.sub(r'<(p|div|br|tr|h[1-6]|li|td|th)[^>]*>', '\n', inner, flags=re.IGNORECASE)
    inner = re.sub(r'</(p|div|tr|h[1-6]|li|td|th)>', '\n', inner, flags=re.IGNORECASE)

    # 4. 移除所有剩余 HTML 标签
    inner = re.sub(r'<[^>]+>', '', inner)

    # 5. HTML 实体解码（基础）
    entity_map = {
        '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>',
        '&quot;': '"', '&apos;': "'", '&#39;': "'", '&mdash;': '—',
        '&hellip;': '…', '&middot;': '·',
    }
    for ent, char in entity_map.items():
        inner = inner.replace(ent, char)

    # 6. 规范化空白
    inner = re.sub(r'[ \t]+', ' ', inner)
    inner = re.sub(r'\n{3,}', '\n\n', inner)

    return inner.strip()


def detect_site(url):
    """根据 URL 判断应该放到哪个目录"""
    if 'jwc.' in url or 'jwc.' in url:
        return 'data/docs/学校通知'
    if 'xgc.' in url or 'xgc.' in url:
        return 'data/docs/学校通知'
    if 'zs.' in url:
        return 'data/docs/招生就业'
    return 'data/docs/网站抓取'


def save_to_doc(content, title, target_dir, source_url):
    """保存为 .txt 文件"""
    os.makedirs(target_dir, exist_ok=True)

    # 文件名清洗：去掉特殊字符，限制长度
    safe_title = re.sub(r'[\\/:*?"<>|\r\n]', '', title)
    safe_title = safe_title[:80].strip()

    if not safe_title:
        safe_title = f'page_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

    filepath = os.path.join(target_dir, f'{safe_title}.txt')

    # 写入内容（含元信息头）
    content_with_meta = f"""# {title}

来源 URL: {source_url}
抓取时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

{content}

---
（本文档由 scripts/scrape_url.py 自动抓取）
"""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content_with_meta)

    return filepath


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/scrape_url.py <url1> [url2] [url3] ...")
        print("示例:")
        print("  python scripts/scrape_url.py https://jwc.cwxu.edu.cn/info/1100/5934.htm")
        sys.exit(1)

    urls = sys.argv[1:]
    print(f"准备抓取 {len(urls)} 个 URL")
    print("=" * 60)

    saved_files = []
    for url in urls:
        try:
            print(f"\n[抓取] {url}")
            html = fetch_url(url)
            title = extract_title(html)
            content = extract_text(html)
            target_dir = detect_site(url)
            filepath = save_to_doc(content, title, target_dir, url)
            print(f"  [OK] 标题: {title}")
            print(f"  [OK] 字数: {len(content)} 字符")
            print(f"  [OK] 保存: {filepath}")
            saved_files.append(filepath)
        except Exception as e:
            print(f"  [FAIL] 失败: {e}")

    print("\n" + "=" * 60)
    print(f"完成! 共保存 {len(saved_files)} 个文件")

    if saved_files:
        print("\n下一步: 重新构建向量库（增量构建几分钟就完）")
        print("  python src/rag/build_vectorstore.py")
        print("\n然后重启 app.py，问'奖学金评定通知'就能答了")


if __name__ == '__main__':
    main()