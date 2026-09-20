# ============================================================
# 📦 本文件重点掌握能力
# ============================================================
# 1. YOLOv8 推理调用(ultralytics 库)
# 2. **YOLO-World 开放词汇检测**(无需训练,识别任意类)
# 3. 模型自动下载(首次跑自动下 yolov8n.pt / yolov8s-world.pt)
# 4. 检测结果解析(类别 + 置信度 + bbox)
# 5. 带框标注图生成(result.plot())
# 6. 自定义模型加载(本地训练好的 .pt 文件)
#
# 💡 进阶方向(写到简历上):
#    - 训练自定义电路元器件检测模型(数据集标注 + 训练脚本)
#    - 部署到 GPU 加速推理
#    - 模型蒸馏/量化(适配移动端)
# ============================================================

"""
YOLOv8 / YOLO-World 物体检测推理(2026-10-18 升级)

两个模式:
1. 普通 YOLOv8 —— 80 类 COCO 固定(预训练 yolov8n.pt)
2. YOLO-World —— 开放词汇,你说识别啥就识别啥(yolov8s-world.pt)

为什么用 YOLO-World:
- 校园场景(电路板 / 元器件 / 食堂菜 / 教材)都不在 COCO 80 类里
- 不用标注数据集 + 训练,直接用自然语言定义要识别的类
- 切类别只需 `model.set_classes([...])`
"""

from pathlib import Path
from typing import List, Dict, Union, Optional
import logging

import numpy as np
from PIL import Image

try:
    from ultralytics import YOLO, YOLOWorld
except ImportError as e:
    raise ImportError(
        "请先装 ultralytics: pip install ultralytics"
    ) from e

log = logging.getLogger("yolo.inference")


# weights/ 目录(与本文件同级)
WEIGHTS_DIR = Path(__file__).parent / "weights"
WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)


# 默认 YOLO-World 类别(校园场景)
# 这些都是校园学习生活里常见但 COCO 里没有的
DEFAULT_WORLD_CLASSES = [
    # 电子/电路
    "circuit board", "breadboard", "resistor", "capacitor", "IC chip",
    "LED", "wire", "oscilloscope", "multimeter", "soldering iron",
    "transistor", "diode", "inductor",
    # 学习
    "textbook", "notebook", "pen", "ruler", "calculator", "eraser",
    "backpack", "laptop computer",
    # 食堂/生活
    "rice", "noodles", "vegetables", "soup bowl", "chopsticks", "spoon",
    "tea", "water bottle",
    # 校园设施
    "library building", "classroom", "computer screen", "whiteboard",
    "projector", "chair", "desk",
    # 校园植物/动物
    "tree", "flower", "cat", "dog", "bird",
]


def get_default_model() -> str:
    """获取默认普通 YOLO 模型(yolov8n.pt)"""
    default = WEIGHTS_DIR / "yolov8n.pt"
    if default.exists():
        return str(default)
    log.info("本地没有 yolov8n.pt,从 ultralytics 下载...")
    model = YOLO("yolov8n.pt")
    cache_path = Path(model.ckpt_path)
    if cache_path.exists() and cache_path != default:
        import shutil
        try:
            shutil.copy2(cache_path, default)
            log.info(f"模型已复制到 {default}")
        except Exception as e:
            log.warning(f"复制模型失败(不影响使用): {e}")
    return str(default)


def get_world_model() -> str:
    """获取 YOLO-World 模型(yolov8s-world.pt)—— 开放词汇版"""
    default = WEIGHTS_DIR / "yolov8s-world.pt"
    if default.exists():
        return str(default)
    log.info("本地没有 yolov8s-world.pt,从 ultralytics 下载...")
    model = YOLOWorld("yolov8s-world.pt")
    cache_path = Path(model.ckpt_path)
    if cache_path.exists() and cache_path != default:
        import shutil
        try:
            shutil.copy2(cache_path, default)
            log.info(f"YOLO-World 模型已复制到 {default}")
        except Exception as e:
            log.warning(f"复制模型失败(不影响使用): {e}")
    return str(default)


def detect_objects(
    image: Union[str, Path, np.ndarray, Image.Image],
    model_path: Optional[str] = None,
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
) -> Dict:
    """
    普通 YOLOv8 物体检测(80 类 COCO 固定)
    """
    if model_path is None:
        model_path = get_default_model()

    model = YOLO(model_path)
    results = model(image, conf=conf_threshold, iou=iou_threshold, verbose=False)

    if not results:
        raise RuntimeError("YOLO 推理返回空结果")

    result = results[0]
    detections: List[Dict] = []
    for box in result.boxes:
        cls_id = int(box.cls[0])
        label = model.names.get(cls_id, str(cls_id))
        conf = float(box.conf[0])
        bbox = box.xyxy[0].tolist()
        detections.append({
            "label": label,
            "conf": round(conf, 3),
            "bbox": [round(v, 4) for v in bbox],
        })

    plot_bgr = result.plot()
    annotated = Image.fromarray(plot_bgr[..., ::-1])

    return {
        "annotated": annotated,
        "detections": detections,
        "count": len(detections),
        "model": model_path,
        "mode": "yolov8",
        "classes_used": list(model.names.values()),
    }


def detect_objects_world(
    image: Union[str, Path, np.ndarray, Image.Image],
    classes: Optional[List[str]] = None,
    model_path: Optional[str] = None,
    conf_threshold: float = 0.15,
    iou_threshold: float = 0.45,
) -> Dict:
    """
    YOLO-World 开放词汇检测(2026-10-18 升级)

    Args:
        image: 图像路径 / numpy / PIL
        classes: 自定义类别列表;None 用 DEFAULT_WORLD_CLASSES
        model_path: 模型路径;None 用 yolov8s-world.pt
        conf_threshold: 置信度阈值(默认 0.15,因为世界模型略低)

    Returns:
        dict: 同 detect_objects,多了 "requested_classes"
    """
    if model_path is None:
        model_path = get_world_model()
    if classes is None:
        classes = DEFAULT_WORLD_CLASSES

    model = YOLOWorld(model_path)
    model.set_classes(classes)  # 设置本次要识别的类别
    results = model.predict(image, conf=conf_threshold, iou=iou_threshold, verbose=False)

    if not results:
        raise RuntimeError("YOLO-World 推理返回空结果")

    result = results[0]
    detections: List[Dict] = []
    for box in result.boxes:
        cls_id = int(box.cls[0])
        label = model.names.get(cls_id, str(cls_id))
        conf = float(box.conf[0])
        bbox = box.xyxy[0].tolist()
        detections.append({
            "label": label,
            "conf": round(conf, 3),
            "bbox": [round(v, 4) for v in bbox],
        })

    plot_bgr = result.plot()
    annotated = Image.fromarray(plot_bgr[..., ::-1])

    return {
        "annotated": annotated,
        "detections": detections,
        "count": len(detections),
        "model": model_path,
        "mode": "yolo-world",
        "requested_classes": classes,
        "classes_used": list(model.names.values()),
    }


def format_detections_md(detections: List[Dict]) -> str:
    """检测结果格式化成 Markdown(给 Gradio Markdown 组件)"""
    if not detections:
        return "_未检测到任何对象_\n\n(可能:置信度阈值过高 / 图里没有目标类 / 换个角度试试)"

    lines = [f"**检测到 {len(detections)} 个对象:**\n"]
    by_label: Dict[str, List[float]] = {}
    for d in detections:
        by_label.setdefault(d["label"], []).append(d["conf"])

    for label, confs in sorted(by_label.items(), key=lambda x: -max(x[1])):
        avg = sum(confs) / len(confs)
        max_c = max(confs)
        lines.append(
            f"- **{label}** × {len(confs)} "
            f"(最高 {max_c:.2f} / 均值 {avg:.2f})"
        )
    return "\n".join(lines)


# 模块自测
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法:python src/yolo/inference.py <image_path> [mode]")
        print("  mode=yolov8    默认 80 类 COCO")
        print("  mode=world     开放词汇(yolov8s-world.pt)")
        sys.exit(1)

    img_path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "world"

    print(f"=== YOLO {mode} 检测 ===")
    if mode == "world":
        result = detect_objects_world(img_path)
    else:
        result = detect_objects(img_path)

    print(f"\n检测到 {result['count']} 个对象:")
    print(format_detections_md(result["detections"]))
    print(f"\n模型: {result['model']}")
    if "requested_classes" in result:
        print(f"请求类别数: {len(result['requested_classes'])}")
    print(f"已生成标注图: {result['annotated'].size}")
    out = Path(__file__).parent.parent.parent / "data" / "yolo_test_out.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    result["annotated"].save(out)
    print(f"已保存: {out}")