"""
每日自动抓取无锡学院官网通知 + 增量入向量库

监控页面：
- 校级通知公告
- 教务处通知公告 / 公示 / 校内文件
- 校园要闻 / 校园快讯
- 会议安排

工作流：
1. 每天凌晨 2 点（或首次启动时）抓取每个监控页面的列表
2. 跟 crawl_state.json 比对，只抓"新增"的详情
3. 用 scrape_url.py 的工具提取正文 → 保存为 .txt → data/docs/学校通知/
4. 调 scripts/add_new_docs.py 增量入向量库
5. 写日志到 logs/daily_crawl.log

两种模式：
  --once   跑一次（手动测试 / Windows Task Scheduler）
  (无参)  daemon 模式（APScheduler 每天 18:00 自动跑）

用法：
  python scripts/daily_crawl.py --once       # 跑一次
  python scripts/daily_crawl.py             # 长期后台运行
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse
import re

import requests
from bs4 import BeautifulSoup

PROJECT = r"D:\wxu\wxu-multimodal-smart-agent"
os.chdir(PROJECT)
sys.path.insert(0, PROJECT)

# 路径配置
STATE_FILE = "logs/crawl_state.json"
LOG_FILE = "logs/daily_crawl.log"

# 要监控的页面（核心调度信息）
# pages: 翻页数量（1 = 只看第1页）
# 注意：路径式分页 URL 模板里用 {page} 占位符；不需要翻页的页面省略 {page}（不会替换）
WATCHED_PAGES = [
    {
        "name": "校级通知公告",
        "list_url": "https://www.cwxu.edu.cn/notice_list.jsp?urltype=tree.TreeTempUrl&wbtreeid=1039",
        "target_dir": "data/docs/学校通知/校级通知",
        "pages": 1,
    },
    {
        "name": "教务处通知公告",
        "list_url": "https://jwc.cwxu.edu.cn/index/tzgg{page}.htm",  # tzgg.htm / tzgg/2.htm / tzgg/3.htm...
        "target_dir": "data/docs/学校通知/教务处通知公告",
        "pages": 15,  # 保守：只翻最近 ~15 页（约 6 个月 × 12 条/页 = 180 条）
    },
    {
        "name": "教务处公示",
        "list_url": "https://jwc.cwxu.edu.cn/gs{page}.htm",
        "target_dir": "data/docs/学校通知/教务处公示",
        "pages": 5,
    },
    {
        "name": "教务处校内文件",
        "list_url": "https://jwc.cwxu.edu.cn/gzzd/xnwj{page}.htm",
        "target_dir": "data/docs/学校通知/教务处校内文件",
        "pages": 3,
    },
    {
        "name": "校园要闻",
        "list_url": "https://www.cwxu.edu.cn/index/xyyw{page}.htm",
        "target_dir": "data/docs/校园要闻",
        "pages": 3,
    },
    {
        "name": "校园快讯",
        "list_url": "https://www.cwxu.edu.cn/index/xykx{page}.htm",
        "target_dir": "data/docs/校园快讯",
        "pages": 3,
    },
    {
        "name": "会议安排",
        "list_url": "https://www.cwxu.edu.cn/hyan_list.jsp?urltype=tree.TreeTempUrl&wbtreeid=1040",
        "target_dir": "data/docs/学校通知/会议安排",
        "pages": 1,
    },
    # ===== 新增 5 个高价值页面 =====
    {
        "name": "教学日历",
        "list_url": "https://jwc.cwxu.edu.cn/dwgk/jxrl{page}.htm",
        "target_dir": "data/docs/学校通知/教学日历",
        "pages": 1,  # 校历本身就3-5 条历史
    },
    {
        "name": "人才培养",
        "list_url": "https://jwc.cwxu.edu.cn/jxyj/rcpy{page}.htm",
        "target_dir": "data/docs/学校通知/人才培养",
        "pages": 2,
    },
    {
        "name": "选课管理",
        "list_url": "https://jwc.cwxu.edu.cn/jwgl/xkgl{page}.htm",
        "target_dir": "data/docs/学校通知/选课管理",
        "pages": 2,
    },
    {
        "name": "办事流程",
        "list_url": "https://jwc.cwxu.edu.cn/ywbl/bslc{page}.htm",
        "target_dir": "data/docs/学校通知/办事流程",
        "pages": 2,
    },
    {
        "name": "教学新闻",
        "list_url": "https://jwc.cwxu.edu.cn/index/jxxw{page}.htm",
        "target_dir": "data/docs/学校通知/教学新闻",
        "pages": 2,
    },
    # ====== 2026-09-19 新增:补 9/16 审计指出的 5 类缺口(图书馆/学工/体育)======
    {
        "name": "图书馆通知",
        "list_url": "https://lib.cwxu.edu.cn/index/tzgg.htm",
        "target_dir": "data/docs/图书馆/通知",
        "pages": 1,  # 站点不含 {page},仅抓首页
    },
    {
        "name": "学工处通知",
        "list_url": "https://xgc.cwxu.edu.cn/index/tzgg.htm",
        "target_dir": "data/docs/学工处/通知",
        "pages": 1,
    },
    {
        "name": "体育部通知",
        "list_url": "https://tyb.cwxu.edu.cn/index/tzgg.htm",
        "target_dir": "data/docs/体育部/通知",
        "pages": 1,
    },
    # ====== 2026-09-19 后勤管理处（zwc.cwxu.edu.cn）5 个栏目 ======
    {
        "name": "后勤工作动态",
        "list_url": "https://zwc.cwxu.edu.cn/index/gzdt.htm",
        "target_dir": "data/docs/后勤处/工作动态",
        "pages": 1,
    },
    {
        "name": "后勤服务通知",
        "list_url": "https://zwc.cwxu.edu.cn/hqfw1.htm",
        "target_dir": "data/docs/后勤处/服务通知",
        "pages": 1,
    },
    {
        "name": "校园管理通知",
        "list_url": "https://zwc.cwxu.edu.cn/xygl.htm",
        "target_dir": "data/docs/后勤处/校园管理",
        "pages": 1,
    },
    {
        "name": "基建工作通知",
        "list_url": "https://zwc.cwxu.edu.cn/jjgz1.htm",
        "target_dir": "data/docs/后勤处/基建工作",
        "pages": 1,
    },
    {
        "name": "公共卫生通知",
        "list_url": "https://zwc.cwxu.edu.cn/ggws.htm",
        "target_dir": "data/docs/后勤处/公共卫生",
        "pages": 1,
    },
]


# 日期过滤：只入库最近 N 个月的通知
# 列表页的标题/链接里有日期字符串（如 "2026-09-14"）
import datetime as _dt
RECENT_MONTHS = 6
_cuttoff_date = _dt.date.today() - _dt.timedelta(days=RECENT_MONTHS * 30)
CUTOFF_DATE_STR = _cuttoff_date.strftime("%Y-%m-%d")  # e.g. "2026-03-16"


def _is_recent(title: str, date_str: str = "") -> bool:
    """判断 title 或 date_str 是否在最近 N 个月内"""
    # 修复 LOW #7（2026-09-17）：删函数内 `import re`，模块顶部 L34 已 import
    # 从 title 提取日期（如 "通知标题2026-09-14"）
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", title)
    if not m:
        m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", date_str)
    if not m:
        return True  # 找不到日期 → 默认保留（避免漏掉）
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        item_date = _dt.date(y, mo, d)
        return item_date >= _cuttoff_date
    except Exception:
        return True


# ============================================================
# 日志
# ============================================================
def setup_logger():
    """配置日志：同时输出到文件和控制台"""
    logger = logging.getLogger("daily_crawl")
    logger.setLevel(logging.INFO)

    # 清空旧 handler
    logger.handlers.clear()

    # 文件 handler（追加模式）
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    # 控制台 handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


LOG = setup_logger()


# ============================================================
# State 管理（URL 去重）
# ============================================================
def load_state():
    """加载已抓取的 URL 列表"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "seen_urls": [],
        "last_run": None,
        "total_crawled": 0,
        "history": [],  # 每次运行的统计
    }


def save_state(state):
    """保存 state"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def normalize_url(url: str) -> str:
    """
    URL 归一化（修复 HIGH #7 2026-09-17）。

    旧版只用 `url.split('#')[0]`、`urlparse` 是死代码，导致同一新闻
    https://x.com/info/123 与 http://x.com/info/123 被视为不同 URL
    重复抓取。

    现实现真正归一：
    - 统一 scheme 到 https
    - 去掉 fragment
    - host 转小写、去前缀 www.
    - 末尾 / 归一
    """
    parsed = urlparse(url.strip())
    scheme = "https"
    netloc = (parsed.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


# ============================================================
# 抓取逻辑
# ============================================================
def _probe_network(timeout=5):
    """
    断网短路(2026-09-20 加):探测学校主站是否可达。
    失败 = 校园网断 / DNS 故障 / 睡眠 → 跳过本次抓取,避免 20 个页面全部空跑。
    返回 True/False。
    """
    try:
        requests.get("https://www.cwxu.edu.cn", timeout=timeout,
                     headers={"User-Agent": "WXUBot/1.0"})
        return True
    except Exception as e:
        LOG.warning(f"探活失败: {e}")
        return False


def fetch_page(url, timeout=15, retries=3, backoff=1.5):
    """抓取一个 URL，返回 HTML 文本(2026-09-20 加 retry + 状态码校验)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WXUBot/1.0 (Educational)"
    }
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout, headers=headers)
            r.raise_for_status()  # 4xx/5xx 抛 HTTPError
            r.encoding = r.apparent_encoding or 'utf-8'
            return r.text
        except Exception as e:
            if attempt == retries - 1:
                LOG.error(f"FAIL fetch ({retries}x retry): {url} - {e}")
                return None
            LOG.warning(f"  retry {attempt + 1}/{retries - 1}: {url} - {type(e).__name__}")
            time.sleep(backoff ** attempt)  # 1s, 1.5s, 2.25s
    return None


def extract_items_from_list(html, list_url):
    """
    从通知列表页提取 (title, url, summary) 三元组
    兼容多种页面结构：
    - 教务处通知页：<a href="../info/1100/5954.htm"><em>标题</em><span>日期</span></a>
    - 校级通知页：<a href="#"><h2>标题</h2><p>摘要（长正文）</p></a>
    - 校园要闻：<a href="info/1033/21279.htm" target="_blank">标题</a>

    过滤策略：只匹配 href 含 'info/' 的链接（或 href=# + 有摘要）

    返回：[(title, url, summary), ...]
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")

        # 提取标题：优先 em / h2 / h3
        title_tag = a.find(["em", "h2", "h3"])
        if title_tag:
            title = title_tag.get_text(strip=True)
        else:
            # 回退：从 a.get_text 提取（去掉日期 [YYYY-MM-DD]）
            text = a.get_text(" ", strip=True)
            text = re.sub(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', '', text).strip()
            title = re.sub(r'^\[[^\]]+\]', '', text).strip()
            title = title.split('\n')[0].strip()

        # 过滤：无标题、太短的标题、明显不是通知
        if not title or len(title) < 5 or len(title) > 120:
            continue
        if any(skip in title for skip in ["更多", "查看更多", "首页", "上页", "下页", "尾页", "转到"]):
            continue

        # 提取摘要（如果有）
        p_tag = a.find("p")
        summary = p_tag.get_text(strip=True) if p_tag else ""

        # 提取日期：从 <span>（教务处格式）或 a.get_text 里找
        # 注意：教务处通知格式是 <em>标题</em><span>2026-09-14</span>
        # 主站格式是 <h2>标题</h2>（无日期在子元素里，需要从 a.get_text 找）
        date_in_a = ""
        span_tag = a.find("span")
        if span_tag:
            span_text = span_tag.get_text(strip=True)
            if re.match(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', span_text):
                date_in_a = span_text
        if not date_in_a:
            # 从 a.get_text 提取（去掉标题部分后的剩余）
            a_full_text = a.get_text(" ", strip=True)
            m_date = re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', a_full_text)
            if m_date:
                date_in_a = m_date.group(0)

        # 判断是否通知链接
        is_real_link = 'info/' in href
        is_js_link = (href in ('#', '', 'javascript:void(0)') or href.startswith('javascript:'))

        if is_real_link:
            # 用 urljoin 处理 ../info/ 这种相对路径
            absolute = urljoin(list_url, href)
            url = normalize_url(absolute)
        elif is_js_link and summary and len(summary) >= 30:
            # JS 链接 + 有摘要 → 用 list_url + 标题作为唯一 key
            url = f"{list_url}#{title}"
        else:
            continue

        if url in seen_urls:
            continue
        seen_urls.add(url)

        items.append((title, url, summary, date_in_a))

    return items


def fetch_and_save_detail(url, target_dir, summary=""):
    """
    抓取详情页（或直接用 summary），提取正文，保存为 .txt
    返回 (filepath, content_length) 或 (None, 0)
    """
    # 如果 url 是 JS 占位符（#），直接用 summary
    if "#" in url and not url.startswith("http"):
        # JS 链接：用 summary 作为正文
        if not summary or len(summary) < 30:
            return None, 0
        title = url.split("#")[-1] if "#" in url else "untitled"
        os.makedirs(target_dir, exist_ok=True)
        from scrape_url import save_to_doc
        filepath = save_to_doc(summary, title, target_dir, "list_summary")
        return filepath, len(summary)

    # 真实 URL：抓详情页
    from scrape_url import fetch_url, extract_text, extract_title, save_to_doc

    html = fetch_url(url)
    if not html:
        return None, 0

    title = extract_title(html) or ""
    content = extract_text(html)

    if len(content) < 50:
        LOG.warning(f"  内容过短（{len(content)}字），跳过: {url}")
        return None, 0

    os.makedirs(target_dir, exist_ok=True)
    filepath = save_to_doc(content, title, target_dir, url)

    return filepath, len(content)


def add_files_to_vectorstore(file_paths):
    """
    把指定 .txt 文件加载、切块、增量加入 FAISS 向量库
    比 add_new_docs.py 更快：只处理指定的文件，不做全量扫描
    """
    from src.rag.vector_store import VectorStoreManager
    from src.rag.data_loader import DocumentLoader, TextChunker

    # 1. 加载 + 清洗
    loader = DocumentLoader("data/docs")
    all_docs = []
    for fp in file_paths:
        try:
            docs = loader.load_document(fp)
            for doc in docs:
                doc.page_content = loader.clean_text(doc.page_content)
                doc.metadata["source"] = os.path.relpath(fp, "data/docs")
                doc.metadata["crawled_at"] = datetime.now().isoformat()
            all_docs.extend(docs)
        except Exception as e:
            LOG.error(f"  加载失败: {fp} - {e}")

    if not all_docs:
        LOG.warning("  无有效文档，跳过入库")
        return 0

    # 2. 切块
    chunker = TextChunker(chunk_size=800, overlap=150)
    chunks = chunker.split_documents(all_docs)
    LOG.info(f"  共 {len(all_docs)} 个文档 → {len(chunks)} 个 chunks")

    # 3. 加载已有向量库 + 添加新 chunks
    manager = VectorStoreManager(persist_directory="data/faiss_db_bge")
    manager.load_vectorstore()
    manager.add_documents(chunks)

    return len(chunks)


def run_crawl_once(state):
    """
    跑一次完整抓取流程
    Returns: dict with stats
    """
    seen_urls = set(state.get("seen_urls", []))
    new_urls_by_page = {}
    saved_files = []
    stats = {
        "pages_scanned": 0,
        "items_found": 0,
        "new_items": 0,
        "saved": 0,
        "failed_pages": 0,
        "total_chars": 0,
    }

    # 2026-09-20 断网短路:先探活学校主站,失败就别浪费时间扫 20 个页面
    if not _probe_network():
        LOG.error("断网短路:探活失败,跳过本次抓取")
        LOG.error("  可能原因:校园网断网 / DNS 故障 / 笔记本睡眠")
        stats["failed_pages"] = len(WATCHED_PAGES)
        return stats

    LOG.info("=" * 60)
    LOG.info(f"开始抓取 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    LOG.info("=" * 60)

    # 1. 扫描所有列表页，找新增（支持翻页）
    for page in WATCHED_PAGES:
        page_name = page["name"]
        url_template = page["list_url"]
        target_dir = page["target_dir"]
        pages = page.get("pages", 1)

        page_new_items = []
        page_total_items = 0

        for page_num in range(1, pages + 1):
            # 替换 {page} 占位符：page_num=1 时去掉 .htm 前的 /1，page_num>=2 时加 /N
            if "{page}" in url_template:
                if page_num == 1:
                    list_url = url_template.replace("{page}.htm", ".htm").replace("{page}", "")
                else:
                    list_url = url_template.replace("{page}", f"/{page_num}")
            else:
                list_url = url_template  # 不支持翻页

            LOG.info(f"[扫描] {page_name} 第 {page_num}/{pages} 页: {list_url}")
            html = fetch_page(list_url)
            if not html:
                LOG.warning(f"  抓取失败，跳过此页")
                stats["failed_pages"] += 1
                continue

            items = extract_items_from_list(html, list_url)
            # 日期过滤：只保留最近 N 个月（用 title 和 date_in_a 联合判断）
            items_recent = [(t, u, s, d) for (t, u, s, d) in items if _is_recent(t, d)]
            n_filtered = len(items) - len(items_recent)
            if n_filtered:
                LOG.info(f"  日期过滤: {n_filtered}/{len(items)} 条被过滤（早于 {CUTOFF_DATE_STR}）")

            new_items = [(t, u, s) for (t, u, s, _) in items_recent if u not in seen_urls]
            page_total_items += len(items_recent)
            page_new_items.extend(new_items)

            # 加入 seen（防止重复）
            for _, u, _, _ in items_recent:
                seen_urls.add(u)

            # 如果这一页没有任何近期内容，可能到历史页了 → 提前停
            if items and not items_recent:
                LOG.info(f"  本页内容全部早于 {CUTOFF_DATE_STR}，停止翻页")
                break

            time.sleep(0.5)

        LOG.info(f"  {page_name} 合计: 列表 {page_total_items}, 新增 {len(page_new_items)}")
        stats["pages_scanned"] += pages
        stats["items_found"] += page_total_items
        stats["new_items"] += len(page_new_items)
        new_urls_by_page[page_name] = page_new_items

        time.sleep(0.5)

    # 2. 抓详情页并保存
    LOG.info("-" * 60)
    LOG.info("开始抓取详情页")
    for page_name, items in new_urls_by_page.items():
        if not items:
            continue

        # 找 target_dir
        target_dir = next(p["target_dir"] for p in WATCHED_PAGES if p["name"] == page_name)
        LOG.info(f"[抓取] {page_name}（{len(items)} 条新增）")

        for title, url, summary in items:
            try:
                filepath, char_count = fetch_and_save_detail(url, target_dir, summary)
                if filepath:
                    saved_files.append(filepath)
                    stats["saved"] += 1
                    stats["total_chars"] += char_count
                    LOG.info(f"  + {title[:40]} ({char_count}字)")
                time.sleep(0.5)
            except Exception as e:
                LOG.error(f"  FAIL: {url} - {e}")

    # 3. 增量入向量库
    if saved_files:
        LOG.info("-" * 60)
        LOG.info(f"[入库] 增量入向量库（{len(saved_files)} 个新文件）")
        try:
            add_files_to_vectorstore(saved_files)
        except Exception as e:
            LOG.error(f"  入库异常: {e}", exc_info=True)
            LOG.error("  可手动跑: python scripts/add_new_docs.py")

    # 4. 更新 state
    state["seen_urls"] = list(seen_urls)
    state["last_run"] = datetime.now().isoformat()
    state["total_crawled"] = state.get("total_crawled", 0) + stats["saved"]
    state["history"] = state.get("history", [])[-20:]  # 保留最近 20 次
    state["history"].append({
        "time": state["last_run"],
        "new_items": stats["new_items"],
        "saved": stats["saved"],
        "failed_pages": stats["failed_pages"],
    })
    save_state(state)

    LOG.info("=" * 60)
    LOG.info(f"完成: 新增 {stats['new_items']} 条, 实际保存 {stats['saved']} 条, "
             f"共 {stats['total_chars']} 字")
    LOG.info(f"累计抓取: {state['total_crawled']} 条")
    LOG.info("=" * 60)

    return stats


def run_daemon(schedule_time="18:00"):
    """daemon 模式：每天定时跑"""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError:
        LOG.error("需要安装 apscheduler: pip install apscheduler")
        return

    scheduler = BlockingScheduler()

    def job():
        try:
            state = load_state()
            run_crawl_once(state)
        except Exception as e:
            LOG.error(f"任务异常: {e}", exc_info=True)

    # 解析 schedule_time (HH:MM) 给 cron 用（之前是硬编码 02:00,导致 --time 参数不生效,2026-09-24 修）
    try:
        hh, mm = schedule_time.split(":")
        hh, mm = int(hh), int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
    except ValueError:
        LOG.error(f"schedule_time 格式错误: {schedule_time!r},应为 HH:MM,回退到 18:00")
        hh, mm = 18, 0

    # 按 schedule_time 定时跑
    scheduler.add_job(job, 'cron', hour=hh, minute=mm, id='daily_crawl')
    LOG.info(f"daemon 启动：每天 {schedule_time} 自动抓取")
    LOG.info("首次启动立即跑一次...")

    # 首次启动立即跑一次
    try:
        state = load_state()
        run_crawl_once(state)
    except Exception as e:
        LOG.error(f"首次运行失败: {e}", exc_info=True)

    LOG.info("按 Ctrl+C 停止")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        LOG.info("daemon 已停止")


def main():
    parser = argparse.ArgumentParser(description="每日抓取无锡学院官网通知")
    parser.add_argument("--once", action="store_true", help="跑一次（默认 daemon 模式）")
    parser.add_argument("--time", default="18:00", help="daemon 模式下每天的运行时间 (HH:MM)")
    parser.add_argument("--reset", action="store_true", help="重置 state（强制全量抓取）")
    args = parser.parse_args()

    if args.reset and os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        LOG.warning("State 已重置，下次会全量抓取")

    if args.once:
        state = load_state()
        run_crawl_once(state)
    else:
        run_daemon(args.time)


if __name__ == "__main__":
    main()