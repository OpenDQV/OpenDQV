# Federation

> Last reviewed: 2026-11-26

Federation is a design for multi-node contract synchronisation: an **authority node** holds the canonical contract set and **child nodes** apply its changes under a two-phase commit protocol. OpenDQV Core ships the record-keeping and read side of that design, not the synchronisation itself.

## What OpenDQV Core implements

| Implemented in OpenDQV Core | Not implemented in OpenDQV Core |
|---|---|
| The federation log table and its read API: `GET /federation/log`, the `GET /federation/events` SSE stream | Node registration — `POST /federation/register` always returns 501 |
| Node status and health: `GET /federation/status`, `GET /federation/health` (node state, state-transition log, isolation events) | Bootstrap from an upstream, or any call to `OPENDQV_UPSTREAM` |
| Contract version comparison with a peer: `GET /federation/sync-status?peer=<url>` | Sending, acknowledging or committing `push` / `ack` / `commit` / `reject` events between nodes |
| `is_federated: true` in `/federation/status` when `OPENDQV_UPSTREAM` is set | Detecting upstream loss: nothing in Core moves a node to `isolated` |
| | Stopping validation after `OPENDQV_MAX_ISOLATION_HOURS` |

All federation endpoints answer from local state only; the one outbound call is `sync-status` fetching `GET /api/v1/contracts` from the `peer` you pass. Setting `OPENDQV_UPSTREAM` marks the node as federated in `/federation/status` and nothing else.

The sections below describe the protocol the federation log is designed to record. Core stores and serves these events; it does not generate the cross-node ones itself.

---

## What federation is

A federated deployment consists of two or more OpenDQV Core nodes where one node acts as the authority. The authority node:

- Owns the canonical version of each data contract
- Initiates all contract change propagation
- Receives acknowledgements from child nodes before committing

Child nodes:

- Receive contract change notifications from the authority
- Apply changes locally and acknowledge receipt
- Record every event in their local federation log
- Continue operating (with a warning) if they lose upstream contact, up to `OPENDQV_MAX_ISOLATION_HOURS`

The federation log (`opendqv/core/federation.py`) is an append-only SQLite table — every event is recorded with a monotonically increasing log sequence number (LSN). Child nodes use the LSN as a replication cursor: "give me all events since `lsn=N`".

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

> **Note:** This endpoint always returns HTTP 501. Node registration (join token validation, topology recording, contract bootstrapping) is not implemented in OpenDQV Core, and there is no other way to register a node or bootstrap contracts from an upstream. Setting `OPENDQV_UPSTREAM` only marks the node as federated in `GET /federation/status`.

**Example response (501):**
```json
{
  "detail": {
    "error": "federation_not_enabled",
    "message": "Node registration is not implemented in OpenDQV Core. Setting OPENDQV_UPSTREAM only marks this node as federated in GET /federation/status; no registration or contract sync is performed.",
    "docs": "https://github.com/OpenDQV/OpenDQV/blob/main/docs/federation.md"
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

The example shows the event shape. Nothing in OpenDQV Core writes to the federation log, so on a Core node this endpoint returns `"count": 0` unless another process writes to the table.

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

The Federation page of the OpenDQV Workbench reads this endpoint.

---

### `GET /federation/events` (SSE stream)

Server-Sent Events stream for real-time federation updates. Clients connect once and receive push notifications for new federation log events, node state transitions, and periodic heartbeat pings.

Use `GET /federation/log?since=N` after reconnect to replay any events missed during disconnection.

---

<a id="setting-up-a-federation-pair"></a>

## Comparing two OpenDQV Core instances

OpenDQV Core cannot set up a federation pair: it does not register nodes, propagate contract changes or write `push` / `commit` events. What it can do is tell you whether two instances carry the same contract versions.

**Step 1 — Compare contract versions with a peer**

```bash
curl -s "http://localhost:8000/api/v1/federation/sync-status?peer=https://peer.example.com:8000" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

`diverged` lists every contract whose version differs (or that exists on only one side). The peer URL must be a public address — the SSRF guard refuses private, loopback and link-local addresses with 400. A peer that cannot be reached sets `peer_error`.

Run the call from CI on a schedule and fail the job when `diverged` is not empty to turn it into a drift check. (`sync-status` also emits a `sync_diverged` notification, but `sync_diverged` is not one of the webhook event types you can register for, so no webhook receives it.)

**Step 2 — Check node health**

```bash
curl -s http://localhost:8000/api/v1/federation/health \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Confirm `opendqv_node_state: "online"` and `open_isolation_events: []`.

---

## Isolation handling

> **In OpenDQV Core nothing detects upstream loss**, so a node never enters `isolated` on its own and no isolation events are written in normal running. This section describes what the isolation records mean when a transition is recorded.

A node enters isolation when it loses contact with its upstream authority. The node health state machine records the transition, and the isolation log opens an isolation episode (shown under `open_isolation_events` in `/federation/health`).

### What `isolation_start` means operationally

- The node detected that its upstream (`OPENDQV_UPSTREAM`) is unreachable
- The node transitions to `isolated` state in the health state machine
- `isolated_since` is set in `/federation/status` and `/federation/health`
- The node continues to serve validation requests using the last committed contract set

### What a node should do when it receives `isolation_start`

1. Do not stop serving validation — the last committed contract version remains valid
2. Monitor `GET /federation/health` for `open_isolation_events`
3. Investigate upstream connectivity (network partition, authority node restart, DNS)
4. Check `OPENDQV_MAX_ISOLATION_HOURS` (default: 72 hours) — an isolation episode longer than this is flagged `exceeded_threshold` in the isolation record. OpenDQV Core does not stop validating when it is exceeded.

### What `isolation_end` means operationally

- The node has re-established contact with its upstream
- The node transitions back to `online` (or `degraded` if there are pending unsynced changes)
- The isolation log closes the open episode

The closed episode forms the compliance record for the isolation period and is visible in `GET /federation/health` under `recent_isolation_events`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENDQV_NODE_ID` | hostname | Identity of this node. Set to a meaningful name (e.g. `eu-west-1`, `singapore-prod`) for readable federation logs. |
| `OPENDQV_UPSTREAM` | *(unset)* | URL of the authority node. Sets `is_federated: true` and `upstream_url` in `/federation/status`; Core makes no call to it. |
| `OPENDQV_JOIN_TOKEN` | *(unset)* | Reported as `join_token_set` in `/config`; not otherwise used (registration and bootstrap are not implemented). |
| `OPENDQV_MAX_ISOLATION_HOURS` | `72` | Stored on each isolation record (`max_allowed_seconds`) to flag long episodes. Core does not stop validating when it is exceeded. |
| `OPENDQV_MAX_SSE_CONNECTIONS` | `50` | Maximum concurrent `/federation/events` SSE clients per worker. Prevents long-lived connections from starving the validation pool. |

`OPENDQV_NODE_ID` is the only required configuration for a standalone node. All other federation variables are only relevant when `OPENDQV_UPSTREAM` is set.

---

## Current limitations

**Synchronisation is not implemented in OpenDQV Core.** See [What OpenDQV Core implements](#what-opendqv-core-implements): there is no registration, bootstrap, push propagation, acknowledgement, commit, conflict resolution, retry or isolation detection. Keep instances aligned by deploying the same contract files to each (for example from one Git repository) and use `GET /federation/sync-status` to check them.

**Multi-parent federation is not implemented either.** See [docs/patterns/multi_parent_federation.md](patterns/multi_parent_federation.md) for the design and the current workaround.

---

## See also

- [docs/patterns/federation_deprecation.md](patterns/federation_deprecation.md) — how authority nodes deprecate inherited rules with an auditable migration path
- [docs/patterns/multi_parent_federation.md](patterns/multi_parent_federation.md) — planned multi-authority architecture and current workaround
- [docs/runbook.md](runbook.md) — operational runbook including federation troubleshooting
