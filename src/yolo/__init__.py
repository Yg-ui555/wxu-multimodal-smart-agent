"""YOLO 模块:物体检测(YOLOv8 / YOLO-World 开放词汇)"""
from .inference import (
    detect_objects,
    detect_objects_world,
    format_detections_md,
    DEFAULT_WORLD_CLASSES,
)

__all__ = [
    "detect_objects",
    "detect_objects_world",
    "format_detections_md",
    "DEFAULT_WORLD_CLASSES",
]