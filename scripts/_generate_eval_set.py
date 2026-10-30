"""
扩测试集到 300 题（2026-10-12）
==================================

# 扩题策略
# 1) 读现有 60 题,统计分类分布
# 2) 用 LLM 按分类生成 240 道新题(分批 50 题/次,避免单次 token 超限)
# 3) 用 RAG 检索每道题的 top-1 文档,从文档中提取 expected_keywords(关键!)
#    原因:LLM 自己写的 expected_keywords 经常不准(它不知道 KB 里有什么)
# 4) 输出到 tests/eval_set_300.json,人工 review 后合并到 tests/eval_set.json
#
# 用法:
#   python scripts/_generate_eval_set.py --count 50
#   python scripts/_generate_eval_set.py --count 240 --output tests/eval_set_300.json
"""
import os
import sys
import io
import json
import time
import argparse
import logging
from pathlib import Path
from collections import Counter

# Force UTF-8 stdout (Windows GBK 问题)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

# 加载 .env (LLM_API_KEY 等) — 2026-10-12 收尾修复
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
log = logging.getLogger('gen_eval')


# ============================================================
# 1) 分类定义 + 目标题数
# ============================================================
# 按 60 题分类 × 5 = 300 题目标
TARGET_PER_CATEGORY = {
    "多模态近似": 50,
    "闲聊": 35,
    "同义词-数字电路": 25,
    "失败兜底": 25,
    "课程": 15,
    "合规-拒绝": 15,
    "政策-奖学金": 15,
    "政策-转专业": 15,
    "校历": 10,
    "奖学金": 10,
    "图书馆": 10,
    "合规-元信息": 10,
    "政策-四六级": 10,
    "办事流程": 5,
    "数学": 5,
    "同义词": 5,
    "选课": 5,
    "政策-创业": 5,
    "政策-心理": 5,
    "数字表格-食堂": 5,
    "数字表格-宿舍": 5,
    "数字表格-图书馆": 5,
    "数字表格-课程": 5,
    "数字表格-学分": 5,
}
# 总数 295,四舍五入 300


# ============================================================
# 2) LLM 生成 prompt
# ============================================================
PROMPT_TEMPLATE = """你是无锡学院校园问答评估专家,需要为 RAG 系统生成测试题。

## 任务
按"{category}"分类生成 {count} 道新题,格式严格如下(JSON 数组):
[
  {{
    "category": "{category}",
    "difficulty": "easy|medium|hard",
    "question": "题目文本",
    "expected_topic": "期望答案涉及的关键主题/概念(供后续验证)",
    "banned_keywords": ["不能出现的违规词,如'不知道'/'无法'"]
  }},
  ...
]

## 出题要求
- 题目要真实自然,学生口吻(口语化 + 各种提问方式)
- 同一分类内不要重复(每题主题/角度不同)
- 难度分布:30% easy / 50% medium / 20% hard
- 中文题目,校园场景
- expected_topic 写"如果 RAG 命中正确答案,答案里应该有什么关键词"
- banned_keywords 只在合规/兜底分类里写 ['不知道','无法','没法'],其他分类可空

## {category} 分类说明
{category_desc}

## 输出
仅返回 JSON 数组(不要解释,不要 markdown 标记)。"""


CATEGORY_DESC = {
    "多模态近似": "模拟'学生拍图后用文字近似提问'的场景(图书馆/食堂/校车/宿舍热水/补考通知/课表/电路板等)",
    "闲聊": "问候/自我介绍/感谢/再见等闲聊,期望 AI 友好回应不调 tool",
    "同义词-数字电路": "数字电路术语同义词(MUX/数据选择器、锁存器/latch、触发器/flip-flop、译码器/decoder 等)",
    "失败兜底": "用户问知识库/常识外的问题(天气预报、其他学校、Python 入门等),期望 AI 礼貌兜底",
    "课程": "电子信息/计算机专业课程相关内容(模电、数电、信号与系统、高数等)",
    "合规-拒绝": "用户问试卷原题/答案/往年真题,期望 AI 礼貌拒绝并引导到教学日历/任课教师",
    "政策-奖学金": "奖学金评定、金额、申请材料、兼得规则等",
    "政策-转专业": "转专业的申请时间、流程、绩点要求、所需材料等",
    "校历": "学期开始/结束时间、寒暑假、教学周次等",
    "奖学金": "(同政策-奖学金,这里是旧分类)",
    "图书馆": "图书馆开放时间、借阅规则、座位预约等",
    "合规-元信息": "问考试时间/范围/形式/占分比等元信息(可答,但不能透露具体考题)",
    "政策-四六级": "英语四六级报名时间/费用/合格线等",
    "办事流程": "各类办事流程(请假、证明开具、户口迁移等)",
    "数学": "基础数学计算(1+1、平方、开方等),期望走常识路径",
    "同义词": "(泛指术语同义,不只是数字电路)",
    "选课": "选课规则、体育选课、跨专业选课等",
    "政策-创业": "大学生创业扶持政策、孵化基地、创业大赛等",
    "政策-心理": "学校心理咨询预约、心理普查、心理健康教育等",
    "数字表格-食堂": "食堂菜单、价格、开放时间等数字类",
    "数字表格-宿舍": "宿舍热水供应时间、门禁时间、报修电话等",
    "数字表格-图书馆": "图书馆借书数量、借期、罚款金额等",
    "数字表格-课程": "课时、学分、教室分布等数字类",
    "数字表格-学分": "总学分要求、各类学分分布等",
}


# ============================================================
# 3) LLM 调用
# ============================================================
def call_llm(category: str, count: int) -> list:
    """调 LLM 生成一道分类的 N 道题"""
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        temperature=0.9,  # 高温度,题目多样性
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        timeout=120.0,
    )

    desc = CATEGORY_DESC.get(category, "")
    prompt = PROMPT_TEMPLATE.format(category=category, count=count, category_desc=desc)
    try:
        resp = llm.invoke([
            SystemMessage(content="你是校园评估专家,严格返回 JSON 数组。"),
            HumanMessage(content=prompt),
        ])
        content = (getattr(resp, "content", None) or str(resp)).strip()
        # 抠 JSON 数组
        import re
        m = re.search(r"\[.*\]", content, re.S)
        if m:
            arr = json.loads(m.group(0))
            return arr
        log.warning(f"[{category}] LLM 输出无 JSON 数组, 截取前 200: {content[:200]}")
        return []
    except Exception as e:
        log.warning(f"[{category}] LLM 调用失败: {e!r}")
        return []


# ============================================================
# 4) 用 RAG 验证 + 提取 expected_keywords
# ============================================================
# 通用停用词(2026-10-12 收尾修复):过滤掉判别力为 0 的高频通用词
_EXTENDED_STOP = set("""
的 了 一 是 不 在 有 和 无 很 也 就 要 与 等 或 之 而 于 上 下 中 把 被 让 给
我们 你们 他们 这个 那个 什么 怎么 怎样 如何 为什么 哪些 哪些
通知 安排 服务 学院 学校 大学 学院 老师 学生 工作 情况 信息 内容
问题 一下 一些 一点 请问 谢谢 麻烦 不好意思 抱歉
表示 提示 提供 通过 进行 以及 包括 等等
""".split())


def extract_keywords_from_retrieval(question: str, retriever, top_n: int = 4) -> list:
    """跑 RAG 检索,从 top-N 文档里抽有判别力的关键词

    改进点(2026-10-12):
    - 扩展停用词表(过滤"无锡/学院/通知/服务"等判别力为 0 的通用词)
    - 优先抽取"和 question 显著重合"的词(题目专属概念)
    - 保留 TF-IDF 思路:对每篇文档都高频的词不要
    """
    try:
        docs = retriever.retrieve(question)
        if not docs:
            return []

        # 2026-10-12 新策略:用 RAG 召回的 top-1 文档,提取"和 question 有重合"的名词
        # 简单 TF:在 top-1 文档里出现 2+ 次,长度 2-6,不在停用词表
        target = docs[0].page_content
        import jieba
        import re as _re
        # 先从 question 抽所有可能的名词
        q_words = set()
        for w in jieba.cut(question):
            w = w.strip()
            if len(w) >= 2 and w not in _EXTENDED_STOP and not _re.match(r'^[\d\W]+$', w):
                q_words.add(w)

        # 在 target 里找高频非停用词
        from collections import Counter
        words = []
        for w in jieba.cut(target):
            w = w.strip()
            if len(w) < 2 or len(w) > 6:
                continue
            if w in _EXTENDED_STOP:
                continue
            if _re.match(r'^[\d\W]+$', w):
                continue
            words.append(w)
        counter = Counter(words)

        # 优先选"和 question 有重合"的词
        matched = [w for w, _ in counter.most_common(20) if w in q_words][:3]
        # 不足的补上高频非停用词
        if len(matched) < 3:
            for w, _ in counter.most_common(20):
                if w not in matched and w not in _EXTENDED_STOP:
                    matched.append(w)
                if len(matched) >= 3:
                    break
        return matched[:3]
    except Exception as e:
        log.warning(f"RAG retrieval 失败: {e!r}")
        return []


# ============================================================
# 5) 主流程
# ============================================================
def main(count: int, output: str, with_rag_check: bool = True):
    """生成 N 道新题,分批调 LLM"""
    # 加载 retriever(RAG 验证用)
    retriever = None
    if with_rag_check:
        try:
            from src.rag.vector_store import VectorStoreManager
            from src.rag.retriever import create_retriever
            mgr = VectorStoreManager(persist_directory="data/faiss_db_bge")
            mgr.load_vectorstore()
            retriever = create_retriever(
                mgr.vectorstore,
                documents=mgr._documents,
                top_k=4,
                use_bm25=True,
                use_reranker=False,
                vector_top_k=8,
                bm25_top_k=8,
            )
            print(f"RAG retriever 就绪, {len(mgr._documents)} 文档")
        except Exception as e:
            log.warning(f"RAG retriever 加载失败,跳过关键词验证: {e!r}")
            with_rag_check = False

    all_new_cases = []
    next_id = 100  # 从 100 开始,避开旧 ID

    for cat, target in TARGET_PER_CATEGORY.items():
        # 按比例生成(count 限制时)
        if count and len(all_new_cases) >= count:
            break
        remaining = count - len(all_new_cases) if count else target
        actual = min(target, remaining)
        if actual <= 0:
            continue

        print(f"\n[{cat}] 生成 {actual} 题...")
        t0 = time.time()
        items = call_llm(cat, actual)
        print(f"  LLM 返回 {len(items)} 题, 耗时 {time.time()-t0:.1f}s")

        # 转换格式 + 补 ID + RAG 验证关键词
        for item in items:
            new_case = {
                "id": next_id,
                "category": cat,
                "difficulty": item.get("difficulty", "medium"),
                "question": item.get("question", "").strip(),
                "expected_keywords": [],
                "banned_keywords": item.get("banned_keywords", []),
            }
            if not new_case["question"]:
                continue
            # RAG 验证:从检索结果抽关键词
            if with_rag_check and retriever:
                kw = extract_keywords_from_retrieval(new_case["question"], retriever)
                if kw:
                    new_case["expected_keywords"] = kw[:3]  # 取 top-3
                else:
                    # RAG 召不到 → 这题可能是"无答案"或 KB 缺口,标注空关键词
                    log.warning(f"  题 {next_id} RAG 召不到: {new_case['question'][:30]}")
            all_new_cases.append(new_case)
            next_id += 1

        # 速率控制
        time.sleep(0.5)

    # 输出
    out_path = PROJECT_ROOT / output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_new_cases, f, ensure_ascii=False, indent=2)
    print(f"\n[done] {len(all_new_cases)} 题写入 {out_path}")
    print(f"  分类分布: {dict(Counter(c['category'] for c in all_new_cases))}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=50, help="生成几道(0=按目标全部)")
    ap.add_argument("--output", default="tests/eval_set_240.json", help="输出文件")
    ap.add_argument("--no-rag-check", action="store_true", help="跳过 RAG 验证")
    args = ap.parse_args()
    main(args.count, args.output, with_rag_check=not args.no_rag_check)
