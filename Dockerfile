# 基础镜像可切换：国内构建时用 docker.m.daocloud.io 代理（docker compose build.args 覆盖）
ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=https://mirrors.cloud.tencent.com/pypi/simple

# apt 源切换到腾讯云镜像（国内构建更快）
RUN sed -i \
        's|deb.debian.org|mirrors.cloud.tencent.com|g; s|security.debian.org|mirrors.cloud.tencent.com|g' \
        /etc/apt/sources.list.d/debian.sources

# Tesseract OCR with Chinese support for scanned invoices.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --no-dev --frozen

COPY main.py ./
COPY templates ./templates
COPY static ./static
COPY 报销单模版.xlsx ./

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
