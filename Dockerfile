# syntax=docker/dockerfile:1

# 默认值适合直接构建；国内服务器可在 compose 的 build.args 中切换代理。
ARG PYTHON_IMAGE=python:3.13-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.1

# 直接复用 uv 官方镜像中的静态二进制，避免通过 pip 下载约 22 MB 的 uv wheel。
FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE}

# FROM 会开启新的 ARG 作用域，需在当前阶段重新声明才能供 ENV 使用。
ARG UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_HTTP_TIMEOUT=120 \
    UV_NO_PROGRESS=1 \
    PATH="/app/.venv/bin:${PATH}"

# apt 源切换到腾讯云镜像（国内构建更快）。
RUN sed -i \
        's|deb.debian.org|mirrors.cloud.tencent.com|g; s|security.debian.org|mirrors.cloud.tencent.com|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先只复制依赖清单，业务代码变化不会导致依赖重装。
COPY pyproject.toml uv.lock ./

# cache mount 在 uv.lock 变化、依赖层必须重建时仍可复用已下载的 wheel。
# 应用是单文件入口，无需把项目本身安装进虚拟环境。
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,id=billmanage-uv,target=/root/.cache/uv \
    uv sync --no-dev --frozen --no-install-project

COPY main.py ./
COPY templates ./templates
COPY static ./static
COPY 报销单模版.xlsx ./

EXPOSE 8000

# 直接运行虚拟环境中的 uvicorn，容器启动时不再让 uv 检查或同步依赖。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
