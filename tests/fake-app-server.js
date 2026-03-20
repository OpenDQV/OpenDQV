/**
 * Fake CRM App Server — simulates a source system integrating with OpenDQV.
 *
 * Runs on port 3000. Provides a simple UI to submit customer records
 * and see real-time validation results from the OpenDQV API.
 *
 * Usage: node tests/fake-app-server.js
 */

const http = require("http");
const https = require("https");
const { URL } = require("url");

const OPENDQV_URL = process.env.OPENDQV_URL || "http://localhost:8000";
const PORT = 3000;

// ── OpenDQV client ──────────────────────────────────────────────────

function callOpenDQV(path, body) {
  return new Promise((resolve, reject) => {
    const url = new URL(path, OPENDQV_URL);
    const lib = url.protocol === "https:" ? https : http;
    const payload = JSON.stringify(body);

    const req = lib.request(
      url,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload),
          "ngrok-skip-browser-warning": "true",
        },
      },
      (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode, body: JSON.parse(data) });
          } catch {
            resolve({ status: res.statusCode, body: data });
          }
        });
      }
    );
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

// ── HTML UI ─────────────────────────────────────────────────────────

const HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Fake CRM — OpenDQV Integration Test</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, system-ui, sans-serif; background: #f5f5f5; padding: 2rem; }
    h1 { margin-bottom: 0.5rem; }
    .subtitle { color: #666; margin-bottom: 2rem; }
    .container { max-width: 900px; margin: 0 auto; }
    .card { background: white; border-radius: 8px; padding: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 1.5rem; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
    label { display: block; font-weight: 600; margin-bottom: 0.3rem; font-size: 0.9rem; }
    input, select { width: 100%; padding: 0.5rem; border: 1px solid #ddd; border-radius: 4px; font-size: 0.95rem; }
    input:focus, select:focus { outline: none; border-color: #4a90d9; }
    .btn-row { margin-top: 1rem; display: flex; gap: 0.75rem; }
    button { padding: 0.6rem 1.5rem; border: none; border-radius: 4px; font-size: 0.95rem; cursor: pointer; font-weight: 600; }
    .btn-primary { background: #4a90d9; color: white; }
    .btn-primary:hover { background: #357abd; }
    .btn-secondary { background: #e0e0e0; color: #333; }
    .btn-batch { background: #7c3aed; color: white; }
    .btn-batch:hover { background: #6d28d9; }
    #result { margin-top: 1rem; }
    .result-pass { background: #d4edda; border: 1px solid #c3e6cb; border-radius: 6px; padding: 1rem; }
    .result-fail { background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 6px; padding: 1rem; }
    .result-info { background: #d1ecf1; border: 1px solid #bee5eb; border-radius: 6px; padding: 1rem; }
    .error-item { padding: 0.3rem 0; font-size: 0.9rem; }
    .error-field { font-weight: 600; color: #c0392b; }
    .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 3px; font-size: 0.8rem; font-weight: 600; }
    .badge-error { background: #e74c3c; color: white; }
    .badge-warning { background: #f39c12; color: white; }
    pre { background: #2d2d2d; color: #f8f8f2; padding: 1rem; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; margin-top: 0.5rem; }
    .context-info { font-size: 0.85rem; color: #666; margin-top: 0.3rem; }
    .presets { margin-bottom: 1rem; }
    .presets button { padding: 0.4rem 0.8rem; font-size: 0.8rem; margin-right: 0.5rem; background: #f0f0f0; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; }
    .presets button:hover { background: #e0e0e0; }
    .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid #ccc; border-top-color: #333; border-radius: 50%; animation: spin 0.6s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .api-url { font-family: monospace; font-size: 0.85rem; color: #888; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Fake CRM App</h1>
    <p class="subtitle">Simulates a source system calling OpenDQV for data validation</p>
    <p class="api-url">OpenDQV endpoint: <strong>${OPENDQV_URL}</strong></p>

    <div class="card" style="margin-top:1rem">
      <h3>New Customer Record</h3>
      <div class="presets" style="margin-top:0.75rem">
        <strong style="font-size:0.85rem">Quick fill:</strong>
        <button onclick="fillPreset('valid_adult')">Valid Adult</button>
        <button onclick="fillPreset('underage')">Underage (10yo)</button>
        <button onclick="fillPreset('teen')">Teen (17yo)</button>
        <button onclick="fillPreset('bad_data')">Bad Data</button>
        <button onclick="fillPreset('empty')">Clear</button>
      </div>
      <div class="form-grid" style="margin-top:0.75rem">
        <div>
          <label>First Name</label>
          <input id="first_name" value="Sunny">
        </div>
        <div>
          <label>Last Name</label>
          <input id="last_name" value="Sharma">
        </div>
        <div>
          <label>Email</label>
          <input id="email" type="email" value="sunny@example.com">
        </div>
        <div>
          <label>Date of Birth</label>
          <input id="date_of_birth" type="date" value="1990-05-15">
        </div>
        <div>
          <label>Context</label>
          <select id="context">
            <option value="">None (base rules)</option>
            <option value="salesforce" selected>Salesforce (18+)</option>
            <option value="kids_app">Kids App (5-17)</option>
            <option value="uk_region">UK Region</option>
          </select>
          <p class="context-info">Context applies different validation rules</p>
        </div>
        <div>
          <label>Record ID</label>
          <input id="record_id" value="CRM-001">
        </div>
      </div>
      <div class="btn-row">
        <button class="btn-primary" onclick="validateSingle()">Validate Record</button>
        <button class="btn-batch btn-batch" onclick="validateBatch()">Batch Test (5 records)</button>
      </div>
    </div>

    <div id="result"></div>
  </div>

  <script>
    const presets = {
      valid_adult: { first_name: 'Sunny', last_name: 'Sharma', email: 'sunny@example.com', date_of_birth: '1990-05-15', record_id: 'CRM-001' },
      underage:    { first_name: 'Alex', last_name: 'Young', email: 'alex@example.com', date_of_birth: '2015-06-01', record_id: 'CRM-002' },
      teen:        { first_name: 'Teen', last_name: 'User', email: 'teen@example.com', date_of_birth: '2009-01-15', record_id: 'CRM-003' },
      bad_data:    { first_name: '', last_name: 'X', email: 'not-an-email', date_of_birth: '', record_id: 'CRM-004' },
      empty:       { first_name: '', last_name: '', email: '', date_of_birth: '', record_id: '' },
    };

    function fillPreset(name) {
      const p = presets[name];
      Object.keys(p).forEach(k => document.getElementById(k).value = p[k]);
    }

    function getRecord() {
      return {
        first_name: document.getElementById('first_name').value,
        last_name: document.getElementById('last_name').value,
        email: document.getElementById('email').value,
        date_of_birth: document.getElementById('date_of_birth').value,
      };
    }

    async function validateSingle() {
      const el = document.getElementById('result');
      el.innerHTML = '<div class="card"><span class="spinner"></span> Validating...</div>';

      const body = {
        contract: 'customer_onboarding',
        version: '1.0',
        context: document.getElementById('context').value || undefined,
        record_id: document.getElementById('record_id').value || undefined,
        record: getRecord(),
      };

      try {
        const res = await fetch('/api/validate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        renderSingleResult(data);
      } catch (err) {
        el.innerHTML = '<div class="card result-fail">Error: ' + err.message + '</div>';
      }
    }

    async function validateBatch() {
      const el = document.getElementById('result');
      el.innerHTML = '<div class="card"><span class="spinner"></span> Running batch validation...</div>';

      const ctx = document.getElementById('context').value || undefined;
      const body = {
        contract: 'customer_onboarding',
        version: '1.0',
        context: ctx,
        records: [
          { first_name: 'Sunny', last_name: 'Sharma', email: 'sunny@example.com', date_of_birth: '1990-05-15' },
          { first_name: 'Alex', last_name: 'Young', email: 'alex@example.com', date_of_birth: '2015-06-01' },
          { first_name: 'Teen', last_name: 'User', email: 'teen@test.com', date_of_birth: '2009-01-15' },
          { first_name: '', last_name: 'X', email: 'bad', date_of_birth: '' },
          { first_name: 'Jane', last_name: 'Doe', email: 'jane@corp.com', date_of_birth: '1985-11-20' },
        ],
      };

      try {
        const res = await fetch('/api/validate/batch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        renderBatchResult(data);
      } catch (err) {
        el.innerHTML = '<div class="card result-fail">Error: ' + err.message + '</div>';
      }
    }

    function renderSingleResult(data) {
      const el = document.getElementById('result');
      const cls = data.valid ? 'result-pass' : 'result-fail';
      const icon = data.valid ? '&#10004;' : '&#10008;';
      let html = '<div class="card ' + cls + '">';
      html += '<h3>' + icon + ' ' + (data.valid ? 'PASSED' : 'FAILED') + '</h3>';
      html += '<p style="margin-top:0.5rem">Record: <strong>' + (data.record_id || 'N/A') + '</strong> | Contract: ' + data.contract + ' v' + data.version + '</p>';

      if (data.errors && data.errors.length > 0) {
        html += '<div style="margin-top:0.75rem"><strong>Errors:</strong>';
        data.errors.forEach(e => {
          html += '<div class="error-item"><span class="badge badge-error">ERROR</span> <span class="error-field">' + e.field + '</span>: ' + e.message + '</div>';
        });
        html += '</div>';
      }
      if (data.warnings && data.warnings.length > 0) {
        html += '<div style="margin-top:0.75rem"><strong>Warnings:</strong>';
        data.warnings.forEach(w => {
          html += '<div class="error-item"><span class="badge badge-warning">WARN</span> <span class="error-field">' + w.field + '</span>: ' + w.message + '</div>';
        });
        html += '</div>';
      }
      html += '</div>';
      html += '<div class="card"><strong>Raw API Response:</strong><pre>' + JSON.stringify(data, null, 2) + '</pre></div>';
      el.innerHTML = html;
    }

    function renderBatchResult(data) {
      const el = document.getElementById('result');
      const s = data.summary;
      let html = '<div class="card result-info">';
      html += '<h3>Batch Results: ' + s.passed + '/' + s.total + ' passed</h3>';
      html += '<p style="margin-top:0.5rem">' + s.failed + ' failed | ' + s.error_count + ' total errors | ' + s.warning_count + ' warnings</p>';
      html += '</div>';

      const names = ['Sunny Sharma', 'Alex Young', 'Teen User', '(empty) X', 'Jane Doe'];
      data.results.forEach((r, i) => {
        const cls = r.valid ? 'result-pass' : 'result-fail';
        const icon = r.valid ? '&#10004;' : '&#10008;';
        html += '<div class="card ' + cls + '" style="padding:1rem">';
        html += '<strong>' + icon + ' Record ' + r.index + '</strong> — ' + names[i];
        if (r.errors.length > 0) {
          r.errors.forEach(e => {
            html += '<div class="error-item"><span class="badge badge-error">ERROR</span> <span class="error-field">' + e.field + '</span>: ' + e.message + '</div>';
          });
        }
        html += '</div>';
      });

      html += '<div class="card"><strong>Raw API Response:</strong><pre>' + JSON.stringify(data, null, 2) + '</pre></div>';
      el.innerHTML = html;
    }
  </script>
</body>
</html>`;

// ── HTTP Server ─────────────────────────────────────────────────────

const server = http.createServer(async (req, res) => {
  // Proxy validation requests to OpenDQV
  if (req.method === "POST" && req.url === "/api/validate") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", async () => {
      try {
        const result = await callOpenDQV("/api/v1/validate", JSON.parse(body));
        res.writeHead(result.status, { "Content-Type": "application/json" });
        res.end(JSON.stringify(result.body));
      } catch (err) {
        res.writeHead(502, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: err.message }));
      }
    });
    return;
  }

  if (req.method === "POST" && req.url === "/api/validate/batch") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", async () => {
      try {
        const result = await callOpenDQV(
          "/api/v1/validate/batch",
          JSON.parse(body)
        );
        res.writeHead(result.status, { "Content-Type": "application/json" });
        res.end(JSON.stringify(result.body));
      } catch (err) {
        res.writeHead(502, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: err.message }));
      }
    });
    return;
  }

  // Serve the UI
  res.writeHead(200, { "Content-Type": "text/html" });
  res.end(HTML);
});

server.listen(PORT, () => {
  console.log(`\n  Fake CRM App running at http://localhost:${PORT}`);
  console.log(`  Proxying validation to ${OPENDQV_URL}\n`);
  console.log(`  Try these:`);
  console.log(`    1. Open http://localhost:${PORT} in your browser`);
  console.log(`    2. Use the preset buttons to fill sample data`);
  console.log(`    3. Click "Validate Record" or "Batch Test"\n`);
});
