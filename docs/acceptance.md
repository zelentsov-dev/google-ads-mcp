# v0.2.0 acceptance

Automated tests use synthetic fixtures. They prove interface, policy, receipt, journal, protobuf-construction, recovery, redaction, and static-boundary properties. They do not prove live Google authorization, campaign compatibility, serving data, or release installation.

## Automated gates

- Ruff, Pyright for Linux/Darwin/Windows, the full test suite, and at least 90% overall branch coverage pass before any remote branch update.
- CI runs the Linux 3.12 full coverage gate before compatibility runners, cancels superseded runs, and performs coverage-free runtime compatibility tests for Linux 3.11/3.13, macOS 3.12, and Windows 3.12.
- Config, GAQL, journal, operator, typed-plan, policy, receipt, secret-storage, mutation-adapter, security, and stdout-boundary modules have 100% branch coverage.
- Golden `tools/list` and public schemas contain exactly 46 tools. Only `operations_apply` accepts a receipt; no tool accepts a raw mutation or Google service name.
- Static analysis proves the write adapter uses only its allowlisted service and operation types and rejects billing, links, users, offline jobs, uploads, and User Data services.
- Fixtures cover validate-only success/failure, atomic/partial results, ambiguous outcomes, drift, receipt replay/expiry/scope, policy revocation and limits, durable `applying`, crash recovery, and direct readback.
- Adversarial fixtures reject campaign-level generic criterion mutations, removed-parent or over-bid child enables, Enhanced CPC, malformed targeting/keyword/ad/aggregate rows, missing or unsafe final URLs, oversized/duplicate geo IDs, final-component symlinks, and journal inode replacement.
- Artifact, manifest, report and write-state GAQL-template, secret-scan, wheel, sdist, and clean-plugin gates pass from one exact SHA.

## Live account verification

- Verify authentication, hierarchy, inventory (`status`, `budget`, `bidding`, `ad_groups`, `ads`, `assets`, `app_assets`, `asset_group_assets`, `criteria`, `keywords`, `conversions`), all analytics tools, report dates/freshness, null metrics, and cursor bounds against the live account.
- Run live GAQL template validation (`scripts/validate_gaql_templates.py --live-profile production-read-only`) to confirm all 84 report and write-state GAQL queries validate against Google Ads API v25.
- Exercise typed write previews (`validate_only`) and explicitly owner-authorized operations through `preview -> apply -> authoritative direct readback`.
- Compare Google change events, direct reads, and the local journal. Inspect stdout/stderr and artifacts for credentials, paths, raw envelopes, queries, and PII.
- Keep ambiguous, partial, unavailable, and unsupported evidence distinct. Source tests cannot substitute for an unavailable live route.

## Plugin and release

- Install the plugin from the exact release archive in a clean Codex and Claude environment.
- Confirm read-only startup by default and write availability only with all runtime/profile/policy gates.
- Complete exact-head review, green CI/Security/CodeQL push workflows on the exact current `main` SHA, SBOM, checksums, provenance, PyPI Trusted Publishing, and GitHub release verification. The tag workflow must fail before build when the tag is not the current `main` commit or any required exact-SHA workflow is absent or unsuccessful.
