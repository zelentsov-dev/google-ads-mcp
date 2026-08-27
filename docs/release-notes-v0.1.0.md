# Google Ads MCP v0.1.0

Initial security-first, read-only release.

- Connects through Application Default Credentials or one explicit owner-only `google-ads.yaml`.
- Exposes 13 bounded MCP tools for account discovery, health, campaign inventory, catalog reports, change events, field metadata, and safe GAQL.
- Covers every `AdvertisingChannelType` in Google Ads API v25 with generic inventory/performance and specialized catalog reports where supported.
- Preserves null metrics and unknown enums, labels modeled conversions and test-account limitations, and returns explicit `not_supported` API gaps.
- Includes the implicit Google Ads Operator Codex skill and a credential-free frozen uv launcher.
- Contains no write, delete, upload, scheduler, optimizer, hosted OAuth, background worker, remote transport, or OCI capability.

Release artifacts include the wheel, sdist, Codex plugin archive, SPDX SBOM, SHA-256 checksums, and GitHub provenance. Publication remains gated on local test-account, production-read-only, and clean-plugin acceptance.
