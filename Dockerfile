# SPDX-License-Identifier: GPL-3.0-or-later
# Local build only: no receiver image is published by this project.
FROM ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc AS uv
FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3 AS base
ENV DEBIAN_FRONTEND=noninteractive UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN test "$(dpkg --print-architecture)" = amd64 && \
    apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv ca-certificates iproute2 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH

FROM base AS host
RUN apt-get update && apt-get install -y --no-install-recommends pcscd libccid socat && \
    rm -rf /var/lib/apt/lists/* && mkdir -p /run/pcscd
COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable --python /usr/bin/python3.12
CMD ["python", "-m", "sdr_dtv_poc.host_service"]

FROM base AS native
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git cmake g++ make=4.3-4.1build2 pkg-config libpcsclite-dev \
    gnuradio-dev=3.10.9.2-1.1ubuntu2 libgsl-dev=2.7.1+dfsg-6ubuntu2 \
    pybind11-dev=2.11.1-2 python3-numpy python3-scipy ffmpeg=7:6.1.1-3ubuntu5 && \
    rm -rf /var/lib/apt/lists/*
RUN git init /opt/gr-isdbt && \
    git -C /opt/gr-isdbt remote add origin https://github.com/git-artes/gr-isdbt.git && \
    git -C /opt/gr-isdbt fetch --depth=1 origin \
      56b2556c14ecc5d710070f969fda7a2deae65d8b 261019a65f5ac09144a81f0800f9a80bdc88e539
COPY native/build/runtime/prepare_source.py /opt/prepare_runtime.py
COPY native/build/wideband/prepare_source.py /opt/prepare_wideband.py
RUN /usr/bin/python3 /opt/prepare_runtime.py /opt/gr-isdbt /opt/oneseg-source && \
    cmake -S /opt/oneseg-source -B /opt/oneseg-build -DCMAKE_BUILD_TYPE=RelWithDebInfo && \
    cmake --build /opt/oneseg-build -j2 && \
    /usr/bin/python3 /opt/prepare_wideband.py /opt/gr-isdbt /opt/wideband-source && \
    cmake -S /opt/wideband-source -B /opt/wideband-build -DCMAKE_BUILD_TYPE=RelWithDebInfo && \
    cmake --build /opt/wideband-build -j2 && \
    PYTHONPATH=/opt/wideband-build /usr/bin/python3 -c 'from gnuradio import gr; import hlfecwideband'
RUN git init /opt/libaribb25 && \
    git -C /opt/libaribb25 remote add origin https://github.com/tsukumijima/libaribb25.git && \
    git -C /opt/libaribb25 fetch --depth=1 origin dc1d96a90ea554d8997b238fd6712eccf553cdb3 && \
    git -C /opt/libaribb25 checkout --detach dc1d96a90ea554d8997b238fd6712eccf553cdb3 && \
    cmake -S /opt/libaribb25 -B /opt/libaribb25-build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/cas && \
    cmake --build /opt/libaribb25-build -j2 && cmake --install /opt/libaribb25-build
RUN curl -fL --retry 2 https://github.com/tsduck/tsduck/releases/download/v3.45-4798/tsduck_3.45-4798.ubuntu24_amd64.deb -o /tmp/tsduck.deb && \
    echo '0023689f76e75b64e45253771208bcf8ca95b91fbfd0900da097c729f3c39fde  /tmp/tsduck.deb' | sha256sum -c - && \
    apt-get update && apt-get install -y --no-install-recommends /tmp/tsduck.deb && \
    rm /tmp/tsduck.deb && rm -rf /var/lib/apt/lists/* && \
    mkdir -p /usr/share/doc/tsduck && \
    curl -fL --retry 2 https://raw.githubusercontent.com/tsduck/tsduck/v3.45-4798/LICENSE.txt -o /usr/share/doc/tsduck/LICENSE.txt && \
    echo '58be33f8bd6df6371a95d310ea0ea405d9d46c5186bd8fb438020ea09d685353  /usr/share/doc/tsduck/LICENSE.txt' | sha256sum -c - && \
    dpkg-query -W -f='${Package}=${Version}\n' > /opt/packages.lock
FROM native AS receiver
COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable --python /usr/bin/python3.12
COPY native/receiver /research
COPY native/manifest.json native/README.md native/UPSTREAM-LICENSE /opt/receiver-notices/
RUN python -c "from pathlib import Path; from sdr_dtv_poc.live_sources import verify; verify(Path('/research'))"
COPY examples/live.json /config/live.json
RUN mkdir -p /data
ENV PYTHONPATH=/app/src:/opt/wideband-build SDR_NATIVE_PYTHONPATH=/opt/wideband-build \
    SDR_DATA_DIR=/data SDR_LIVE_CONFIG=/config/live.json SDR_DEVICE_LOCK_DIR=/device \
    SDR_CAS_EXECUTABLE=/cas/bin/arib-b25-stream-test LD_LIBRARY_PATH=/cas/lib
USER 10001:10001
STOPSIGNAL SIGTERM
CMD ["uvicorn", "sdr_dtv_poc.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--timeout-graceful-shutdown", "35"]
