# Contributing

Contributions are welcome through focused pull requests.

Never commit credentials, real production fixtures, customer exports, search terms, ads, `.env` files, Google Ads YAML, ADC JSON, private keys, tokens, or local account configuration. Use synthetic IDs and invented account text in tests.

Every change must preserve the v0.2 safety boundary: read-only operation remains the default, writes use only typed previews, apply requires a signed plan-bound receipt, and policy, durable journal, no-retry, exact readback, and unresolved-state blocking remain mandatory. Billing, payments, users, account links, uploads, user-data services, deletes, arbitrary mutation input, schedulers, background workers, hosted OAuth, and hidden network transports remain forbidden. Strict config validation, bounded reads, null semantics, unknown-enum handling, sanitized errors, and clean MCP stdout also remain mandatory. New tools require contract tests and an updated golden tool list. New report templates require metadata-baseline validation and campaign-matrix fixtures.

Run the verification commands in `README.md`. Pull requests changing authentication, GAQL validation, redaction, the adapter boundary, manifests, packaging, or release workflows require security-focused review.
