# Multi-arch: built for linux/amd64 and linux/arm64
# See .github/workflows/docker-publish.yml for CI/CD pipeline
FROM python:3.11-slim AS base

RUN useradd --create-home appuser

WORKDIR /app

COPY requirements.txt .
# Pre-pin build tools before installing requirements.
# setuptools 82+ vendors jaraco.context 6.1+ and wheel 0.46+, which resolves
# CVE-2026-23949 (jaraco.context path traversal) and CVE-2026-24049 (wheel
# privilege escalation). Both CVEs affect setuptools' internal _vendor/ copies;
# upgrading setuptools is the correct fix — patching jaraco.context/wheel alone
# does not update the vendored copies that Trivy scans.
RUN pip install --no-cache-dir "setuptools>=82.0.0" "wheel>=0.46.2" "jaraco.context>=6.1.0" && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# WEB_CONCURRENCY controls the number of Gunicorn worker processes.
#
# Default: 1 — safe on any hardware, correct for local dev and containers
# with unknown CPU allocation.
#
# OpenDQV's validate endpoint is CPU-bound (regex, rule evaluation, UUID
# generation). The standard Gunicorn formula (2 × cores + 1) is designed
# for I/O-bound apps and will OVER-subscribe CPU-bound workloads, increasing
# latency without improving throughput. Benchmark your own hardware:
#
#   Benchmark result (i5-7200U, 2 physical cores / 4 logical, hyperthreaded,
#   universal_benchmark contract, 14 rules, rate limiting off, 2026-03-12):
#     1 worker:  94 req/s  p50=112ms  p99=243ms
#     4 workers: 208 req/s  p50=19ms   p99=205ms  ← perf/prod overlay default
#
# For production on a dedicated server, benchmark with your actual hardware
# and set WEB_CONCURRENCY in your .env. Start at 1 and increase until
# throughput stops improving or p99 latency rises.
ENV WEB_CONCURRENCY=1

CMD ["sh", "-c", "gunicorn main:app -w ${WEB_CONCURRENCY} -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 120 --access-logfile -"]
