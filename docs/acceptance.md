# v0.1.0 acceptance

Automated tests use synthetic fixtures and prove interface and safety properties. They do not prove live Google authorization, data availability, or release installation.

## Test account

- Run config validation, auth check, customer discovery, hierarchy, campaign query, and every inventory resource used by the account.
- Run at least one catalog report and verify that an empty serving result includes the test-account limitation.
- Capture before/after account change history and verify that the MCP session made no change.
- Inspect stdout/stderr for clean JSON-RPC and absence of credentials, config contents, raw protobufs, and PII.

## Production read-only account

- Verify the expected customer, currency, and time zone.
- Inspect multiple campaign types, generic inventory, specialized reports, report date ranges, freshness, null metrics, and cursor bounds.
- Verify an unsupported resource/field combination returns `not_supported`.
- Verify safe GAQL rejects mutations, multiple statements, comments, sensitive resources/fields, missing limits, and unbounded metrics.
- Confirm no change event is attributable to the MCP session.

## Plugin and release

- Install the plugin from the exact release archive in a clean environment.
- Start a new Codex task and exercise account health, inventory, performance, change-history, and safe-GAQL routing.
- Confirm the operator skill requests explicit profile/customer identity and never claims it can apply a change.
- Complete exact-head review, green CI/security gates, artifact scan, SBOM, checksums, provenance, PyPI Trusted Publishing, and GitHub release verification.
