FROM python:3.11-slim

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin pulseguard \
    && mkdir -p /data /app \
    && chown pulseguard:pulseguard /data /app

WORKDIR /app
COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src
COPY config /app/config
COPY docker/entrypoint.sh /entrypoint.sh

RUN pip install --no-cache-dir . \
    && chmod 755 /entrypoint.sh \
    && chown -R pulseguard:pulseguard /app

USER pulseguard
EXPOSE 8080
VOLUME /data
ENTRYPOINT ["/entrypoint.sh"]
