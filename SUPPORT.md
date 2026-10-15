# Support

## Community Support (Free)

OpenDQV Core is a community-supported open-source project. The primary support channel is **GitHub Issues**.

- **Bug reports:** [github.com/OpenDQV/OpenDQV/issues](https://github.com/OpenDQV/OpenDQV/issues)
- **Feature requests:** [github.com/OpenDQV/OpenDQV/issues](https://github.com/OpenDQV/OpenDQV/issues)
- **Questions and discussions:** [github.com/OpenDQV/OpenDQV/discussions](https://github.com/OpenDQV/OpenDQV/discussions)

We aim to respond to issues within a few days, but response times depend on maintainer availability — this is an honest, no-SLA community project.

### Beta support commitment (3.x)

OpenDQV Core is Beta (since 2.0.0; currently 3.x). REST endpoints, MCP tool
names and Python SDK signatures are stable within 3.x; removing a contract key
or rule type needs a major version. Rule verdicts are being aligned with
OpenDQV Cloud's managed engine (not yet publicly available): a verdict or error-code difference is fixed in
the next release, patch releases included, with no deprecation period, and
every such change is listed in the [CHANGELOG](CHANGELOG.md). Security fixes
ship in the latest release; older lines get no backports. See
[API Stability](README.md#api-stability).

This is still a community OSS project with no paid SLA — see the section below.

## What to Include in a Bug Report

A good bug report helps us help you faster:

1. **OpenDQV version** (`GET /` returns the current version)
2. **Deployment method** (Docker, local Python, Kubernetes)
3. **Minimal reproduction** — the contract YAML and the record that triggers the issue
4. **What you expected** and what actually happened
5. **Relevant log output** (redact any sensitive field values)

## Commercial Support

There is currently no paid support offering. If there is community demand for a supported tier — SLA-backed responses, deployment assistance, regulated-industry guidance — we are open to discussing it. Raise it in [Discussions](https://github.com/OpenDQV/OpenDQV/discussions).

## Security Issues

**Do not report security vulnerabilities in public Issues.** See [SECURITY.md](SECURITY.md) for the responsible disclosure process.
