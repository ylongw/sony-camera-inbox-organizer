ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ARG VERSION=0.1.0
ARG PIP_INDEX_URL=https://pypi.org/simple
LABEL org.opencontainers.image.title="Sony Camera Inbox Organizer" \
      org.opencontainers.image.description="Camera media organizer with Sony Shot Mark to Live Photo conversion" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONFIG_PATH=/config/config.yaml \
    STATE_PATH=/config/state.sqlite \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libimage-exiftool-perl \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml LICENSE README.md ./
COPY src ./src
RUN PIP_INDEX_URL="${PIP_INDEX_URL}" pip install --no-cache-dir . \
    && groupadd --gid 1000 camera-inbox \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin camera-inbox \
    && mkdir -p /config /data \
    && chown -R camera-inbox:camera-inbox /config /data

USER camera-inbox
EXPOSE 8080
VOLUME ["/config", "/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sony-camera-inbox"]
