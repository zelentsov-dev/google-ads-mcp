# v0.2.0 acceptance

Automated tests use synthetic fixtures. They prove interface, policy, receipt, journal, protobuf-construction, recovery, redaction, and static-boundary properties. They do not prove live Google authorization, campaign compatibility, serving data, or release installation.

## Automated gates

- Ruff, Pyright for Linux/Darwin/Windows, the full test suite, and at least 90% overall branch coverage pass before any remote branch update.
- CI runs the Linux 3.12 full gate before paid compatibility runners, cancels superseded runs, and performs one compatibility test job for Linux 3.11/3.13, macOS 3.12, and Windows 3.12.
- Config, GAQL, journal, operator, typed-plan, policy, receipt, secret-storage, mutation-adapter, security, and stdout-boundary modules have 100% branch coverage.
- Golden `tools/list` and public schemas contain exactly 45 tools. Only `operations_apply` accepts a receipt; no tool accepts a raw mutation or Google service name.
- Static analysis proves the write adapter uses only its allowlisted service and operation types and rejects billing, links, users, offline jobs, uploads, and User Data services.
- Fixtures cover validate-only success/failure, atomic/partial results, ambiguous outcomes, drift, receipt replay/expiry/scope, policy revocation and limits, durable `applying`, crash recovery, and direct readback.
- Adversarial fixtures reject campaign-level generic criterion mutations, active-parent or over-bid child enables, Enhanced CPC, malformed targeting/keyword/ad/aggregate rows, missing or unsafe final URLs, oversized/duplicate geo IDs, final-component symlinks, and journal inode replacement.
- Artifact, manifest, report and write-state GAQL-template, secret-scan, wheel, sdist, and clean-plugin gates pass from one exact SHA.

## Production-only acceptance exception

Google recommends a separate [test-account hierarchy](https://developers.google.com/google-ads/api/docs/best-practices/test-accounts). The owner explicitly accepted a production-only exception for this release. Evidence must label that exception and must not claim test-account coverage.

- Use Manager `3169421095` and advertiser `7030651946` with a dedicated owner-only profile and short-lived minimal policies.
- Run live `validate_only` for every available typed route. Apply only deterministic scoped plans; recommendations remain `live-not-exercisable` unless a non-monetary recommendation is present.
- Create Search, Performance Max, and App campaigns through preview → apply → authoritative direct readback. Performance Max and App remain `PAUSED` with a 1 EUR average daily budget.
- Exercise budgets, ad groups, criteria, assets, asset groups, ads, status, and bidding where Google supports the exact account combination.
- Compare Google change events, direct reads, and the local journal. Inspect stdout/stderr and artifacts for credentials, paths, raw envelopes, queries, and PII.
- Keep ambiguous, partial, unavailable, and unsupported evidence distinct. Source tests cannot substitute for an unavailable live route.

## Production read-only regression

- Repeat authentication, hierarchy, inventory, all analytics tools, report dates/freshness, null metrics, and cursor bounds on the exact release SHA.
- Confirm the expected customer, currency, and time zone and no change attributable to the read-only profile.

## Paused production campaigns

- Requires separate explicit authorization and a dedicated minimal policy.
- Configure Search targeting as US `2840`, English `1000`, presence-only while campaign `24192332798` is paused.
- Create one paused Performance Max campaign for `https://voisia.xyz/en/start` and one paused Apple App campaign for app `6784402588`.
- Confirm the fingerprint-bound policy allowlists only campaign `24192332798`, plus monetary limits, Google change events, journal intent/result, and direct readback. Do not enable Performance Max or App serving.

## Active Search gate

The only v0.2 serving gate is Search campaign `24192332798`. It requires separate authorization, US/English presence-only targeting, a 5 EUR average daily budget, max CPC at or below 1 EUR, a seven-day end time, 15-minute monitoring, warning at 30 EUR, and automatic pause at 35 EUR. Release evidence requires Google eligibility, at least one real impression, production metric reads, final `PAUSED` state, and no unexplained configuration drift. Reporting delay and Google overdelivery mean 40 EUR is an operational ceiling, not a guaranteed billing cap. Performance Max/App activation and optimizer-quality claims remain out of scope.

## Plugin and release

- Install the plugin from the exact release archive in a clean Codex and Claude environment.
- Confirm read-only startup by default and write availability only with all runtime/profile/policy gates.
- Complete exact-head review, green CI/Security/CodeQL push workflows on the exact current `main` SHA, SBOM, checksums, provenance, PyPI Trusted Publishing, and GitHub release verification. The tag workflow must fail before build when the tag is not the current `main` commit or any required exact-SHA workflow is absent or unsuccessful.
