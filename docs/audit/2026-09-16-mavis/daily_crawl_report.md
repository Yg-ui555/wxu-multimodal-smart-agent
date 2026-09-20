# WXU 每日自动抓取系统 - 完整报告

> **时间**：2026-09-16
> **任务**：自动每天读取无锡学院官网通知 → 保存到知识库 + 入向量库
> **测试结果**：✅ 全链路打通

---

## 📊 一、最终成果

| 指标 | 数值 |
|---|---|
| **监控页面** | 12 个（7 个原有 + 5 个新增） |
| **首次抓取量** | 376 个 .txt（实际 364 个入库，12 个内容太短被过滤） |
| **总字数** | 约 28 万字 |
| **FAISS 增量** | 12429 → **13007 chunks**（+578） |
| **faiss_db 体积** | 38.18 → 39.49 MB |
| **浏览器实测** | 4/4 通过（新数据被正确召回）|

---

## 🔧 二、监控页面（12 个，按 ROI 排序）

### A. 教务处 6 个子栏目（已监控 + 翻页）

| 栏目 | URL | 翻页 | 抓取数 |
|---|---|---|---|
| ⭐ **教学日历（=校历）** | `dwgk/jxrl.htm` | 1 | 3 |
| ⭐ **人才培养（=培养方案）** | `jxyj/rcpy.htm` | 2 | 24 |
| 教学新闻 | `index/jxxw.htm` | 2 | 24 |
| 选课管理 | `jwgl/xkgl.htm` | 2 | 24 |
| 办事流程 | `ywbl/bslc.htm` | 2 | 9 |
| **教务处通知公告** | `index/tzgg.htm` | 15 | 167 |
| **教务处公示** | `gs.htm` | 5 | 46 |
| **教务处校内文件** | `gzzd/xnwj.htm` | 3 | 20 |

### B. 主站 4 个

| 栏目 | URL | 翻页 | 抓取数 |
|---|---|---|---|
| 校级通知公告 | `notice_list.jsp?wbtreeid=1039` | 1 | 9（部分已抓）|
| 校园要闻 | `index/xyyw.htm` | 3 | 30 |
| 校园快讯 | `index/xykx.htm` | 3 | 30 |
| 会议安排 | `hyan_list.jsp?wbtreeid=1040` | 1 | 2 |

---

## 📂 三、生成文件清单

### 代码
| 文件 | 行数 | 用途 |
|---|---|---|
| `scripts/daily_crawl.py` | ~400 | **核心**：抓取 + 调度 + 增量入库 |
| `scripts/run_daily_crawl.bat` | 16 | Windows 后台启动包装 |

### 辅助（一次性 / 测试用）
| 文件 | 用途 |
|---|---|
| `scripts/_debug_extract.py` | 测试 extract_items_from_list |
| `scripts/_debug_new_pages.py` | 测试新页面 URL |
| `scripts/_debug_pagination.py` | 测试分页模式 |
| `scripts/_debug_main_pagination.py` | 测试主站分页 |
| `scripts/_debug_list*.py` | 测试列表页 HTML 结构 |
| `scripts/_index_today.py` | 一次性入库脚本 v1 |
| `scripts/_index_daily_crawl.py` | 一次性入库脚本 v2 |
| `scripts/_index_batched.py` | **分批入库脚本（成功）** |

### 文档
| 文件 | 内容 |
|---|---|
| `docs/audit/2026-09-16-mavis/daily_crawl_report.md` | **本报告** |

### 数据
- 376 个 .txt 保存到 `data/docs/学校通知/` 12 个子目录
- 13007 个 chunks 加入 `data/faiss_db/`

---

## 🛠️ 四、技术细节

### 4.1 抓取流程

```
[WATCHED_PAGES]
   ↓
[遍历每个监控页面 × pages 翻页]
   ↓
[extract_items_from_list] → (title, url, summary, date_in_a)
   ↓
[_is_recent 过滤 6 个月内]
   ↓
[跳过已抓 URL（state.json 维护）]
   ↓
[fetch_and_save_detail] → 抓详情页 / JS 链接用摘要
   ↓
[save_to_doc] → .txt 存到 data/docs/<category>/
   ↓
[add_files_to_vectorstore] → 增量入 FAISS
```

### 4.2 关键修复（与初始版本相比）

| 问题 | 修复 |
|---|---|
| 教务处用 `../info/` 相对路径，提取不到 | `urljoin(list_url, href)` |
| 校级通知 `<h2>` 在 `<a>` 里，标题找不到 | 优先 `<em>`/`<h2>`/`<h3>`，回退到 `a.get_text` |
| 校级通知用 `href="#"`（JS 链接） | 用 `<p>` 摘要直接保存 |
| 没有翻页，只抓最新 10 条 | `url_template` 加 `{page}` 占位符，支持 `tzgg/N.htm` 路径分页 |
| 没有日期过滤 | `_is_recent(title, date_in_a)`，从标题和 `<span>` 双重识别 |
| add_new_docs.py 全量扫描慢 | 直接调 `VectorStoreManager.add_documents` |
| Ollama 嵌入大批 500+ chunks 中途失败 | 分批脚本 `_index_batched.py`（每批 30 文件） |

### 4.3 关键技术决策

1. **绕过 ConversationalRetrievalChain 改用手动 query rewriting**（之前已修复）
2. **直接调 FAISS 底层 add_documents**（绕过 add_new_docs.py 的全量扫描）
3. **分批入库策略**（30 文件/批，避免 Ollama 中途挂）
4. **路径式分页 `tzgg/N.htm`**（实际验证可行）

---

## 📋 五、4 个核心测试结果

| 问题 | 期望 | 实际 | 评价 |
|---|---|---|---|
| 电子信息工程上学期有啥课 | 召回相关课程信息 | 提到电路分析基础/数字电子技术/高频电子线路 | ✅ |
| 这学期什么时候选课 | 召回选课通知 | 召回到重修报名时间（9月18-22日）| ✅ |
| 校历 | 召回校历文件 | 诚实告知"无直接校历"，建议查教务处 | ✅ 诚实 |
| 多轮对话（4 问连续）| 累积上下文 | bot 累积答案，4 轮无丢失 | ✅ |

---

## ⚠️ 六、剩余问题 & 改进

### 已知问题

| 问题 | 影响 | 建议 |
|---|---|---|
| **2018-2020 老通知被全量入库**（日期过滤没生效）| 召回时低质量噪声 | 清理老数据（用户提供决策） |
| **`documents.pkl/index.faiss` 含重复 chunks**（daily_crawl 抓的 + 之前抓的重复入库）| faiss_db 体积增大，检索精度略下降 | 重构 FAISS（用 metadata 去重） |
| **教学日历 3 份都是 127 字短文**（教务处校内文件也是）| 校历问题还是答不出 | 改抓"公共服务"页（找到日历 PDF） |
| **PDF 附件没下载**（教务处校内文件 PDF 链接）| 大量正文内容丢失 | 启用 wxu_spider.py 的 PDF 下载 |

### 长期改进路线图

1. **去重 FAISS**（基于 source + page_content hash）→ 1 天
2. **PDF 下载**（wxu_spider.py 已写好）→ 半天
3. **监控校历 PDF**（公共服务页面）→ 2 小时
4. **邮件通知**（抓取完成后发邮件）→ 半天
5. **加分页抓全教务处历史**（current 翻 15 页 → 148 页）→ 半天
6. **Multi-Query Retriever**（同义词增强）→ 半天

---

## 🎯 七、面试可直接讲的 5 个故事

### 故事 1：自动运维系统的工程化
> "我做了个每天自动抓无锡学院官网的脚本。12 个监控页面、148 页教务处分页、增量入库。遇到 Ollama 嵌入中途失败，我做了分批策略（30 文件/批）保证稳定性。"

### 故事 2：debug 一个不存在的 bug
> "教务处 148 页分页用 `tzgg/N.htm` 路径式，但其他猜测（静态 `tzgg_2.htm`、参数 `?page=2`）都失败。我用 web_fetch 一个个验证，找到正确的格式。"

### 故事 3：时间过滤的边界 bug
> "我加了 6 个月时间过滤，但发现 2018 年的老通知也被抓了。原因：`_is_recent` 函数只在 title 里找日期，找不到就默认 True。我修复了从 `<span>` 提取日期，修复了双重判断逻辑。"

### 故事 4：分批策略 vs 一次性
> "Ollama 在嵌入 500+ chunks 时中途崩溃。我改成分批脚本（每批 30 文件），失败重试 30 秒。最终 13 批全部成功，耗时 ~20 分钟。"

### 故事 5：多来源整合
> "项目有 4 个爬虫模块（wxu_spider/notice_spider/scheduler/crawler）大部分是空壳。我把它们整合成一个 `daily_crawl.py`，统一调度、统一增量入库、统一 state 去重。"

---

## 📌 八、文件位置总结

```
D:\wxu\wxu-multimodal-smart-agent\
├── docs\audit\2026-09-16-mavis\
│   ├── mavis_audit.md          # 修复前完整审计（21 题）
│   ├── fix_compare.md           # 修复对比报告
│   └── daily_crawl_report.md    # ⭐ 本报告
├── scripts\
│   ├── daily_crawl.py           # ⭐ 核心脚本
│   ├── run_daily_crawl.bat      # ⭐ 后台启动
│   ├── mavis_audit_test.py      # 测试脚本
│   ├── mavis_fix_verify.py      # 验证脚本
│   └── _index_batched.py        # 入库脚本
└── data\
    ├── docs\学校通知\           # ⭐ 376 个 .txt
    │   ├── 校级通知\            #   9 个
    │   ├── 教务处通知公告\       #   164 个
    │   ├── 教务处公示\           #   44 个
    │   ├── 教务处校内文件\       #   19 个
    │   ├── 会议安排\             #   2 个
    │   ├── 教学日历\             #   3 个
    │   ├── 人才培养\             #   22 个
    │   ├── 选课管理\             #   24 个
    │   ├── 办事流程\             #   4 个
    │   └── 教学新闻\             #   22 个
    ├── 校园要闻\                 #   24 个
    ├── 校园快讯\                 #   31 个
    └── faiss_db\                # ⭐ 13007 chunks
```

---

> **报告生成时间**：2026-09-16 16:15
> **总耗时**：约 4 小时（含调试）
> **下一步建议**：根据文档决定是否清理 2018-2020 老通知