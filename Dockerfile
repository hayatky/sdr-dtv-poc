# SPDX-License-Identifier: GPL-3.0-or-later
FROM ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc AS uv
FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3
ENV DEBIAN_FRONTEND=noninteractive UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv ca-certificates ffmpeg=7:6.1.1-3ubuntu5 && \
    dpkg-query -W -f='${Package}=${Version}\n' > /opt/packages.lock && \
    rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src ./src
COPY scripts/generate-demo.py ./scripts/generate-demo.py
RUN uv sync --locked --no-dev --no-editable --python /usr/bin/python3.12 && \
    uv run --no-sync python scripts/generate-demo.py --output /opt/demo/demo.ts && \
    mkdir -p /data /run/sdr-hls && chown 10001:10001 /data /run/sdr-hls
ENV PATH=/app/.venv/bin:$PATH SDR_DATA_DIR=/data SDR_DEMO_PATH=/opt/demo/demo.ts
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["uvicorn", "sdr_dtv_poc.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--timeout-graceful-shutdown", "10"]
