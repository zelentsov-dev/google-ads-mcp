# Google Ads MCP v0.1.1

Read-only stabilization and onboarding release.

- Updates the pinned Google Ads API v25 campaign inventory query to use `campaign.start_date_time` and `campaign.end_date_time`.
- Adds regression coverage that rejects the removed date-only field names.
- Replaces the OAuth-first setup with a service-account-first path for unattended local use.
- Adds an end-to-end Manager Account, developer token, production advertiser, test hierarchy, local configuration, client registration, smoke-test, and troubleshooting checklist.
- Preserves the 13-tool read-only MCP surface and the three-service adapter allowlist.

Release artifacts include the wheel, sdist, Codex plugin archive, SPDX SBOM, SHA-256 checksums, and GitHub provenance. Publication remains gated on exact-head test-account, production-read-only, clean-plugin, CI, and security acceptance.
