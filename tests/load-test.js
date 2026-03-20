/**
 * OpenDQV Load Test
 *
 * Runs concurrent validation requests against the API and reports throughput,
 * latency percentiles, and error rates.
 *
 * Usage: node tests/load-test.js [duration_seconds] [concurrency]
 *   e.g. node tests/load-test.js 60 10
 */

const http = require("http");

const API_URL = process.env.OPENDQV_URL || "http://localhost:8000";
const DURATION = parseInt(process.argv[2] || "60", 10);
const CONCURRENCY = parseInt(process.argv[3] || "10", 10);

// Mix of payloads to simulate real traffic
const PAYLOADS = [
  // Valid SF contact (salesforce_prod)
  {
    path: "/api/v1/validate",
    body: {
      contract: "sf_contact", version: "1.0", context: "salesforce_prod",
      record: { FirstName: "Sarah", LastName: "Chen", Email: "sarah@acme.com", Phone: "+1 415-555-0101", Birthdate: "1985-03-15", AccountName: "Acme Corp", MailingCity: "SF", MailingCountry: "US" },
    },
  },
  // Invalid SF contact (missing required)
  {
    path: "/api/v1/validate",
    body: {
      contract: "sf_contact", version: "1.0", context: "salesforce_prod",
      record: { FirstName: "", LastName: "", Email: "bad", Birthdate: "not-a-date", AccountName: "" },
    },
  },
  // Valid SF lead (web_form)
  {
    path: "/api/v1/validate",
    body: {
      contract: "sf_lead", version: "1.0", context: "web_form",
      record: { FirstName: "Mike", LastName: "T", Company: "DataFlow", Email: "mike@dataflow.com", LeadSource: "Web", Status: "New" },
    },
  },
  // Invalid SF lead (disposable email)
  {
    path: "/api/v1/validate",
    body: {
      contract: "sf_lead", version: "1.0", context: "web_form",
      record: { FirstName: "Spam", LastName: "Bot", Company: "Fake", Email: "spam@mailinator.com", LeadSource: "Web", Status: "New" },
    },
  },
  // Customer onboarding (salesforce context)
  {
    path: "/api/v1/validate",
    body: {
      contract: "customer_onboarding", version: "1.0", context: "salesforce",
      record: { first_name: "Sunny", last_name: "Sharma", email: "sunny@example.com", date_of_birth: "1990-05-15" },
    },
  },
  // Batch (3 records)
  {
    path: "/api/v1/validate/batch",
    body: {
      contract: "sf_contact", version: "1.0", context: "salesforce_prod",
      records: [
        { FirstName: "A", LastName: "B", Email: "a@b.com", Birthdate: "1990-01-01", AccountName: "X" },
        { FirstName: "", LastName: "", Email: "bad", Birthdate: "", AccountName: "" },
        { FirstName: "C", LastName: "D", Email: "c@d.com", Birthdate: "1985-06-15", AccountName: "Y" },
      ],
    },
  },
];

// ── Stats tracking ──────────────────────────────────────────────────

const stats = {
  total: 0,
  success: 0,
  errors: 0,
  latencies: [],
  statusCodes: {},
  startTime: null,
  intervalStats: [],  // per-10s snapshots
};

function percentile(arr, p) {
  if (arr.length === 0) return 0;
  const sorted = arr.slice().sort((a, b) => a - b);
  const idx = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.max(0, idx)];
}

// ── HTTP request ────────────────────────────────────────────────────

function sendRequest() {
  return new Promise((resolve) => {
    const payload = PAYLOADS[Math.floor(Math.random() * PAYLOADS.length)];
    const data = JSON.stringify(payload.body);
    const url = new URL(payload.path, API_URL);
    const start = process.hrtime.bigint();

    const req = http.request(
      url,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
        },
      },
      (res) => {
        let body = "";
        res.on("data", (chunk) => (body += chunk));
        res.on("end", () => {
          const elapsed = Number(process.hrtime.bigint() - start) / 1e6; // ms
          stats.total++;
          stats.latencies.push(elapsed);
          stats.statusCodes[res.statusCode] = (stats.statusCodes[res.statusCode] || 0) + 1;
          if (res.statusCode >= 200 && res.statusCode < 400) {
            stats.success++;
          } else {
            stats.errors++;
          }
          resolve();
        });
      }
    );
    req.on("error", () => {
      stats.total++;
      stats.errors++;
      stats.latencies.push(0);
      resolve();
    });
    req.write(data);
    req.end();
  });
}

// ── Worker loop ─────────────────────────────────────────────────────

async function worker(endTime) {
  while (Date.now() < endTime) {
    await sendRequest();
  }
}

// ── Progress reporting ──────────────────────────────────────────────

function reportProgress(elapsed) {
  const rps = stats.total / (elapsed / 1000);
  const recent = stats.latencies.slice(-100);
  const p50 = percentile(recent, 50).toFixed(1);
  const p99 = percentile(recent, 99).toFixed(1);
  process.stdout.write(
    `\r  ${elapsed / 1000}s | ${stats.total} reqs | ${rps.toFixed(0)} rps | p50=${p50}ms p99=${p99}ms | errors=${stats.errors}`
  );
}

// ── Main ────────────────────────────────────────────────────────────

async function main() {
  console.log(`\n  OpenDQV Load Test`);
  console.log(`  Duration: ${DURATION}s | Concurrency: ${CONCURRENCY} | Target: ${API_URL}`);
  console.log(`  Payload mix: ${PAYLOADS.length} variants (single + batch)\n`);

  stats.startTime = Date.now();
  const endTime = stats.startTime + DURATION * 1000;

  // Progress ticker
  const ticker = setInterval(() => {
    const elapsed = Date.now() - stats.startTime;
    reportProgress(elapsed);

    // Snapshot every 10s
    if (Math.floor(elapsed / 10000) > stats.intervalStats.length) {
      stats.intervalStats.push({
        elapsed_s: Math.floor(elapsed / 1000),
        total: stats.total,
        rps: (stats.total / (elapsed / 1000)).toFixed(1),
        p50: percentile(stats.latencies.slice(-200), 50).toFixed(1),
        p95: percentile(stats.latencies.slice(-200), 95).toFixed(1),
        p99: percentile(stats.latencies.slice(-200), 99).toFixed(1),
        errors: stats.errors,
      });
    }
  }, 1000);

  // Launch workers
  const workers = [];
  for (let i = 0; i < CONCURRENCY; i++) {
    workers.push(worker(endTime));
  }
  await Promise.all(workers);
  clearInterval(ticker);

  // Final report
  const totalTime = (Date.now() - stats.startTime) / 1000;
  const rps = stats.total / totalTime;

  console.log(`\n\n  ── Results ──────────────────────────────────────`);
  console.log(`  Duration:     ${totalTime.toFixed(1)}s`);
  console.log(`  Concurrency:  ${CONCURRENCY}`);
  console.log(`  Total reqs:   ${stats.total}`);
  console.log(`  Throughput:   ${rps.toFixed(1)} req/s`);
  console.log(`  Success:      ${stats.success} (${((stats.success / stats.total) * 100).toFixed(1)}%)`);
  console.log(`  Errors:       ${stats.errors}`);
  console.log(`  Status codes: ${JSON.stringify(stats.statusCodes)}`);
  console.log(``);
  console.log(`  Latency (ms):`);
  console.log(`    min:  ${percentile(stats.latencies, 0).toFixed(1)}`);
  console.log(`    p50:  ${percentile(stats.latencies, 50).toFixed(1)}`);
  console.log(`    p90:  ${percentile(stats.latencies, 90).toFixed(1)}`);
  console.log(`    p95:  ${percentile(stats.latencies, 95).toFixed(1)}`);
  console.log(`    p99:  ${percentile(stats.latencies, 99).toFixed(1)}`);
  console.log(`    max:  ${percentile(stats.latencies, 100).toFixed(1)}`);

  if (stats.intervalStats.length > 0) {
    console.log(`\n  ── Timeline (10s intervals) ─────────────────────`);
    console.log(`  ${"Time".padEnd(8)} ${"Total".padEnd(8)} ${"RPS".padEnd(8)} ${"p50".padEnd(8)} ${"p95".padEnd(8)} ${"p99".padEnd(8)} Errors`);
    for (const s of stats.intervalStats) {
      console.log(`  ${(s.elapsed_s + "s").padEnd(8)} ${String(s.total).padEnd(8)} ${s.rps.padEnd(8)} ${s.p50.padEnd(8)} ${s.p95.padEnd(8)} ${s.p99.padEnd(8)} ${s.errors}`);
    }
  }

  console.log(`\n`);
}

main().catch(console.error);
