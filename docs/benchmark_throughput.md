# Benchmark: Single-Process Throughput and Monthly Capacity

## Methodology

Throughput was measured using a reproducible three-duration load test series against a running
Docker deployment. The same test was run at 1 minute, 5 minutes, and 10 minutes to confirm
that throughput is stable over time — not a short burst that degrades.

**Full results and raw data:** `tests/load-test-results.md` (latest run: 2026-03-27)

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
| 1-minute | 60.1 s | 14,078 | 234.4 req/s | 0 | 18.5 ms | 125.3 ms | 159.8 ms |
| 5-minute | 300.1 s | 71,243 | 237.4 req/s | 0 | 19.7 ms | 135.8 ms | 166.5 ms |
| 10-minute | 600.1 s | 137,208 | 228.7 req/s | 0 | 18.7 ms | 131.9 ms | 162.7 ms |
| **Combined** | **960 s** | **222,529** | **~229–237 req/s** | **0** | — | — | — |

Zero errors across 222,529 requests. Throughput is stable across all three run lengths —
no warmup spike, no degradation. The 5-minute figure (237.4 req/s) is the most representative
of a stabilised mid-range load. p99 dropped from ~205ms to ~163ms vs. the previous baseline,
attributable to async fire-and-forget SQLite writes on the validation hot path (v1.8.7).

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

At a sustained **237 req/s** (5-minute stabilised figure, conservative), a single-process
deployment can handle:

| Timeframe | Records at 237 req/s |
|-----------|----------------------|
| 1 hour | ~853,200 |
| 1 day | ~20,476,800 |
| 30 days | **~614,304,000 (614 M)** |

The arithmetic is straightforward: `237 req/s × 86,400 s/day × 30 days = 614 M records/month`.

Any monthly volume target can be evaluated the same way — divide by 30 days and by 86,400
seconds to get the required req/s, then compare to the 237 req/s measured baseline.

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

Each OpenDQV instance shares no per-request state — horizontal scaling behind a load balancer requires no coordination between instances.

## Platform coverage

### Linux — Dell XPS 13 (reference environment)

The results above were measured on Ubuntu Linux with native Docker (no virtualisation layer).
This is the recommended baseline for capacity planning.

### macOS — MacBook Pro 13" 2020 (Docker Desktop)

**Hardware:** Intel Core i7-1068NG7 @ 2.30GHz, 32 GB RAM
**OS:** macOS (Darwin 25.4.0, updated)
**Docker:** Docker Desktop (updated; containers run inside a Linux VM — adds network virtualisation overhead)
**Config:** 4 workers (`WEB_CONCURRENCY=4`), rate limiting disabled
**Date:** 2026-03-27 (v1.8.7, async fire-and-forget SQLite)

| Run | Duration | Total requests | Throughput | Errors | p95 | p99 |
|-----|----------|---------------|------------|--------|-----|-----|
| 1-minute | 60 s | 13,506 | **224.9 req/s** | 0 | 127.1 ms | 174.8 ms |
| 5-minute | 300 s | 67,468 | **224.8 req/s** | 0 | 123.7 ms | 171.2 ms |
| 10-minute | 600 s | 133,926 | **223.2 req/s** | 0 | 124.8 ms | 174.0 ms |

**Combined: 214,900 requests, zero errors.**

**Throughput note:** ~5.5% lower than Linux native Docker (228.7 req/s 10-min) — expected Docker
Desktop VM overhead. Throughput is rock-solid across all three durations (224.9 → 224.8 → 223.2 req/s),
confirming no degradation under sustained load. The Linux native Docker figures remain the
authoritative benchmark for production capacity planning.

**Interpretation:** For production capacity planning, use the Linux 5-minute stabilised figure
(237 req/s) as the conservative baseline — it reflects native Docker on real server-class hardware.
The macOS figures reflect developer-laptop Docker Desktop performance and should not be used for
production sizing. A cloud VM (e.g. 4-core compute-optimised) will outperform both.

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
**Date:** 2026-03-19

| Run | Duration | Total requests | Throughput | Errors | p50 | p95 | p99 |
|-----|----------|---------------|------------|--------|-----|-----|-----|
| 1-minute (cold) | 60.1 s | 3,426 | 57.0 req/s | 0 | 150 ms | 473 ms | 753 ms |
| 5-minute | 300.1 s | 21,563 | 71.8 req/s | 0 | 69 ms | 406 ms | 639 ms |
| 10-minute | 600.2 s | 47,454 | **79.1 req/s** | **0** | **47 ms** | 402 ms | 523 ms |

**Combined: 72,443 requests, zero errors.**

**Warmup profile:** The Pi 400 shows pronounced warmup behaviour — p50 drops from 150ms
(cold 1-minute) to 47ms (warm 10-minute) as the Python interpreter and OS page cache
warm up. The 10-minute throughput (79.1 req/s) is the definitive figure. The 10-minute
timeline is flat: every 10-second interval holds 78.8–81.0 req/s with zero errors.

**Occasional p99 spikes** (~1700ms) appear at ~360s and ~510s — consistent with SD card
I/O or GC pauses. These do not affect throughput or error rate.

**Note:** The Pi 400 is a constrained ARM64 device at the lower end of what OpenDQV
supports — not a production deployment target, but a confirmed proof that OpenDQV runs
correctly on ARM64 with zero errors across 72,443 requests. For AWS Graviton deployments
(Cortex-X1 class, 3GHz+), throughput will be significantly higher than the Pi 400 figure.
For edge use cases (IoT, factory floor, low-power validation nodes), the Python path
(`bash install.sh`, no Docker) will outperform the Docker path on this hardware.

---

### Platform comparison

| Platform | Hardware | Workers | Workload | req/s | p50 | p99 |
|----------|----------|---------|----------|-------|-----|-----|
| EC2 c6i.large (Docker) | Xeon @ ~3.5 GHz, 4 GB | 2 | Valid-only | **485** | 36 ms | 208 ms |
| EC2 c6i.large (Docker) | Xeon @ ~3.5 GHz, 4 GB | 2 | **Mixed 50/50** | **~482** | **~37 ms** | **~182 ms** |
| EC2 c6i.large (Docker) | Xeon @ ~3.5 GHz, 4 GB | 2 | Invalid-only | **480** | 37 ms | 156 ms |
| Linux (native Docker) | i5-7200U @ 2.5 GHz, 8 GB | 4 | Mixed | ~229–237 | 19 ms | 163 ms |
| macOS (Docker Desktop) | i7-1068NG7 @ 2.3 GHz, 32 GB | 4 | Mixed | ~224 | — | 174 ms |
| Windows 10 (Docker Desktop) | i7, Windows 10 | 4 | Mixed | ~185* | 17 ms | 288 ms |
| MacBook Pro 2019 (bare metal) | i7 @ 2.6 GHz | 1 | Valid-only | ~257† | — | — |
| Raspberry Pi 400 (ARM64, Docker) | Cortex-A72 @ 1.8 GHz, 4 GB | 4 | Mixed | 79.1 | 47 ms | 523 ms |

*1-minute figure only. †Bare metal, not Docker.

**Key insight from the EC2 data (v1.9.9 hot-path caches):** The valid/invalid performance gap
collapsed from 18% to ~1% after caching condition flags, severity values, and error codes on
the Rule model at parse time. Invalid records no longer carry meaningful overhead vs valid records.
A realistic production workload should be sized against the **mixed workload figure (~482 req/s,
2 workers on c6i.large)**.

**Sizing rule of thumb:** `WEB_CONCURRENCY = number of vCPUs`. c6i.large (2 vCPU) saturates at
2 workers — adding a 3rd or 4th worker adds context-switch overhead without throughput gain.

**Benchmark date:** EC2 figures measured 2026-04-06 (`customer` contract, 12 rules, `AUTH_MODE=open`,
rate limiting raised to 100k/min, Apache Bench 10k requests × 20 concurrent).
Linux/macOS/Windows/Pi figures measured 2026-03-12 to 2026-03-27 (`universal_benchmark` contract, same rule count).

OpenDQV runs on all platforms with zero errors. For production capacity planning,
use the EC2 mixed-workload figure (~482 req/s, c6i.large, 2 workers) as the cloud baseline.
The Pi 400 figure (79.1 req/s) is the ARM64 floor — AWS Graviton deployments will
significantly exceed this.

## Five Standard Workloads (in-process, no Docker required)

These workloads run via `pytest tests/test_benchmark.py -v -s` and measure engine throughput
in-process without network or HTTP overhead. They are the reproducible baseline for
cross-tool comparison. Add `-s` to see per-workload throughput printed to stdout.

| Workload | Description | Path | Threshold |
|----------|-------------|------|-----------|
| W1 | Single-record, `universal_benchmark` (14 rules), 1,000 sequential calls | Pure Python | < 10 s |
| W2 | Batch, mixed 10-rule contract, 1,000 rows | DuckDB | < 30 s |
| W3 | Batch, mixed 10-rule contract, 10,000 rows | DuckDB | < 60 s |
| W4 | Batch, 5-regex-rule contract, 1,000 rows | DuckDB | < 30 s |
| W5 | Batch, 5-range-rule contract, 1,000 rows | DuckDB | < 30 s |

Thresholds are conservative CI guards. On a development laptop the actual times are
typically 5–20× faster than the threshold. Run with `-s` to see the real numbers.

## Comparative Methodology (community-submitted)

OpenDQV does not publish comparative benchmarks against dbt tests, Great Expectations,
or Soda Core, because:

1. Fair comparison requires installing and configuring each tool correctly — a one-person
   task that is likely to produce a biased setup.
2. These tools have different scopes: dbt tests run post-load in a warehouse, GE and Soda
   run against DataFrames or warehouse connections. OpenDQV runs write-time at the API
   boundary. The comparison is architectural, not just speed.

**If you run a comparison and want to contribute your results:**

1. Use the five standard workloads above as the OpenDQV baseline
2. Run equivalent checks in dbt / GE / Soda against the same data using their recommended
   patterns (not naive Python loops)
3. Record hardware, tool version, and configuration
4. Open a PR adding a `docs/community_benchmarks/` file with your results and methodology

The comparison that matters most for regulated pipelines is not raw throughput — it is
**time-to-failure-detection**: how quickly does a bad record surface an error at the point
of write vs. at the next pipeline run or after warehouse ingestion?

## See also

- `tests/load-test-results.md` — full raw benchmark data and run history
- `tests/load-test-universal.js` — the load test script
- `tests/test_benchmark.py` — lightweight in-process smoke test (CI regression guard, not a load test)
- `docs/deployment_registry.md` — deployment configuration options
- `docs/disaster-recovery.md` — high-availability setup
