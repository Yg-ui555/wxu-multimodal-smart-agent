"""
用户意图识别（2026-09-22 工程落地版）
基于关键词正则的纯函数意图分类，零 LLM 依赖，可单测。

设计原则：
    - 纯函数：输入 query 字符串，输出 intent 标签
    - 可扩展：新增意图只需在 INTENT_PATTERNS 加 key + regex 列表
    - 高性能：正则 re.search，<1ms 处理一次 query
    - 中英文支持
    - 默认 fallback：general（走 RAG 单轮）
"""

import re
from typing import Tuple, List, Dict

INTENT_PATTERNS: Dict[str, List[str]] = {
    # 课表查询（最多场景）
    "schedule": [
        r"今天上啥", r"今天上什", r"今天有什么课",
        r"明天上啥", r"明天上什", r"明天有课", r"明天有什么课",
        r"下周课", r"下周课表", r"下节课", r"课程表", r"我的课表",
        r"今天.*课", r"明天.*课",
        r"周[一二三四五六日].*课", r"周[一二三四五六日].*第",
        r"第\d+节", r"几节",
        r"schedule", r"today.*class", r"tomorrow.*class",
    ],
    # 奖学金/助学金
    "scholarship": [
        r"奖学金", r"助学金", r"国家奖学金", r"校一等奖",
        r"励志奖学金", r"校友奖学金", r"评定", r"申请条件",
        r"怎么申请", r"如何申请", r"奖学金.*多少",
        r"scholarship", r"financial aid",
    ],
    # 校园生活/知识库
    "kb": [
        r"食堂", r"菜单", r"今天吃啥", r"餐厅",
        r"图书馆", r"开放时间", r"借书", r"自习室",
        r"宿舍", r"热水", r"洗衣机",
        r"超市", r"打印店", r"快递",
        r"教务处", r"怎么去", r"在哪",
        r"周边", r"好玩", r"好吃",
        r"dining", r"library", r"dormitory",
    ],
    # 新闻/公告/活动
    "news": [
        r"新闻", r"通知", r"公告", r"活动",
        r"校历", r"日历", r"放假", r"开学",
        r"赛事", r"比赛", r"晚会",
        r"news", r"announcement", r"event",
    ],
    # OCR/图片处理
    "image_ocr": [
        r"识别", r"提取文字", r"看看图片", r"图里",
        r"ocr", r"扫描",
    ],
    # 课表上传
    "schedule_upload": [
        r"上传课表", r"导入课表", r"课表pdf", r"pdf课表",
        r"截图.*课表", r"课表.*截图",
        r"upload.*schedule",
    ],
}


def detect_intent(query: str) -> Tuple[str, float]:
    """
    检测用户 query 的意图

    Returns:
        (intent, confidence)
        intent: schedule / scholarship / kb / news / image_ocr / schedule_upload / general
        confidence: 0.0-1.0（命中关键词数 / 总关键词数）
    """
    if not query or not query.strip():
        return "general", 0.0

    query_lower = query.strip().lower()
    scores: Dict[str, int] = {}

    for intent, patterns in INTENT_PATTERNS.items():
        hit = 0
        for pat in patterns:
            try:
                if re.search(pat, query_lower):
                    hit += 1
            except re.error:
                # 忽略坏的 regex
                continue
        if hit > 0:
            scores[intent] = hit

    if not scores:
        return "general", 0.0

    # 选最高分的 intent
    best_intent = max(scores, key=scores.get)
    total_keywords = len(INTENT_PATTERNS[best_intent])
    confidence = min(1.0, scores[best_intent] / max(1, total_keywords / 2))
    return best_intent, confidence


def extract_keywords(query: str, top: int = 5) -> List[str]:
    """
    提取 query 中的关键词（用于 fallback RAG 搜索）
    """
    if not query:
        return []
    # 简单中文分词：按 2-gram 切 + 长度 >= 2 的连续中文
    chinese_chars = re.findall(r'[\u4e00-\u9fff]{2,}', query)
    return chinese_chars[:top]


if __name__ == "__main__":
    # 单测：覆盖每种 intent
    test_cases = [
        ("今天上啥?", "schedule"),
        ("明天有课吗", "schedule"),
        ("奖学金怎么申请", "scholarship"),
        ("校一等奖学金多少钱", "scholarship"),
        ("食堂今天有啥菜", "kb"),
        ("图书馆几点开门", "kb"),
        ("这周有什么活动", "news"),
        ("校历给我看看", "news"),
        ("看看这张图片", "image_ocr"),
        ("上传我的课表", "schedule_upload"),
        ("你好", "general"),
        ("", "general"),
    ]
    correct = 0
    for query, expected in test_cases:
        intent, conf = detect_intent(query)
        status = "✓" if intent == expected else "✗"
        if intent == expected:
            correct += 1
        print(f"  {status} '{query}' → {intent} (conf={conf:.2f}) [expect {expected}]")
    print(f"\n{correct}/{len(test_cases)} 通过")