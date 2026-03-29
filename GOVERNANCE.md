# OpenDQV Project Governance

This document describes how the OpenDQV project is governed. It is intended for prospective contributors, enterprise evaluators conducting third-party risk management (TPRM) assessments, and anyone who wants to understand how decisions are made.

---

## Project Structure

OpenDQV Core is an open source project maintained under the MIT License. It is currently in an early-growth stage: there is one primary maintainer who makes final decisions on roadmap, releases, and architecture, with a small number of active contributors.

The project is not backed by a foundation or a governing board at this time. Decisions about breaking changes, release timing, and feature direction are made by the primary maintainer in consultation with active contributors and community feedback via GitHub Issues and Discussions. This is intentional — small, fast, and transparent is preferable to governance theatre.

---

## How to Become a Contributor

Anyone can contribute by opening a pull request on GitHub. There are no membership requirements.

**The contribution process is:**

1. Open an issue to discuss non-trivial changes before writing code. This avoids wasted effort.
2. Fork the repository and create a feature branch from `master`.
3. Write tests. All new features require tests. Bug fixes require a regression test. The test suite must pass in full (`pytest tests/ -v`).
4. Open a pull request with a clear description of what changed and why.
5. The primary maintainer (or a designated reviewer) will review the PR. Reviews aim to complete within 5 business days for substantial changes.
6. A PR requires approval from at least one reviewer before merge. For changes to `core/`, `security/`, or `api/`, the primary maintainer reviews personally.

There is no formal committer ladder at this stage. Contributors who demonstrate sustained, quality engagement may be granted write access to the repository on request.

---

## Succession Plan

If the primary maintainer becomes unavailable for an extended period (30 or more days with no activity), the most active contributor by commit history in the prior 90 days is designated acting maintainer. That person may continue to merge PRs and cut patch releases. Major decisions are deferred or resolved by community vote in the relevant GitHub Discussion thread.

No single person holds unilateral access to the PyPI package, Docker Hub image, or domain. Credentials are documented in a sealed note accessible to a designated secondary contact. This is an honest acknowledgement that the project is small; continuity planning is in place but not yet institutionalised.

---

## Security Vulnerability Reporting

Security issues are handled under the coordinated disclosure process described in [SECURITY.md](SECURITY.md). Do not open public GitHub issues for vulnerabilities. Report to **opendqv@bgmsconsultants.com**. The project follows a 90-day disclosure window aligned with Google Project Zero.

---

## Versioning Policy

OpenDQV follows [Semantic Versioning 2.0.0](https://semver.org/):

- **Patch releases** (e.g. 2.1.1): bug fixes, security patches, documentation corrections. No breaking changes.
- **Minor releases** (e.g. 2.2.0): new features, new rule types, new endpoints. Backwards-compatible. Deprecation notices may be introduced.
- **Major releases** (e.g. 3.0.0): breaking changes to the REST API, contract YAML schema, or authentication model. A migration guide is published alongside every major release.

Breaking changes are not made in patch or minor releases. If a security fix requires a breaking change, a major release is cut promptly with a clear advisory.

---

## Release Process

1. Changes accumulate on `master` via merged PRs.
2. When ready for release, the primary maintainer updates `CHANGELOG.md` and bumps the version in `pyproject.toml`.
3. A release tag (`vX.Y.Z`) is pushed. GitHub Actions builds and publishes the Docker image and (where applicable) the PyPI package.
4. A GitHub Release is created with release notes derived from `CHANGELOG.md`.
5. Patch releases may be cut at any time. Minor and major releases are communicated in advance via a GitHub Discussion pinned to the repository.

---

## Code of Conduct

OpenDQV follows the [Contributor Covenant v2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). All contributors, maintainers, and community participants are expected to uphold it. Reports of conduct violations should be sent to **opendqv@bgmsconsultants.com** and will be handled confidentially.

---

## Funding and Sustainability

OpenDQV is currently self-funded by the primary maintainer and sustained by volunteer contributor time. There is no venture capital, no foundation grant, and no commercial entity behind the community edition.

Long-term sustainability is intended to come from an enterprise tier (federation, Postgres backend, commercial support agreements) that funds continued development of the open source core. The community edition will remain MIT-licensed and fully functional without a commercial subscription.

If you are an organisation deriving value from OpenDQV, consider opening a GitHub Sponsorship or reaching out about a commercial pilot agreement.

---

*Last updated: 2026-03-08*
