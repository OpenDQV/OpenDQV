# Benchmark: Single-Process Throughput and Monthly Capacity

## Methodology

Throughput was measured using a reproducible three-duration load test series against a running
Docker deployment. The same test was run at 1 minute, 5 minutes, and 10 minutes to confirm
that throughput is stable over time — not a short burst that degrades.

**Full results and raw data:** `tests/load-test-results.md` (latest run: 2026-03-12)

## Test environment

- **Hardware:** Dell XPS 13 9360 (Intel Core i5-7200U @ 2.50GHz, 8 GB RAM) — a developer laptop, not a server
- **OS:** Ubuntu Linux (6.x kernel)
- **Docker:** FastAPI + Gunicorn/Uvicorn, 4 workers (`WEB_CONCURRENCY=4`) via `docker-compose.perf.yml`
  *(the default out-of-the-box is 1 worker — see Dockerfile; `docker-compose.prod.yml` defaults to 4)*
- **Contract:** `universal_benchmark` (14 rules — `not_empty`, `max_length`, `regex`, `compare`,
  `lookup` ×2, `range`, `unique`, `required_if`, `date_format`, `min_length`)
- **Rate limiting:** disabled (`RATE_LIMIT_VALIDATE=off`) to measure pure engine throughput
- **Tool:** `tests/load-test-universal.js` (Node.js, 10 concurrent workers, 6 payload variants)

## Results

| Run | Duration | Total requests | Throughput | Errors | p50 | p95 | p99 |
|-----|----------|---------------|------------|--------|-----|-----|-----|
| 1-minute | 60.1 s | 11,595 | 193.0 req/s | 0 | 24.4 ms | 150.5 ms | 207.6 ms |
| 5-minute | 300.1 s | 62,575 | 208.5 req/s | 0 | 19.1 ms | 151.1 ms | 205.1 ms |
| 10-minute | 600.1 s | 144,510 | 240.8 req/s | 0 | 13.7 ms |  144.5 ms | 202.9 ms |
| **Combined** | **960 s** | **218,680** | **~208–241 req/s** | **0** | — | — | — |

Zero errors across 218,680 requests. The 10-minute run shows a ramp from ~204 req/s at t=10s
to ~241 req/s by t=590s as the CPU boost state engages — the 5-minute figure (208.5 req/s) is the
most representative of a stabilised mid-range load. No degradation, no memory growth, no connection
leaks.

## Reproducing these results

```bash
docker compose down -v --remove-orphans
docker compose -f docker-compose.yml -f docker-compose.perf.yml up -d --build api
sleep 4

node tests/load-test-universal.js 60 10     # 1-minute
node tests/load-test-universal.js 300 10    # 5-minute
node tests/load-test-universal.js 600 10    # 10-minute
```

`docker-compose.perf.yml` sets `RATE_LIMIT_VALIDATE=off` and `WEB_CONCURRENCY=4`.

## Extrapolation to monthly volumes

At a sustained **208 req/s** (5-minute stabilised figure, conservative), a single-process
deployment can handle:

| Timeframe | Records at 208 req/s |
|-----------|----------------------|
| 1 hour | ~748,800 |
| 1 day | ~17,971,200 |
| 30 days | **~539,136,000 (539 M)** |

The arithmetic is straightforward: `208 req/s × 86,400 s/day × 30 days = 539 M records/month`.
Under sustained load with a warm CPU, throughput climbs to ~241 req/s, giving ~622 M records/month
at the ceiling.

Any monthly volume target can be evaluated the same way — divide by 30 days and by 86,400
seconds to get the required req/s, then compare to the 208 req/s measured baseline.

## Bottleneck analysis

The throughput ceiling is **Gunicorn worker count**, not CPU or memory:

- CPU utilisation at 208 req/s: well below 100% — the CPU is not the limit
- Memory per worker: ~45 MB RSS — 4 workers use ~180 MB total
- Each worker handles one request synchronously; throughput scales linearly with worker count
- Raising `WEB_CONCURRENCY` to 8 on this hardware would push throughput to ~400 req/s

The `universal_benchmark` contract (14 rules, 2 lookup file reads) represents a deliberately
heavy contract. Simpler contracts with no lookup rules run ~30% faster.

## Rate limiter note

With the default `RATE_LIMIT_VALIDATE=300/minute` in place, throughput is capped at ~158 req/s
for high-concurrency callers. For deployments processing high volumes, either raise the limit
or move rate enforcement upstream to nginx/Caddy. See `tests/load-test-results.md`
(rate-limiter overhead investigation, 2026-03-09) for the full analysis.

## Batch endpoint

The `/api/v1/validate/batch` endpoint processes multiple records per HTTP round-trip,
eliminating per-request serialisation overhead. For bulk ingestion (nightly ETL, file
processing), batch mode gives **10–50× throughput improvement**. A batch job at 5,000
records/batch can process very large volumes with a fraction of the API calls.

## Horizontal scaling threshold

A single 4-worker process is appropriate for most deployments. Consider adding instances when:

- Sustained throughput requirement exceeds ~400 req/s (raise workers first)
- You have many simultaneous callers rather than one high-volume caller
- You need geographic distribution or fault isolation

Each OpenDQV instance is stateless — horizontal scaling behind a load balancer requires
no coordination between instances.

## Platform coverage

### Linux — Dell XPS 13 (reference environment)

The results above were measured on Ubuntu Linux with native Docker (no virtualisation layer).
This is the recommended baseline for capacity planning.

### macOS — MacBook Pro 13" 2020 (Docker Desktop)

**Hardware:** Intel Core i7-1068NG7 @ 2.30GHz, 32 GB RAM
**OS:** macOS 26.0.1
**Docker:** Docker Desktop (containers run inside a Linux VM — adds network virtualisation overhead)
**Config:** 4 workers (`WEB_CONCURRENCY=4`), rate limiting disabled

| Run | Duration | Total requests | Throughput | Errors | p50 | p99 |
|-----|----------|---------------|------------|--------|-----|-----|
| 1-minute | 60 s | 15,393 | **256.1 req/s** | 0 | 17.8 ms | 162.6 ms |

**Observed vs predicted:** The original prediction (Docker Desktop 10–20% slower than Linux)
did not hold — the Mac outperforms the Linux reference on throughput because the i7-1068NG7
(10th-gen Ice Lake, 2020) is a significantly faster chip than the i5-7200U (7th-gen Kaby Lake,
2017) used in the Linux reference. The Mac has 4× the RAM and a newer CPU microarchitecture.

**Key difference between platforms:**

| Metric | Linux (i5-7200U, native Docker) | macOS (i7-1068NG7, Docker Desktop) |
|--------|--------------------------------|-------------------------------------|
| 1-min throughput | 193.0 req/s | **256.1 req/s** |
| p50 latency | 24.4 ms | **17.8 ms** |
| p99 latency | 207.6 ms | **162.6 ms** |
| Stabilised (10-min) | 240.8 req/s | not yet measured |

**Interpretation:** For production capacity planning, use the Linux 5-minute stabilised figure
(208 req/s) as the conservative baseline — it reflects a realistic server-class deployment.
The macOS figures reflect developer-laptop performance and should not be used for production
sizing. A cloud VM (e.g. 4-core compute-optimised) will outperform both.

### Windows 10 — Dell XPS 13 (Docker Desktop)

**Hardware:** Intel Core i7, Windows 10
**Docker:** Docker Desktop for Windows (containers run inside a WSL2/Hyper-V VM)
**Config:** 4 workers (`WEB_CONCURRENCY=4`), rate limiting disabled

| Run | Duration | Total requests | Throughput | Errors | p50 | p99 |
|-----|----------|---------------|------------|--------|-----|-----|
| 1-minute | 60 s | 11,108 | **185.1 req/s** | 0 | 16.5 ms | 288.3 ms |

**Note:** The higher p99 (288.3 ms vs 162–207 ms on Linux/Mac) reflects the additional
virtualisation overhead in Docker Desktop for Windows. The p50 (16.5 ms) is competitive,
indicating the engine itself is fast — the tail latency is driven by Windows networking
stack overhead under sustained load, not the validation logic.

### Raspberry Pi 400 (ARM64, Docker)

**Hardware:** Raspberry Pi 400 (ARM Cortex-A72 @ 1.8 GHz, 4 GB RAM)
**OS:** Raspberry Pi OS (64-bit, Debian-based)
**Docker:** Docker Engine on ARM64 (native, no virtualisation layer)
**Config:** 4 workers (`WEB_CONCURRENCY=4`), rate limiting disabled

| Run | Duration | Total requests | Throughput | Errors | p50 | p99 |
|-----|----------|---------------|------------|--------|-----|-----|
| 1-minute | 60 s | 4,821 | **80.4 req/s** | 0 | 45.8 ms | 688.0 ms |

**Note:** The Pi 400 is a constrained ARM64 device — not a deployment target, but a proof
that OpenDQV runs correctly on ARM architecture with zero errors. The high p99 (688 ms)
reflects CPU-bound behaviour on a 1.8 GHz single-board computer under sustained load.
For edge use cases (IoT, factory floor, low-power validation nodes) the Python path
(`bash install.sh`, no Docker) will perform better than the Docker path on this hardware.

---

### Four-platform comparison

| Platform | Hardware | Throughput (1-min) | p50 | p99 |
|----------|----------|--------------------|-----|-----|
| Linux (native Docker) | i5-7200U, 8 GB | 193.0 req/s | 24.4 ms | 207.6 ms |
| macOS (Docker Desktop) | i7-1068NG7, 32 GB | 256.1 req/s | 17.8 ms | 162.6 ms |
| Windows 10 (Docker Desktop) | i7, Windows 10 | 185.1 req/s | 16.5 ms | 288.3 ms |
| Raspberry Pi 400 (ARM64, Docker) | Cortex-A72 @ 1.8 GHz, 4 GB | 80.4 req/s | 45.8 ms | 688.0 ms |

OpenDQV runs on all four platforms with zero errors. For production capacity planning,
use the Linux native Docker figures as the conservative baseline.

## See also

- `tests/load-test-results.md` — full raw benchmark data and run history
- `tests/load-test-universal.js` — the load test script
- `tests/test_benchmark.py` — lightweight in-process smoke test (CI regression guard, not a load test)
- `docs/deployment_registry.md` — deployment configuration options
- `docs/disaster-recovery.md` — high-availability setup
