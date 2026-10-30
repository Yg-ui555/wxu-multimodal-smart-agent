# ============================================================
# WXU Multimodal Smart Agent —— 生产镜像
# ============================================================
# 镜像偏大是预期内的：requirements 里含 torch/ultralytics（YOLO-World 识图）
# 与 paddleocr（OCR），这是「多模态」能力的代价 —— 纯问答链路其实用不到。
#   · 只要问答不要视觉：注释掉下面两行 apt/pip，镜像能从 4GB+ 降到 ~1GB
#       pip install -r requirements.txt 里的 ultralytics / paddleocr / paddlepaddle / torch / torchvision
#   · 构建：docker compose build
# ============================================================

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl 给 healthcheck 用；libgl1/libglib2.0-0 是 opencv 的运行库
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖：单独一层，改代码时不会触发重装
COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && pip install opencv-contrib-python==4.10.0.84 --no-deps

# 再拷代码
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY static/ ./static/
COPY assets/ ./assets/

# 向量库 / 文档 / 日志 都在 bind mount 里，不进镜像
RUN mkdir -p /app/data /app/logs

EXPOSE 7862

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:7862/health || exit 1

# 与 start.ps1 / README 保持一致：fastapi_app.py 内部会把项目根插进 sys.path
CMD ["python", "src/web_app/fastapi_app.py"]
