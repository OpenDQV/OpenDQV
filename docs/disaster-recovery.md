# OpenDQV Disaster Recovery and Business Continuity

This document defines the recovery objectives, backup procedures, and continuity posture for OpenDQV deployments. It is intended for infrastructure operators and for risk assessors evaluating the service for use in data pipeline or integration contexts.

---

## What Data is Persisted

OpenDQV stores the following in `opendqv.db` (SQLite):

| Data type | Stored | Notes |
|-----------|--------|-------|
| API tokens (hashed) | Yes | Plaintext tokens are never stored |
| Webhook registrations | Yes | URLs and event type subscriptions |
| Contract version history | Yes | Full YAML snapshots per version |
| Federation events | Yes | Enterprise edition only |
| **Validation payloads** | **No** | Processed in-memory, discarded after response |

Contract YAML files are stored on the filesystem in the `contracts/` directory. These are the primary source of truth for validation behaviour and should be version-controlled in git.

Because validation payloads are never persisted, a compromise or loss of `opendqv.db` does not expose the data that was validated against the service.

---

## Recovery Objectives

| Objective | Target | Basis |
|-----------|--------|-------|
| RTO (Recovery Time Objective) | 15 minutes | Time to pull image, restore db file, and restart container on existing host infrastructure |
| RPO (Recovery Point Objective) | 24 hours | Based on a daily backup cadence; tokens and webhook registrations created since the last backup must be re-issued |

These targets assume a single-instance deployment where the host is available and a recent backup exists. They are not contractual SLAs for the community edition.

---

## Backup Procedure

### Database Backup

Run daily on the host. The simplest approach:

```bash
# On the host, with the container running (SQLite WAL mode is safe for file copy)
cp /path/to/opendqv.db /path/to/backups/opendqv.db.$(date +%Y%m%d)
```

Alternatively, copy from inside the container:

```bash
docker cp opendqv-api-1:/app/opendqv.db ./backups/opendqv.db.$(date +%Y%m%d)
```

Automate with a cron job or your infrastructure's scheduled task facility. Retain at least 7 daily backups.

### Contract File Backup

Contract YAML files in `contracts/` should be committed to git. The git repository is the backup for contract definitions. If contracts are edited directly on the host without committing, include the `contracts/` directory in your filesystem backup.

---

## Restore Procedure

1. Stop the running container:

```bash
docker compose down
```

2. Replace the database file with the backup:

```bash
cp /path/to/backups/opendqv.db.20260308 /path/to/opendqv.db
```

3. Restart the service:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

4. Verify health:

```bash
curl -s http://localhost:8000/health
```

5. Re-issue any API tokens that were created after the backup's timestamp. Existing tokens present in the restored database will continue to function.

---

## High Availability and Failover

The community edition has no built-in high availability. The in-memory rate limiter state is not shared between processes or instances. This means:

- **Single instance**: full service interruption if the host or container fails.
- **Multiple instances behind a load balancer**: possible, but rate limit counters are not synchronised. Use a Redis-backed rate limiter or enforce rate limits at the load balancer. See `SECURITY.md` for the per-worker rate limit multiplication caveat.

Horizontal scaling with a shared persistent state requires the enterprise Postgres backend. Contact the project maintainers for details.

**Recommended mitigation for community edition users with high-availability requirements:** deploy behind a load balancer that performs health-check-based routing (`GET /health`). If the primary instance fails the health check, traffic routes to a warm standby. Both instances must share the same `contracts/` volume or git-sync their contract files.

---

## Circuit Breaker Guidance for Source Systems

During an OpenDQV outage, validation requests will fail with connection errors or timeouts. Source systems integrating OpenDQV should implement a circuit breaker pattern. The appropriate fail posture depends on the use case:

- **Non-critical data paths** (analytics ingest, reporting feeds): fail-open — allow data through and flag for retrospective validation once the service recovers.
- **Regulated data paths** (KYC, AML, sanctions screening): fail-closed — block the record and queue it for validation once the service recovers. Do not allow unvalidated data onto regulated systems.

The OpenDQV Python SDK includes a `@validate_with` guard decorator that can be configured with a fallback behaviour.

---

## SQLite Integrity and Crash Safety

`opendqv.db` runs in WAL (Write-Ahead Logging) mode with `synchronous=NORMAL`. This provides crash safety: a hard process termination will not corrupt the database. After an unexpected shutdown, verify integrity before restart:

```bash
sqlite3 opendqv.db "PRAGMA integrity_check"
```

Expected output: `ok`. If the integrity check reports errors, restore from the most recent clean backup.

---

## Encryption at Rest

SQLite is stored as a plain file and is not encrypted by default. Anyone with read access to the host filesystem can read the database contents. Mitigations:

- **Filesystem-level encryption**: use LUKS or dm-crypt on the host volume containing `opendqv.db`. This is the recommended approach for most deployments.
- **Column-level encryption**: SQLCipher can replace the standard SQLite library to encrypt the database file with a passphrase. This requires a custom build and is not included in the standard Docker image.

The validation payloads processed by the service are never written to disk, so filesystem encryption primarily protects token metadata and webhook configuration rather than the data being validated.

---

*Last updated: 2026-03-08*
