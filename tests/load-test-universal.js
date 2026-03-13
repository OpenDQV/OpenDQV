/**
 * OpenDQV Universal Benchmark Load Test
 *
 * Targets the universal_benchmark contract exclusively.
 * Used for the reproducible performance baseline.
 *
 * Prerequisite: copy the starter contract into the active contracts directory first:
 *   cp examples/starter_contracts/universal_benchmark.yaml contracts/
 *   cp -r examples/starter_contracts/ref contracts/
 *
 * Usage:
 *   node tests/load-test-universal.js [duration_seconds] [concurrency]
 *   e.g. node tests/load-test-universal.js 60 10
 */

const http = require("http");

const API_URL = process.env.OPENDQV_URL || "http://localhost:8000";
const DURATION = parseInt(process.argv[2] || "60", 10);
const CONCURRENCY = parseInt(process.argv[3] || "10", 10);
const CONTRACT = "universal_benchmark";

// ── Payload fixtures ─────────────────────────────────────────────────
// Mix of valid + invalid single records and a small batch.
// All use the universal_benchmark contract fields.

const SINGLE_VALID = {
  path: "/api/v1/validate",
  body: {
    contract: CONTRACT, version: "1.0",
    record: {
      record_id: "REC-001",
      email: "alice@example.com",
      phone: "+447911123456",
      created_date: "2026-01-15",
      status: "ACTIVE",
      currency: "GBP",
      country_code: "GB",
      amount: 2500.00,
      score: 87,
      description: "Standard test record for OpenDQV benchmark",
      suspension_reason: null,
    },
  },
};

const SINGLE_INVALID = {
  path: "/api/v1/validate",
  body: {
    contract: CONTRACT, version: "1.0",
    record: {
      record_id: "",                        // fails not_empty
      email: "not-an-email",               // fails regex
      phone: "07911123456",                // fails E.164 (no +)
      created_date: "2099-12-31",          // fails compare_to:today
      status: "UNKNOWN",                   // fails lookup
      currency: "XYZ",                     // fails lookup
      amount: -50,                         // fails range
      score: 150,                          // fails range
      description: "",
      suspension_reason: null,
    },
  },
};

const SINGLE_SUSPENDED = {
  path: "/api/v1/validate",
  body: {
    contract: CONTRACT, version: "1.0",
    record: {
      record_id: "REC-SUSP-001",
      email: "bob@corp.com",
      phone: "+12125551234",
      created_date: "2026-03-01",
      status: "SUSPENDED",
      currency: "USD",
      amount: 0,
      score: 0,
      suspension_reason: "Payment failure",  // required_if satisfied
    },
  },
};

const SINGLE_SUSPENDED_MISSING = {
  path: "/api/v1/validate",
  body: {
    contract: CONTRACT, version: "1.0",
    record: {
      record_id: "REC-SUSP-002",
      email: "carol@corp.com",
      phone: "+447700900123",
      created_date: "2026-02-28",
      status: "SUSPENDED",
      currency: "EUR",
      amount: 100,
      score: 50,
      // suspension_reason intentionally absent — triggers required_if
    },
  },
};

const BATCH_MIXED = {
  path: "/api/v1/validate/batch",
  body: {
    contract: CONTRACT, version: "1.0",
    records: [
      {
        record_id: "B001", email: "d@example.com", phone: "+14155551234",
        created_date: "2026-01-01", status: "ACTIVE", currency: "USD",
        amount: 100, score: 50,
      },
      {
        record_id: "B002", email: "bad-email", phone: "not-a-phone",
        created_date: "9999-01-01", status: "INVALID", currency: "ZZZ",
        amount: -1, score: 999,
      },
      {
        record_id: "B003", email: "e@example.com", phone: "+447911999888",
        created_date: "2025-06-15", status: "PENDING", currency: "GBP",
        amount: 50000, score: 75,
      },
      {
        record_id: "B004", email: "f@corp.io", phone: "+4915901234567",
        created_date: "2026-03-09", status: "INACTIVE", currency: "EUR",
        amount: 0, score: 0, description: "Closed account",
      },
      {
        record_id: "B005", email: "g@ngo.org", phone: "+2348012345678",
        created_date: "2026-02-20", status: "ACTIVE", currency: "NGN",
        amount: 75000, score: 92,
      },
    ],
  },
};

const PAYLOADS = [
  SINGLE_VALID,
  SINGLE_VALID,         // weight valid 2x
  SINGLE_INVALID,
  SINGLE_SUSPENDED,
  SINGLE_SUSPENDED_MISSING,
  BATCH_MIXED,
];

// ── Stats ────────────────────────────────────────────────────────────

const stats = {
  total: 0, success: 0, errors: 0,
  latencies: [], statusCodes: {},
  startTime: null, intervalStats: [],
};

function percentile(arr, p) {
  if (!arr.length) return 0;
  const s = arr.slice().sort((a, b) => a - b);
  return s[Math.max(0, Math.ceil((p / 100) * s.length) - 1)];
}

// ── HTTP ─────────────────────────────────────────────────────────────

function sendRequest() {
  return new Promise((resolve) => {
    const payload = PAYLOADS[Math.floor(Math.random() * PAYLOADS.length)];
    const data = JSON.stringify(payload.body);
    const url = new URL(payload.path, API_URL);
    const start = process.hrtime.bigint();

    const req = http.request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) },
    }, (res) => {
      let body = "";
      res.on("data", c => body += c);
      res.on("end", () => {
        const ms = Number(process.hrtime.bigint() - start) / 1e6;
        stats.total++;
        stats.latencies.push(ms);
        stats.statusCodes[res.statusCode] = (stats.statusCodes[res.statusCode] || 0) + 1;
        res.statusCode >= 200 && res.statusCode < 400 ? stats.success++ : stats.errors++;
        resolve();
      });
    });
    req.on("error", () => {
      stats.total++; stats.errors++; stats.latencies.push(0); resolve();
    });
    req.write(data);
    req.end();
  });
}

// ── Workers ──────────────────────────────────────────────────────────

async function worker(endTime) {
  while (Date.now() < endTime) await sendRequest();
}

// ── Progress ─────────────────────────────────────────────────────────

function snapshot(elapsed) {
  const window = stats.latencies.slice(-300);
  return {
    elapsed_s: Math.floor(elapsed / 1000),
    total: stats.total,
    rps: (stats.total / (elapsed / 1000)).toFixed(1),
    p50: percentile(window, 50).toFixed(1),
    p95: percentile(window, 95).toFixed(1),
    p99: percentile(window, 99).toFixed(1),
    errors: stats.errors,
  };
}

// ── Main ─────────────────────────────────────────────────────────────

async function main() {
  const label = DURATION >= 600 ? "10-minute" : DURATION >= 300 ? "5-minute" : "1-minute";
  console.log(`\n  OpenDQV Universal Benchmark — ${label} run`);
  console.log(`  Contract:    ${CONTRACT}`);
  console.log(`  Duration:    ${DURATION}s  |  Concurrency: ${CONCURRENCY}  |  Target: ${API_URL}`);
  console.log(`  Payload mix: ${PAYLOADS.length} variants (single valid/invalid/suspended + 5-record batch)\n`);

  stats.startTime = Date.now();
  const endTime = stats.startTime + DURATION * 1000;
  let lastSnapshotMinute = 0;

  const ticker = setInterval(() => {
    const elapsed = Date.now() - stats.startTime;
    const rps = (stats.total / (elapsed / 1000)).toFixed(0);
    const recent = stats.latencies.slice(-100);
    process.stdout.write(
      `\r  ${(elapsed/1000).toFixed(0)}s | ${stats.total.toLocaleString()} reqs | ${rps} rps | ` +
      `p50=${percentile(recent, 50).toFixed(1)}ms p99=${percentile(recent, 99).toFixed(1)}ms | errors=${stats.errors}`
    );

    // Snapshot every 10s
    const snap10 = Math.floor(elapsed / 10000);
    if (snap10 > stats.intervalStats.length) {
      stats.intervalStats.push(snapshot(elapsed));
    }
  }, 1000);

  const workers = Array.from({ length: CONCURRENCY }, () => worker(endTime));
  await Promise.all(workers);
  clearInterval(ticker);

  const totalTime = (Date.now() - stats.startTime) / 1000;
  const rps = stats.total / totalTime;
  const lats = stats.latencies;

  console.log(`\n\n  ── Final Results ─────────────────────────────────────`);
  console.log(`  Duration:     ${totalTime.toFixed(1)}s`);
  console.log(`  Contract:     ${CONTRACT}`);
  console.log(`  Concurrency:  ${CONCURRENCY} workers`);
  console.log(`  Total reqs:   ${stats.total.toLocaleString()}`);
  console.log(`  Throughput:   ${rps.toFixed(1)} req/s`);
  console.log(`  Success:      ${stats.success.toLocaleString()} (${((stats.success/stats.total)*100).toFixed(2)}%)`);
  console.log(`  Errors:       ${stats.errors}`);
  console.log(`  Status codes: ${JSON.stringify(stats.statusCodes)}`);
  console.log(``);
  console.log(`  Latency (ms):`);
  console.log(`    min:  ${percentile(lats, 0).toFixed(1)}`);
  console.log(`    p50:  ${percentile(lats, 50).toFixed(1)}`);
  console.log(`    p75:  ${percentile(lats, 75).toFixed(1)}`);
  console.log(`    p90:  ${percentile(lats, 90).toFixed(1)}`);
  console.log(`    p95:  ${percentile(lats, 95).toFixed(1)}`);
  console.log(`    p99:  ${percentile(lats, 99).toFixed(1)}`);
  console.log(`    max:  ${percentile(lats, 100).toFixed(1)}`);

  if (stats.intervalStats.length > 0) {
    console.log(`\n  ── Timeline (10s intervals) ──────────────────────────`);
    console.log(`  ${"Time".padEnd(8)} ${"Total".padEnd(9)} ${"RPS".padEnd(8)} ${"p50ms".padEnd(8)} ${"p95ms".padEnd(8)} ${"p99ms".padEnd(8)} Errors`);
    for (const s of stats.intervalStats) {
      console.log(
        `  ${(s.elapsed_s+"s").padEnd(8)} ${String(s.total).padEnd(9)} ${s.rps.padEnd(8)} ` +
        `${s.p50.padEnd(8)} ${s.p95.padEnd(8)} ${s.p99.padEnd(8)} ${s.errors}`
      );
    }
  }
  console.log("");

  return { rps: rps.toFixed(1), p50: percentile(lats,50).toFixed(1), p99: percentile(lats,99).toFixed(1), total: stats.total, errors: stats.errors };
}

main().catch(console.error);
