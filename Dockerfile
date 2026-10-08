# Pokemon Card Scanner — CPU inference image
# Data dir (DB, card images, CLIP index, scans) is mounted at the same
# absolute path at runtime: /opt/data/pokemon-card-scanner/data
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# OpenCV (pulled in by ultralytics) needs libglib; ffmpeg not needed for CPU
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/data/pokemon-card-scanner

# CPU-only torch wheels keep the image ~1 GB smaller than the CUDA default
COPY requirements.txt .
RUN pip install --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple \
        torch==2.14.0+cpu torchvision==0.29.0+cpu \
    && pip install -r requirements.txt

COPY . .

# Run as the host's uid so bind-mounted data volumes (owned by whoever created
# them on the host, e.g. uid 10000 for hermes) remain writable.
ARG HOST_UID=1000
ARG HOST_GID=1000
RUN groupadd -g ${HOST_GID} appgroup || true && \
    useradd -m -u ${HOST_UID} -g ${HOST_GID} -o appuser && \
    chown -R ${HOST_UID}:${HOST_GID} /opt/data/pokemon-card-scanner
COPY --chown=appuser:appuser docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER appuser
ENV HOME=/home/appuser \
    HOST=0.0.0.0 \
    PORT=5005

EXPOSE 5005
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5005/health', timeout=4)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
