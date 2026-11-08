# syntax=docker/dockerfile:1.7
# Multi-stage build: wheels are built once, the runtime image has no compilers and runs as non-root.

FROM python:3.12-slim AS build
WORKDIR /src
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --wheel-dir /wheels .

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RTO_ENVIRONMENT=production WEB_CONCURRENCY=4 PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /app app
WORKDIR /app
COPY --from=build /wheels /wheels
RUN pip install /wheels/*.whl && rm -rf /wheels
COPY alembic.ini ./
COPY migrations ./migrations
COPY sample_data ./sample_data
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
# --forwarded-allow-ips: the ingress controller terminates TLS; restrict to its CIDR in production.
CMD ["sh", "-c", "rm -rf $PROMETHEUS_MULTIPROC_DIR && mkdir -p $PROMETHEUS_MULTIPROC_DIR && exec gunicorn rto.main:app -k uvicorn_worker.UvicornWorker -w ${WEB_CONCURRENCY} -b 0.0.0.0:8000 --graceful-timeout 30 --timeout 120 --forwarded-allow-ips='*' --access-logfile -"]
