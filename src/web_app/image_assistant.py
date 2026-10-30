# ============================================================
# 看图助手：把"识物(YOLO-World) + 识字(OCR)"包装成学生视角
# 学生在对话里发图片时，后台自动完成两件事：
#   1. 看看图里有什么（实验器材 / 课本 / 食堂菜 / 校园景物）
#   2. 读读图里写了什么字（通知 / 作业题 / 板书）
# 识别结果整理成大白话，再交给问答大脑结合学校资料回答。
# UI 层只调 look_at_image()，不直接接触任何模型参数。
# ============================================================

import os
import time
import logging
from typing import List, Tuple, Optional

from PIL import Image

log = logging.getLogger("image_assistant")

# 标注图（在图上画出框框的结果图）保存位置
_ANNOTATED_DIR = os.path.join("data", "uploaded_images", "annotated")

# 学生日常会拍到的东西：模型认识的英文名 -> 学生看的中文名
_THING_NAMES = {
    # —— 电子实验 / 电路 ——
    "circuit board": "电路板",
    "breadboard": "面包板",
    "resistor": "电阻",
    "capacitor": "电容",
    "IC chip": "芯片",
    "LED": "LED 灯",
    "wire": "导线",
    "oscilloscope": "示波器",
    "multimeter": "万用表",
    "soldering iron": "电烙铁",
    "transistor": "三极管",
    "diode": "二极管",
    "inductor": "电感",
    # —— 学习用品 ——
    "textbook": "课本",
    "notebook": "笔记本",
    "pen": "笔",
    "ruler": "尺子",
    "calculator": "计算器",
    "eraser": "橡皮",
    "backpack": "书包",
    "laptop computer": "笔记本电脑",
    # —— 食堂 / 吃喝 ——
    "rice": "米饭",
    "noodles": "面条",
    "vegetables": "蔬菜",
    "soup bowl": "汤",
    "chopsticks": "筷子",
    "spoon": "勺子",
    "tea": "饮料",
    "water bottle": "水杯",
    # —— 校园 ——
    "library building": "图书馆",
    "classroom": "教室",
    "computer screen": "电脑屏幕",
    "whiteboard": "白板",
    "projector": "投影仪",
    "chair": "椅子",
    "desk": "课桌",
    # —— 常见的人和物 ——
    "person": "人",
    "bicycle": "自行车",
    "car": "汽车",
    "motorcycle": "电动车",
    "bus": "公交车",
    "cell phone": "手机",
    "book": "书",
    "clock": "钟表",
    # —— 动植物 ——
    "tree": "树",
    "flower": "花",
    "cat": "猫",
    "dog": "狗",
    "bird": "鸟",
}


def _cn(label: str) -> str:
    """模型英文类别名 -> 中文名；没收录就原样返回"""
    return _THING_NAMES.get(label.strip(), label.strip())


def _save_annotated(annotated: Image.Image) -> Optional[str]:
    """把画好框的结果图保存成文件，返回路径（失败不影响主流程）"""
    try:
        os.makedirs(_ANNOTATED_DIR, exist_ok=True)
        fname = f"look_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.jpg"
        path = os.path.join(_ANNOTATED_DIR, fname)
        annotated.convert("RGB").save(path, quality=88)
        return path
    except Exception as e:
        log.warning("标注图保存失败: %r", e)
        return None


def look_at_image(image_path: str, progress=None) -> dict:
    """
    看一张图：认东西 + 读文字。

    Returns:
        {
          "objects": [("电阻", 2), ("面包板", 1), ...],  # 中文名 + 个数，按出现次数排序
          "ocr_text": "图片里读到的全部文字",
          "annotated_path": "画好框的结果图路径 或 None",
          "object_count": 6,
        }
    任何一个模型失败都不影响另一个，绝不因识别失败让对话崩掉。
    """
    def _tick(ratio, desc):
        if progress is not None:
            try:
                progress(ratio, desc=desc)
            except Exception as e:
                log.warning("进度回调失败（忽略）: %r", e)

    objects: List[Tuple[str, int]] = []
    annotated_path: Optional[str] = None
    ocr_text = ""

    # ---------- 1. 认东西 ----------
    _tick(0.15, "正在看图里有什么…")
    try:
        from src.yolo import detect_objects_world
        result = detect_objects_world(image_path)
        counts = {}
        for det in result.get("detections", []):
            label = det.get("label", "")
            counts[label] = counts.get(label, 0) + 1
        # 数量多的排前面，方便学生一眼看到主要物体
        objects = sorted(
            [(_cn(k), v) for k, v in counts.items()],
            key=lambda x: (-x[1], x[0]),
        )
        annotated = result.get("annotated")
        if annotated is not None:
            annotated_path = _save_annotated(annotated)
    except Exception as e:
        log.warning("认物失败（不影响识字）: %r", e)

    # ---------- 2. 读文字 ----------
    _tick(0.55, "正在读图片里的文字…")
    try:
        from src.ocr import extract_text
        ocr_result = extract_text(image_path, conf_threshold=0.5)
        ocr_text = (ocr_result.get("text") or "").strip()
    except Exception as e:
        log.warning("识字失败（不影响认物）: %r", e)

    _tick(0.9, "马上就好…")

    return {
        "objects": objects,
        "ocr_text": ocr_text,
        "annotated_path": annotated_path,
        "object_count": sum(n for _, n in objects),
    }


def describe_for_student(info: dict) -> str:
    """
    给"拍照工具"页用的纯文字说明（学生大白话，没有任何技术词）。
    """
    parts = []
    objects = info.get("objects") or []
    ocr_text = (info.get("ocr_text") or "").strip()

    if objects:
        names = "、".join(f"{name} ×{n}" if n > 1 else name for name, n in objects)
        parts.append(f"我在图里看到了：**{names}**。")
    else:
        parts.append("这张图里没有认出特别明确的物品，可能是拍得比较远、比较暗，或者东西不太常见。")

    if ocr_text:
        parts.append(f"\n\n图片里写着这些文字：\n\n> {ocr_text}")
    else:
        parts.append("\n\n图片里没有读到成片的文字。")

    parts.append("\n\n如果想让我详细讲讲（这是什么、怎么用、通知说了啥），直接到「首页」对话里把图发给我再问一句就行。")
    return "".join(parts)


def build_context_for_chat(info: dict, question: str = "") -> Tuple[str, str]:
    """
    把看图结果整理成给问答大脑的上下文。

    Returns:
        (给大脑看的增强问题, 对话历史里展示给学生的问题摘要)
    增强问题里包含识别出的物品和文字，问答大脑结合学校资料后用大白话回答。
    """
    objects = info.get("objects") or []
    ocr_text = (info.get("ocr_text") or "").strip()
    q = (question or "").strip()

    lines = ["学生发来一张图片" + (f"，并问到：{q}" if q else "，想让你帮忙看看这是什么、讲一讲。")]

    if objects:
        names = "、".join(f"{name}×{n}" if n > 1 else name for name, n in objects)
        lines.append(f"图片中识别到的物品：{names}。")
    if ocr_text:
        lines.append("图片中读到的文字如下：\n\"\"\"\n" + ocr_text[:1500] + "\n\"\"\"")

    lines.append(
        "请像热心的学长学姐一样，用口语化的中文回答："
        "如果图片是通知、题目或实验器材，优先根据上面的文字和物品来解释；"
        "涉及学校规定、时间安排的，结合参考资料核实，没有依据就说明并建议以官方通知为准；"
        "不要罗列技术参数，3-5 句话说清楚。"
    )

    display_q = f"📷 {q}" if q else "📷 发了一张图片"
    return "\n".join(lines), display_q
