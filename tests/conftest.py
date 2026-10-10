# -*- coding: utf-8 -*-
"""
pytest 全局配置（2026-10-09 加）

作用：把项目根目录加到 sys.path，
让 `from src.xxx import ...` 在 pytest 里能直接 import。

不加这个的话，pytest 从 tests/ 目录跑会找不到 src 包。
"""
import sys
import pathlib

# __file__ = tests/conftest.py，parent.parent = 项目根
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
