# Salesforce Integration

> **Correction (September 2026): a synchronous HTTP callout from an Apex trigger does not run.**
> Salesforce refuses it before the request leaves the platform, with
> `System.CalloutException: Callout from triggers are currently not supported`. This held for every
> entry point tried: a save from the standard UI (May 2026), and a Flow's Create Records element,
> anonymous Apex, Bulk API 2.0 and a REST insert (September 2026). The trigger-callout approach this
> guide previously presented as tested and working could not be reproduced with a trigger; a Screen
> Flow calling the validation endpoint (or an older API version) is the likely explanation for the
> March 2026 result it rested on. That section is kept for the record and must not be used.
> For blocking at write time, see [Blocking a save at write time](#blocking-a-save-at-write-time);
> for validating saves from any source after the fact, see
> [Auditing saves from any source](#auditing-saves-from-any-source).

OpenDQV integrates with Salesforce in three ways: rules compiled into an Apex class that runs inside
the trigger (no callout), a Screen Flow or Lightning component that calls the validation endpoint
before the record is created, and an after-save audit that validates every record from any source.

---

## Quick start — validate a Contact in 5 minutes

### 1. Use the built-in salesforce_contact contract

OpenDQV ships `opendqv/contracts/salesforce_contact.yaml` (v1.1, 19 production-grade validation rules) out of the box — no setup required. The bundled contract carries no `contexts:` block; the worked context example below is `examples/contexts/salesforce_contact.yaml`, which you can copy over the bundled file (or into your own contracts directory) to enable `salesforce_prod` / `salesforce_sandbox`. To write your own:

```yaml
contract:
  name: salesforce_contact
  version: "1.0"
  description: "Salesforce Contact quality validation"
  owner: "Data Governance Team"
  status: active

  rules:
    - name: first_name_required
      field: FirstName
      type: not_empty
      severity: error
      error_message: "FirstName is required."

    - name: email_format
      field: Email
      type: regex
      pattern: "^[\\w\\.\\+\\-]+@[\\w\\.-]+\\.[a-zA-Z]{2,}$"
      severity: error
      error_message: "Email must be a valid format."

    - name: birthdate_format
      field: Birthdate
      type: date_format
      severity: error
      error_message: "Birthdate must be YYYY-MM-DD."

  contexts:
    salesforce_prod:
      Birthdate:
        min_age: 18
        max_age: 150
        error_message: "Contact must be 18+ in production."

    salesforce_sandbox:
      Email:
        type: regex
        pattern: "^.*@(example\\.com|test\\.com)$"
        error_message: "Sandbox contacts must use test email domains."
```

### 2. Reload contracts

```bash
curl -X POST http://localhost:8000/api/v1/contracts/reload
```

### 3. Validate a record

```bash
# Production context — enforces 18+ age (requires the examples/contexts contract;
# against the bundled contract the context is undeclared and the response
# carries a context_warning and applies base rules only)
curl -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -d '{
    "record": {"FirstName": "Sarah", "Email": "sarah@acme.com", "Birthdate": "2015-03-15"},
    "contract": "salesforce_contact",
    "context": "salesforce_prod"
  }'
```

Response (blocked — contact is under 18; abridged, the envelope also carries `event_id`, `contract_hash`, `engine_version` etc.):
```json
{
  "valid": false,
  "errors": [
    {"field": "LastName", "rule": "last_name_required", "message": "LastName is required.", "severity": "error",
     "error_code": "OPENDQV_NOT_EMPTY_LAST_NAME_REQUIRED", "suggested_fix": "Provide a non-empty value.", "counterpart_missing": null},
    {"field": "Birthdate", "rule": "birthdate_format", "message": "Contact must be 18+ in production.", "severity": "error",
     "error_code": "OPENDQV_DATE_FORMAT_BIRTHDATE_FORMAT", "suggested_fix": "Use ISO 8601 format: YYYY-MM-DD (e.g. 2026-03-24)", "counterpart_missing": null},
    {"field": "AccountId", "rule": "account_id_not_empty", "message": "AccountId is required in production — no orphan contacts allowed.", "severity": "error",
     "error_code": "OPENDQV_NOT_EMPTY_ACCOUNT_ID_NOT_EMPTY", "suggested_fix": "Provide a non-empty value.", "counterpart_missing": null}
  ],
  "warnings": [{"field": "MailingStreet", "rule": "mailing_street_required", "message": "MailingStreet is recommended for contacts.", "severity": "warning", "error_code": "OPENDQV_NOT_EMPTY_MAILING_STREET_REQUIRED", "suggested_fix": "Provide a non-empty value.", "counterpart_missing": null}],
  "contract": "salesforce_contact",
  "version": "1.1",
  "engine_version": "<engine-version>"
}
```

### 4. Wire into a trigger

```apex
// Phase 1 — push-down (no callout required):
Map<String, Object> data = new Map<String, Object>{
    'FirstName' => contact.FirstName,
    'Email'     => contact.Email,
    'Birthdate' => String.valueOf(contact.Birthdate)
};
if (!OpenDQVValidator.validateRecord(data, 'salesforce_contact')) {
    contact.addError('Record failed data quality validation');
}
```

Use the **Integration Guide** tab in the Streamlit UI to generate ready-to-paste Apex, JavaScript, Python, cURL, Power Automate, and GraphQL snippets.

For the push-down (Approach 1) pattern and the write-time and after-save patterns that call the
endpoint, read on.

---

## The three patterns

| | Push-down Apex (Approach 1) | Screen Flow / component calling the endpoint | After-save audit |
|---|---|---|---|
| Where validation runs | Inside the trigger, no callout | Before `Create Records`, in the UI path you control | `@future` after the save |
| Blocks the save? | Yes, for every source | Yes, for saves through that flow or component only | No — the record is already saved |
| Stays in sync with the contract? | No — a snapshot; regenerate on change | Yes | Yes |
| Reaches OpenDQV's audit log? | No | Yes | Yes |

Approach 1 was tested end-to-end in a Salesforce Developer Edition org (2026-03-17): class deployed,
FAIL and PASS cases confirmed in a Before Insert trigger. The trigger-callout pattern that used to
sit beside it does not run on the platform — see the correction at the top of this page.

---

## Approach 1: Push-down Apex

### How it works

OpenDQV generates an Apex class (`OpenDQVValidator`) that encodes your contract rules as native Salesforce logic. You deploy it once and wire it into a trigger. No callout, no API key, no latency overhead.

The trade-off: it's a **snapshot**. If the contract changes, the deployed class doesn't update automatically — you have to re-generate and re-deploy.

### Generate the Apex class

```bash
# From CLI
opendqv generate <contract-name> salesforce

# With context filter (e.g. only salesforce-tagged rules)
opendqv generate <contract-name> salesforce --context salesforce

# Via API
GET /contracts/<contract-name>/generate?target=salesforce
```

The generated class includes a header that tells you exactly which contract version and timestamp it was built from:

```apex
// Generated by OpenDQV — push-down validation snapshot
// Contract: customer v1.2 | Generated: 2026-03-16T10:00:00Z
// SYNC REMINDER: This class is a snapshot of the contract rules at generation
// time. Re-run if the contract has been updated:
//   opendqv generate customer salesforce
// For live governance (always in sync), see the HTTP callout integration.
```

### Deploy steps

1. Copy the generated Apex into a new file: `force-app/main/default/classes/OpenDQVValidator.cls`
2. Deploy to your org:
   ```bash
   sf project deploy start --source-dir force-app/main/default/classes/OpenDQVValidator.cls
   ```
3. Wire it into a trigger (see below).

### Trigger wiring example

```apex
trigger AccountDQVTrigger on Account (before insert, before update) {
    List<Map<String, Object>> records = new List<Map<String, Object>>();
    for (Account a : Trigger.new) {
        records.add(new Map<String, Object>{
            'name'  => a.Name,
            'email' => a.Email__c,
            'age'   => a.Age__c
        });
    }

    List<Map<String, Object>> results = OpenDQVValidator.validate(records);
    for (Integer i = 0; i < Trigger.new.size(); i++) {
        Map<String, Object> result = results[i];
        List<Object> errors = (List<Object>) result.get('errors');
        List<Object> warnings = (List<Object>) result.get('warnings');
        if (!errors.isEmpty()) {
            Trigger.new[i].addError('OpenDQV validation failed: ' + errors);
        }
        if (!warnings.isEmpty()) {
            System.debug('OpenDQV warnings for ' + Trigger.new[i].LastName + ': ' + warnings);
        }
    }
}
```

> **Sync reminder:** The deployed `OpenDQVValidator` class is a snapshot. If your contract rules change, re-run `opendqv generate <contract-name> salesforce` and redeploy. The generation header shows the timestamp — use it to audit whether the deployed class is current.

### Governor limits note

Regex rules consume CPU time in the Apex execution context. For contracts with many complex regex patterns, profile against Salesforce governor limits in a sandbox before deploying to production. For high-cardinality regex-heavy contracts, Approach 2 (HTTP callout) offloads the compute to OpenDQV's API.

---

## When to upgrade to Approach 2

Use this decision matrix to assess whether push-down Apex is still the right fit:

| Question | Stay on Approach 1 | Consider Approach 2 |
|----------|----------------|-----------------|
| How often does the contract change? | Rarely (monthly or less) | Frequently (weekly or more) |
| Who owns the Salesforce deployment process? | You / your team | Separate release team with long cycles |
| Can you tolerate a sync window between contract update and Salesforce enforcement? | Yes, a day or two is fine | No — must be instant |
| Are your contracts simple (not regex-heavy)? | Yes | No — regex-heavy, governor limits are a concern |
| Do you validate across multiple Salesforce orgs? | No | Yes |
| Is audit traceability to the live contract version required? | Nice to have | Hard requirement |
| Do you have a network path from Salesforce to OpenDQV? | Not yet | Yes (or willing to set it up) |
| Are you already running OpenDQV in production with uptime SLAs? | No | Yes |

If you answered "Consider Approach 2" to three or more of these, read on.

---

## Calling the validation endpoint from a trigger (does not work — kept for the record)

**Observed.** In a Salesforce Developer Edition org on the Summer '26 release (API 67.0), a
`before insert` trigger that made a synchronous `Http().send()` to the validation endpoint was exercised
through four entry points on 26 September 2026: a Flow with a Create Records element, anonymous Apex,
Bulk API 2.0 with 250 rows, and a REST sObject insert. All four threw
`System.CalloutException: Callout from triggers are currently not supported`, 250 of 250 bulk rows
included. No request reached the endpoint. An earlier test in May 2026, in a different Developer Edition
org, gave the same error for a save from the standard UI and for Apex DML. We have not seen an org in
which the callout runs; which editions or API versions might allow it is an open question with no
evidence either way.

**What happens to the record is decided only by the trigger's exception handling:**

| Trigger shape | Effect on the save |
|---|---|
| No `try`/`catch` | Every save of that object fails (`CANNOT_INSERT_UPDATE_ACTIVATE_ENTITY … caused by: System.CalloutException`), valid data or not. |
| `catch` that only logs (`System.debug`) — the shape this guide previously showed | The record saves **unvalidated**. Nothing is logged where anyone looks; the validation service never sees the record. |
| `catch` that calls `addError` | Every save is blocked by the platform error, never by a verdict. |

None of these validates anything. Do not ship a trigger with a synchronous callout. The `OpenDQVCallout`
class, the Before-trigger wiring, the ngrok and Remote Site steps and the governor-limit note that this
guide used to carry for that pattern have been removed; a Named Credential is still the right way to
hold the endpoint URL and token for the patterns below.

---

## Blocking a save at write time

Salesforce lets a save be gated synchronously only where the record is created through something you
control, in the user interface:

1. **Screen Flow + External Service.** Register an OpenAPI description of the validation endpoint as an
   External Service (Setup → External Services → From API Specification), call the validate action from a
   Screen Flow, and branch on the response: proceed to Create Records only on a clear pass (`valid` is
   `true`, or the response's `mode` is `observation_only`); otherwise show the errors and stop. Flow
   Builder only, no code. In a trial org that could not deploy Apex, an External Service action in a
   flow (an autolaunched flow run through the REST API) worked. Give Create Records its own fault
   path: Salesforce's own checks (an invalid email address, a duplicate rule) can still refuse the save
   after validation passed. Flow Builder renames request fields (every `_` shows as `x5f`), but the call
   still sends the contract's names.

   **Use the spec OpenDQV ships for this, not the served one.** The API serves OpenAPI **3.1.0** at
   `/openapi.json`; External Services registration accepts OpenAPI 2.0 and 3.0 documents. OpenDQV ships
   [`docs/salesforce/opendqv-validate-openapi-3.0.json`](salesforce/opendqv-validate-openapi-3.0.json) — an
   OpenAPI 3.0.3 description of `POST /api/v1/validate` alone, with the request fields (`contract`,
   `record`, `record_id`, `context`, `observe_only`) and the response fields the flow branches on (`valid`,
   `mode`, `would_have_failed`, `errors`, `warnings`, `record_id`). Replace the `servers` URL with your
   endpoint before uploading. A test pins the spec to the fields the API actually accepts and returns.

2. **Lightning Web Component + `@AuraEnabled` Apex.** The component calls an Apex method that makes the
   callout, and inserts the record only on a pass. Needs an edition that can deploy Apex. Set
   `req.setTimeout(120000)` (the Apex maximum); the default 10 s is too short for a service that is slow
   to respond or under load, and the call then fails with `Read timed out`.

**Who can run it.** Test with a user who is not an administrator. In our test (Developer Edition,
September 2026) a Standard User could not open the Screen Flow at all ("Insufficient Privileges") until
they had the **Run Flows** permission (Salesforce also accepts the Flow User setting on the user); access
granted to that one flow alone was not enough (with the flow's "restrict access to enabled profiles or
permission sets" option off; not tested with it on). The user also needs access to the External Credential's
principal (External Credential Principal Access in a permission set): without it the call fails with
"We couldn't access the credential(s). You might not have the required permissions…". One permission set
granting both is the simplest set-up. For the Lightning Web Component pattern the user needs access to
the Apex class (Apex Class Access in a permission set) as well as the credential access; without it the
component reports "You do not have access to the Apex class named …". Run Flows is not needed for that
pattern: it saved a record for a user with no Run Flows and no Flow User setting.

**State and country fields.** In an org with State and Country/Territory picklists enabled, Salesforce
refused a save whose `MailingState` was set without a country (`FIELD_INTEGRITY_EXCEPTION: A
country/territory must be specified before specifying a state value`). With picklists enabled, the state
and country fields take picklist values; an ISO code such as `GB` belongs in `MailingCountryCode`.

**Error emails.** Salesforce emails the flow's administrator whenever an element fails, even when the
flow's fault path handles the error and shows the user the reason. The email lists every value the user
entered.

Place the flow or component where users create these records (a button, a quick action, a Lightning
page) and remove the standard **New** button from those layouts. Saves that bypass the UI — API loads,
Data Loader, integrations, inline and list-view edits, the standard Edit button, quick actions not
routed through your flow or component — go through triggers and cannot be blocked this way.

---

## Auditing saves from any source

For inserts and updates from any source, an `after insert, after update` trigger can hand the records to
an `@future(callout=true)` method that calls the validation endpoint. The record is already saved when
the call runs; this validates after the fact and can never block. Skip re-entry from other asynchronous
Apex (`if (System.isFuture() || System.isBatch()) return;`). Note the platform's limit on `@future` calls
per transaction when large loads are expected.

**Tracing a verdict back to its record — the two routes differ.**

- `POST /api/v1/validate` (one record) accepts a `record_id` and echoes it on the response. Send the
  Salesforce Id there and every verdict carries it.
- `POST /api/v1/validate/batch` has **no per-record id**: `records` is a bare list and each entry in
  `results` is keyed by `index`, its zero-based position in the list you sent. Keep the Ids in a parallel
  list in the same order and join on `index`. Every batch result also carries its own `event_id`, the
  audit primary key for that record.

Each audited verdict lands in OpenDQV's audit trail with the contract version, hash and mode it ran
under — see the audit-events endpoints in the [API reference](api_reference.md).

---

## Validating inside the trigger with no callout

Approach 1 above — rules compiled from the contract into an Apex class with `opendqv generate
<contract> salesforce` and run locally in the trigger — makes no callout and is therefore unaffected by
the restriction. Its checks run inside Salesforce and never reach OpenDQV's audit log; regenerate the
class whenever the contract changes, or pair it with the after-save audit for a record of every verdict.

---

## The one-liner

> **Push-down Apex** is the fastest path to Salesforce-native enforcement for every save — zero
> infrastructure, deploys in minutes, drifts until you regenerate it.
> **A Screen Flow or component calling the endpoint** blocks at write time and stays in sync, for the
> saves that go through it.
> **The after-save audit** sees every save from every source, and never blocks.

All three use the same contracts. The difference is where validation runs and what it can stop.
