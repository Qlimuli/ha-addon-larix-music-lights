ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG C.UTF-8

# Install system dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-numpy \
    ffmpeg \
    bash \
    jq \
    curl \
    && pip3 install --no-cache-dir --break-system-packages \
        requests \
        websockets \
        aiohttp

# Copy root filesystem
COPY rootfs /

# Make scripts executable
RUN chmod a+x /run.sh /app/analyzer.py

WORKDIR /app

# Watchdog GUI (served via Home Assistant Ingress)
EXPOSE 8099

CMD ["/run.sh"]
