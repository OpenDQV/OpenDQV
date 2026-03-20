# Federation

> Last reviewed: 2026-03-17

OpenDQV federation is a multi-node contract synchronisation mechanism. An **authority node** holds the canonical contract set; **child nodes** receive push notifications whenever contracts change and apply those changes locally under a two-phase commit protocol.

In standalone mode (the default) federation env vars are unset and all federation endpoints return local state — no upstream calls are made. A node switches to federated mode automatically when `OPENDQV_UPSTREAM` is set.

---

## What federation is

A federated deployment consists of two or more OpenDQV nodes where one node acts as the authority. The authority node:

- Owns the canonical version of each data contract
- Initiates all contract change propagation
- Receives acknowledgements from child nodes before committing

Child nodes:

- Receive contract change notifications from the authority
- Apply changes locally and acknowledge receipt
- Record every event in their local federation log
- Continue operating (with a warning) if they lose upstream contact, up to `OPENDQV_MAX_ISOLATION_HOURS`

The federation log (`core/federation.py`) is an append-only SQLite table — every event is recorded with a monotonically increasing log sequence number (LSN). Child nodes use the LSN as a replication cursor: "give me all events since `lsn=N`".

---

## Event types

There are seven event types in the federation protocol. All are recorded in the `federation_log` table.

| Event type | Direction | Description |
|---|---|---|
| `push` | authority → child | Authority pushes a contract change. Recorded with `status=pending` until the child acks. |
| `pull` | child → authority | Child pulls from upstream (used in pull-mode sync). |
| `ack` | child → authority | Child has validated and applied the pushed contract locally. Transitions the push event to `status=ack`. |
| `commit` | authority → child | Authority has received acks from all expected children and commits the change as final. |
| `reject` | authority → child | Push is rejected — either because of a constraint violation at the child, or because the authority timed out waiting for acks. Recorded with `status=rejected`. |
| `isolation_start` | local | Node has lost contact with its upstream. Recorded locally when the health monitor detects the upstream is unreachable. |
| `isolation_end` | local | Node has regained upstream contact. Paired with the corresponding `isolation_start` event. |

Valid statuses on federation log events: `pending`, `ack`, `committed`, `rejected`.

---

## Two-phase commit flow

### Happy path (push accepted)

```
Authority                     Child Node
    |                              |
    |-- push (status=pending) ---->|
    |                              | (validates locally, applies contract)
    |<-- ack ----------------------|
    |   (authority updates push    |
    |    event status → ack)       |
    |                              |
    |-- commit ------------------->|
    |                              | (change is committed)
```

1. Authority inserts a `push` event with `status=pending` into its federation log.
2. Child receives the push, validates that the incoming contract does not violate any local invariant, and applies it locally.
3. Child sends an `ack`. The authority updates the push event status to `ack`.
4. Once all expected child nodes have acked, the authority inserts a `commit` event. The change is now final on all nodes.

### Rejection path

```
Authority                     Child Node
    |                              |
    |-- push (status=pending) ---->|
    |                              | (validation fails or timeout)
    |<-- reject -------------------|
    |                              |
    |-- reject event recorded ---->|
    |   (status=rejected)          |
    | (governance alert raised)    |
```

A push is rejected when:
- The child detects a constraint violation (e.g. the incoming contract would loosen an inherited invariant that the local governance policy forbids)
- The authority times out waiting for ack

On rejection, the authority records a `reject` event with `status=rejected` and raises a governance alert. The child's local contract is **not** updated; it stays at the last committed version.

---

## API endpoints

All federation endpoints are under `/api/v1/federation/`. They require authentication (see `AUTH_MODE`).

---

### `POST /federation/register`

Register this node with an upstream authority node.

> **OSS tier note:** This endpoint returns HTTP 501 in the OSS tier. Node registration — join token validation, topology recording, and contract bootstrapping — is part of the enterprise federation tier. In the OSS tier, set `OPENDQV_UPSTREAM` and `OPENDQV_JOIN_TOKEN` environment variables and restart; bootstrap happens automatically on startup.

**Example response (501):**
```json
{
  "detail": {
    "error": "federation_not_enabled",
    "message": "Node registration requires the enterprise federation tier. In the OSS tier, set OPENDQV_UPSTREAM and OPENDQV_JOIN_TOKEN environment variables for automatic bootstrap on startup.",
    "docs": "https://opendqv.io/enterprise"
  }
}
```

---

### `GET /federation/status`

Return the federation status of this node.

**Example response:**
```json
{
  "opendqv_node_id": "eu-west-1",
  "is_federated": true,
  "upstream_url": "https://authority.example.com:8000",
  "opendqv_node_state": "online",
  "audit_mode": "basic",
  "contracts_loaded": 12,
  "time_in_state_seconds": 3842,
  "isolated_since": null
}
```

| Field | Description |
|---|---|
| `opendqv_node_id` | Node identity (from `OPENDQV_NODE_ID`, defaults to hostname) |
| `is_federated` | `true` when `OPENDQV_UPSTREAM` is set |
| `upstream_url` | Authority node URL, or `null` in standalone mode |
| `opendqv_node_state` | Current health state: `online`, `degraded`, or `isolated` |
| `time_in_state_seconds` | Seconds the node has been in the current state |
| `isolated_since` | ISO timestamp when isolation began, or `null` |

---

### `GET /federation/log?since=N&contract=name`

Return federation log events since a given LSN. Used as a replication cursor by child nodes replaying history after a reconnect.

**Query parameters:**

| Parameter | Default | Description |
|---|---|---|
| `since` | `0` | Return events with `lsn > N`. Use the highest `lsn` from the previous response to advance the cursor. |
| `contract` | *(all)* | Optional. Filter events to a specific contract name. |

**Example request:**
```
GET /api/v1/federation/log?since=42&contract=customer
```

**Example response:**
```json
{
  "opendqv_node_id": "eu-west-1",
  "since": 42,
  "count": 2,
  "events": [
    {
      "lsn": 43,
      "event_type": "push",
      "contract_name": "customer",
      "contract_version": "1.1",
      "source_node": "authority-primary",
      "target_node": "eu-west-1",
      "payload": {},
      "status": "pending",
      "created_at": "2026-03-17T10:15:00+00:00"
    },
    {
      "lsn": 44,
      "event_type": "commit",
      "contract_name": "customer",
      "contract_version": "1.1",
      "source_node": "authority-primary",
      "target_node": "eu-west-1",
      "payload": {},
      "status": "committed",
      "created_at": "2026-03-17T10:15:02+00:00"
    }
  ]
}
```

Results are ordered by `lsn` ascending. A `target_node` of `null` means the event was a broadcast to all children.

---

### `GET /federation/health`

Return detailed node health data for the federation control plane. Includes current node state, state transition log, open isolation events, and recent isolation history.

**Query parameters:**

| Parameter | Default | Description |
|---|---|---|
| `log_limit` | `20` | Maximum health log entries to return |

**Example response:**
```json
{
  "opendqv_node_id": "eu-west-1",
  "opendqv_node_state": "online",
  "time_in_state_seconds": 3842,
  "isolated_since": null,
  "health_log": [],
  "open_isolation_events": [],
  "recent_isolation_events": []
}
```

The control plane dashboard polls this endpoint to surface stale or isolated nodes requiring governance review.

---

### `GET /federation/events` (SSE stream)

Server-Sent Events stream for real-time federation updates. Clients connect once and receive push notifications for new federation log events, node state transitions, and periodic heartbeat pings.

Use `GET /federation/log?since=N` after reconnect to replay any events missed during disconnection.

---

## Setting up a federation pair

The following steps assume you have two OpenDQV instances running: an authority node and a child node.

**Step 1 — Configure the child node environment**

```bash
export OPENDQV_NODE_ID=child-node-1
export OPENDQV_UPSTREAM=https://authority.example.com:8000
export OPENDQV_JOIN_TOKEN=<token-issued-by-authority>
```

Restart the child node. Bootstrap happens automatically on startup when these variables are present.

**Step 2 — Verify federation status on the child**

```bash
curl -s http://localhost:8000/api/v1/federation/status \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Confirm `is_federated: true` and `opendqv_node_state: "online"`.

**Step 3 — Trigger a push from the authority**

Make a contract change on the authority node (e.g. add a rule via the API or the Streamlit UI). The authority records a `push` event in its federation log and propagates the change to registered children.

**Step 4 — Verify the federation log on the child**

```bash
curl -s "http://localhost:8000/api/v1/federation/log?since=0" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

You should see `push` and `commit` events for the changed contract.

**Step 5 — Check federation health**

```bash
curl -s http://localhost:8000/api/v1/federation/health \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Confirm `opendqv_node_state: "online"` and `open_isolation_events: []`.

---

## Isolation handling

A node enters isolation when it loses contact with its upstream authority. This is recorded as an `isolation_start` event in the federation log.

### What `isolation_start` means operationally

- The node detected that its upstream (`OPENDQV_UPSTREAM`) is unreachable
- The node transitions to `isolated` state in the health state machine
- `isolated_since` is set in `/federation/status` and `/federation/health`
- A governance alert is raised
- The node continues to serve validation requests using the last committed contract set

### What a node should do when it receives `isolation_start`

1. Do not stop serving validation — the last committed contract version remains valid
2. Monitor `GET /federation/health` for `open_isolation_events`
3. Investigate upstream connectivity (network partition, authority node restart, DNS)
4. Check `OPENDQV_MAX_ISOLATION_HOURS` — after this window, even fail-safe-open nodes stop accepting validation requests until they reconnect (default: 72 hours)

### What `isolation_end` means operationally

- The node has re-established contact with its upstream
- The node transitions back to `online` (or `degraded` if there are pending unsynced changes)
- The `isolation_end` event is written to the federation log, paired with the earlier `isolation_start`
- The node re-syncs any contract changes that occurred while it was isolated

The `isolation_start` / `isolation_end` pair forms the compliance record for the isolation period and is visible in `GET /federation/health` under `recent_isolation_events`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENDQV_NODE_ID` | hostname | Identity of this node. Set to a meaningful name (e.g. `eu-west-1`, `singapore-prod`) for readable federation logs. |
| `OPENDQV_UPSTREAM` | *(unset)* | URL of the authority node. Setting this variable switches the node into federated mode. |
| `OPENDQV_JOIN_TOKEN` | *(unset)* | Join token issued by the authority node. Required for authenticated bootstrap. |
| `OPENDQV_MAX_ISOLATION_HOURS` | `72` | How long a node may operate without upstream contact before it stops accepting validation requests. |
| `OPENDQV_MAX_SSE_CONNECTIONS` | `50` | Maximum concurrent `/federation/events` SSE clients per worker. Prevents long-lived connections from starving the validation pool. |

`OPENDQV_NODE_ID` is the only required configuration for a standalone node. All other federation variables are only relevant when `OPENDQV_UPSTREAM` is set.

---

## Current limitations

**Multi-parent federation is not yet implemented.** In v1.0, a child node can declare at most one upstream authority. Attempting to configure multiple upstreams is not validated and will produce undefined behaviour. Multi-parent federation (allowing a node to inherit rules from two or more authority nodes simultaneously) is on the roadmap as a P3 architectural direction item. See [docs/patterns/multi_parent_federation.md](patterns/multi_parent_federation.md) for the planned design and current workaround.

**No built-in conflict resolution — authority node wins.** When a push arrives, the authority's version of the contract replaces the child's local version. There is no merge or diff-based conflict resolution in v1.0.

**No automatic retry on push rejection.** If a push is rejected (constraint violation or timeout), the authority records a `reject` event and raises a governance alert. It does not automatically retry. Operators must investigate the rejection reason and re-initiate the push manually.

---

## See also

- [docs/patterns/federation_deprecation.md](patterns/federation_deprecation.md) — how authority nodes deprecate inherited rules with an auditable migration path
- [docs/patterns/multi_parent_federation.md](patterns/multi_parent_federation.md) — planned multi-authority architecture and current workaround
- [docs/runbook.md](runbook.md) — operational runbook including federation troubleshooting
