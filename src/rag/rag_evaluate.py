# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. 自研轻量评估指标（不依赖 ragas，Python 3.8 可运行）
# 2. recall@k：检索召回率（top-k 是否命中期望文档/关键词）
# 3. 引用命中率：LLM 回答中的引用编号是否对应检索到的文档
# 4. 幻觉率：回答内容与检索文档的关键词重叠率（越低越可能幻觉）
# 5. before/after 对比：驱动检索与 Prompt 迭代
#
# 💡 面试杀手锏：
#    - "如何评估 RAG 质量？" → 自研三指标：recall@k / 引用命中率 / 幻觉率
#    - "为什么不用 ragas？" → Python 3.8 装不上，自研更轻量可控
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

"""
RAG 评估模块：自研 recall@k / 引用命中率 / 幻觉率 三指标
不依赖 ragas，纯 Python 实现
"""

import os
import re
import json
import logging
from typing import List, Dict, Optional, Tuple

from langchain_core.documents import Document

log = logging.getLogger("rag_evaluate")


# ============================================================
# 默认测试集（21 题，覆盖政策/课程/考试/边界/压力）
# ============================================================

DEFAULT_TESTSET = [
    # 政策类
    {"question": "四六级考试报名通知", "expected_keywords": ["四六级", "CET", "报名"], "category": "政策"},
    {"question": "奖学金评定办法", "expected_keywords": ["奖学金", "评定"], "category": "政策"},
    {"question": "转专业流程", "expected_keywords": ["转专业"], "category": "政策"},
    {"question": "校历 学期安排", "expected_keywords": ["校历", "学期"], "category": "政策"},
    {"question": "锡院教〔2025〕71号", "expected_keywords": ["锡院教", "71"], "category": "文号"},
    # 课程类
    {"question": "大一上学期有哪些课程", "expected_keywords": ["大一", "课程", "学期"], "category": "课程"},
    {"question": "电子信息工程专业学分", "expected_keywords": ["电子信息", "学分"], "category": "课程"},
    {"question": "门电路相关知识", "expected_keywords": ["门电路", "组合逻辑", "逻辑门", "数字电路"], "category": "课程"},
    {"question": "微积分基本公式", "expected_keywords": ["微积分", "导数", "积分"], "category": "课程"},
    {"question": "信息论题目", "expected_keywords": ["信息论", "熵"], "category": "课程"},
    # 考试类
    {"question": "期末考试时间", "expected_keywords": ["考试", "期末"], "category": "考试"},
    {"question": "补考安排", "expected_keywords": ["补考"], "category": "考试"},
    # 生活类
    {"question": "图书馆开放时间", "expected_keywords": ["图书馆", "开放"], "category": "生活"},
    {"question": "校园跑要求", "expected_keywords": ["校园跑", "阳光跑", "跑步"], "category": "生活"},
    {"question": "食堂营业时间", "expected_keywords": ["食堂", "营业"], "category": "生活"},
    # 边界/压力
    {"question": "1+1等于几", "expected_keywords": [], "category": "常识"},
    {"question": "你好", "expected_keywords": [], "category": "闲聊"},
    {"question": "Python 怎么学", "expected_keywords": [], "category": "开放"},
    {"question": "xxxx不存在的问题xyz", "expected_keywords": [], "category": "兜底"},
    {"question": "图书馆里面能充电吗", "expected_keywords": ["图书馆", "充电", "插座"], "category": "多轮"},
    {"question": "奖学金有多少金额", "expected_keywords": ["奖学金", "金额", "元"], "category": "政策"},
]


# ============================================================
# 指标 1：recall@k
# ============================================================

def recall_at_k(
    retrieved_docs: List[Document],
    expected_keywords: List[str],
    expected_sources: Optional[List[str]] = None,
) -> bool:
    """
    判断检索结果是否命中期望（关键词或来源）
    Args:
        retrieved_docs: 检索到的文档列表
        expected_keywords: 期望命中的关键词列表（任一命中即可）
        expected_sources: 期望命中的来源路径列表（可选）
    Returns:
        True=命中, False=未命中
    """
    if not retrieved_docs:
        return False

    # 合并所有检索到的文本
    all_text = "\n".join([doc.page_content for doc in retrieved_docs])

    # 关键词命中检查
    if expected_keywords:
        for kw in expected_keywords:
            if kw in all_text:
                return True

    # 来源命中检查
    if expected_sources:
        for doc in retrieved_docs:
            src = doc.metadata.get("source", "")
            for exp_src in expected_sources:
                if exp_src in src:
                    return True

    return False


# ============================================================
# 指标 2：引用命中率
# ============================================================

def citation_hit_rate(
    answer: str,
    retrieved_docs: List[Document],
) -> float:
    """
    引用命中率：回答中提到的来源是否在检索结果中
    简单实现：检查回答中的 [n] 引用编号是否在 1~len(docs) 范围内
    以及回答中的文件名是否在检索来源中
    Args:
        answer: LLM 回答
        retrieved_docs: 检索到的文档
    Returns:
        命中率 0.0~1.0
    """
    if not answer or not retrieved_docs:
        return 0.0

    # 提取回答中的引用编号 [1], [2] 等
    cited_nums = set()
    for m in re.finditer(r'\[(\d+)\]', answer):
        cited_nums.add(int(m.group(1)))

    # 有效编号范围
    valid_nums = set(range(1, len(retrieved_docs) + 1))

    if not cited_nums:
        # 没有引用编号，检查是否有提到来源文件名
        sources_text = set()
        for doc in retrieved_docs:
            src = doc.metadata.get("source", "")
            if src:
                sources_text.add(os.path.basename(src))
        hit = sum(1 for s in sources_text if s in answer)
        return hit / max(len(sources_text), 1)

    # 引用编号命中率
    hits = len(cited_nums & valid_nums)
    return hits / len(cited_nums)


# ============================================================
# 指标 3：幻觉率
# ============================================================

def hallucination_rate(
    answer: str,
    retrieved_docs: List[Document],
) -> float:
    """
    幻觉率（近似）：回答中不在检索文档中的名词性关键词比例
    实现思路：提取回答中的关键词，看有多少不在检索文档中
    比例越高，越可能有幻觉
    Args:
        answer: LLM 回答
        retrieved_docs: 检索到的文档
    Returns:
        幻觉率 0.0~1.0（越低越好）
    """
    if not answer:
        return 0.0
    if not retrieved_docs:
        return 1.0  # 没有检索文档但有回答，大概率幻觉

    # 合并检索文档文本
    context = "\n".join([doc.page_content for doc in retrieved_docs])

    # 提取回答中的中文词（2-4 字）和英文词
    answer_words = set(re.findall(r'[\u4e00-\u9fa5]{2,6}|[a-zA-Z]{2,}', answer))
    context_words = set(re.findall(r'[\u4e00-\u9fa5]{2,6}|[a-zA-Z]{2,}', context))

    if not answer_words:
        return 0.0

    # 不在上下文中的词的比例
    unsupported = answer_words - context_words
    # 过滤掉常见停用词（简单列表）
    stopwords = {"的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
                 "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有",
                 "看", "好", "自己", "这", "那", "这个", "那个", "可以", "可能", "应该",
                 "如果", "但是", "因为", "所以", "或者", "以及", "等", "等等", "请", "谢谢"}
    unsupported = unsupported - stopwords
    answer_words = answer_words - stopwords

    if not answer_words:
        return 0.0

    return len(unsupported) / len(answer_words)


# ============================================================
# 批量评估
# ============================================================

def evaluate_retrieval(
    retriever,
    testset: Optional[List[dict]] = None,
    k: int = 4,
) -> Dict:
    """
    评估检索质量（recall@k）
    Args:
        retriever: 检索器实例
        testset: 测试集，None 则用默认 21 题
        k: top-k
    Returns:
        {"recall@k": float, "details": [...]}
    """
    if testset is None:
        testset = DEFAULT_TESTSET

    details = []
    hit = 0
    for item in testset:
        question = item["question"]
        expected_keywords = item.get("expected_keywords", [])
        expected_sources = item.get("expected_sources")

        try:
            docs = retriever.retrieve(question)
        except Exception as e:
            log.warning(f"检索失败: {question} - {e}")
            docs = []

        is_hit = recall_at_k(docs, expected_keywords, expected_sources)
        if is_hit:
            hit += 1

        details.append({
            "question": question,
            "category": item.get("category", ""),
            "hit": is_hit,
            "retrieved_count": len(docs),
            "top_sources": [d.metadata.get("source", "")[:40] for d in docs[:2]],
        })

    recall = hit / len(testset) if testset else 0.0
    return {"recall@k": recall, "total": len(testset), "hit": hit, "details": details}


def evaluate_answer(
    answer: str,
    retrieved_docs: List[Document],
) -> Dict:
    """
    评估单条回答质量（引用命中率 + 幻觉率）
    """
    return {
        "citation_hit_rate": citation_hit_rate(answer, retrieved_docs),
        "hallucination_rate": hallucination_rate(answer, retrieved_docs),
    }


def evaluate_full(
    retriever,
    qa_system,
    testset: Optional[List[dict]] = None,
    k: int = 4,
) -> Dict:
    """
    完整评估：检索 + 回答
    Args:
        retriever: 检索器
        qa_system: LLMQASystem 实例
        testset: 测试集
        k: top-k
    Returns:
        汇总指标
    """
    if testset is None:
        testset = DEFAULT_TESTSET

    retrieval_result = evaluate_retrieval(retriever, testset, k)

    # 回答质量评估
    citation_scores = []
    hallucination_scores = []
    answer_details = []

    for item in testset:
        question = item["question"]
        try:
            docs = retriever.retrieve(question)
            result = qa_system.answer(question)
            answer = result.get("answer", "")
            metrics = evaluate_answer(answer, docs)
            citation_scores.append(metrics["citation_hit_rate"])
            hallucination_scores.append(metrics["hallucination_rate"])
            answer_details.append({
                "question": question,
                "citation_hit_rate": metrics["citation_hit_rate"],
                "hallucination_rate": metrics["hallucination_rate"],
                "answer_preview": answer[:100],
            })
        except Exception as e:
            log.warning(f"评估失败: {question} - {e}")

    avg_citation = sum(citation_scores) / len(citation_scores) if citation_scores else 0.0
    avg_hallucination = sum(hallucination_scores) / len(hallucination_scores) if hallucination_scores else 0.0

    return {
        "recall@k": retrieval_result["recall@k"],
        "avg_citation_hit_rate": avg_citation,
        "avg_hallucination_rate": avg_hallucination,
        "retrieval_details": retrieval_result["details"],
        "answer_details": answer_details,
    }


def print_report(result: Dict):
    """打印评估报告"""
    print("\n" + "=" * 60)
    print("📊 RAG 评估报告")
    print("=" * 60)
    print(f"  recall@{4}            : {result.get('recall@k', 0):.2%}")
    print(f"  平均引用命中率        : {result.get('avg_citation_hit_rate', 0):.2%}")
    print(f"  平均幻觉率            : {result.get('avg_hallucination_rate', 0):.2%}")
    print("-" * 60)

    details = result.get("retrieval_details", [])
    if details:
        # 按类别统计
        by_category = {}
        for d in details:
            cat = d.get("category", "未知")
            if cat not in by_category:
                by_category[cat] = {"total": 0, "hit": 0}
            by_category[cat]["total"] += 1
            if d["hit"]:
                by_category[cat]["hit"] += 1

        print("\n📂 分类召回率:")
        for cat, stats in by_category.items():
            rate = stats["hit"] / stats["total"] if stats["total"] else 0
            print(f"  {cat:<8}: {stats['hit']}/{stats['total']} = {rate:.0%}")

        print("\n❌ 未命中问题:")
        for d in details:
            if not d["hit"]:
                print(f"  - [{d['category']}] {d['question']}")
                if d["top_sources"]:
                    print(f"      召回: {d['top_sources']}")
    print("=" * 60)


def save_report(result: Dict, output_path: str = "logs/rag_evaluate_report.json"):
    """保存评估报告到 JSON"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    # 只保存可序列化的部分
    serializable = {
        "recall@k": result.get("recall@k"),
        "avg_citation_hit_rate": result.get("avg_citation_hit_rate"),
        "avg_hallucination_rate": result.get("avg_hallucination_rate"),
        "retrieval_details": result.get("retrieval_details", []),
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"报告已保存: {output_path}")


if __name__ == "__main__":
    # 用法示例
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    from src.rag.vector_store import VectorStoreManager
    from src.rag.retriever import create_retriever

    print("加载向量库...")
    manager = VectorStoreManager(persist_directory="data/faiss_db_bge")
    vs = manager.load_vectorstore()

    print("创建检索器...")
    retriever = create_retriever(vs, documents=manager._documents, top_k=4)

    print("\n运行检索评估（21 题）...")
    result = evaluate_retrieval(retriever)
    print_report(result)
    save_report(result)
