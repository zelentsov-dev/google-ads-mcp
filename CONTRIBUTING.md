# Contributing

Contributions are welcome through focused pull requests.

Never commit credentials, real production fixtures, customer exports, search terms, ads, `.env` files, Google Ads YAML, ADC JSON, private keys, tokens, or local account configuration. Use synthetic IDs and invented account text in tests.

Every change must preserve the read-only v0.1 boundary, strict config validation, bounded reads, null semantics, unknown-enum handling, sanitized errors, and clean MCP stdout. New tools require contract tests and an updated golden tool list. New report templates require metadata-baseline validation and campaign-matrix fixtures.

Run the verification commands in `README.md`. Pull requests changing authentication, GAQL validation, redaction, the adapter boundary, manifests, packaging, or release workflows require security-focused review.
