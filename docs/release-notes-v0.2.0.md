# Google Ads MCP v0.2.0

Safe Operator release for Google Ads API v25.

- Preserves the original 13 read tools and adds server/profile metadata, campaign diagnostics, search-term and asset reports, conversion goals, recommendations, keyword ideas, and forecasts.
- Adds 19 typed write previews and four operation interfaces. No public tool accepts a raw protobuf, mutate payload, or Google service name.
- Requires runtime, profile, and unchanged 30-day policy approval; enforces exact customer/currency scope, a fingerprint-bound campaign allowlist for enable, and explicit monetary limits.
- Uses Google `validate_only`, one-time signed ten-minute receipts, durable SQLite `applying` intent, no ambiguous-write retries, direct readback, drift invalidation, replay protection, and recovery verification.
- Creates Search, Performance Max, and App campaigns paused, rejects duplicate create matches, and adds one-time presence-only campaign targeting. Generic criterion updates are limited to positive ad-group keywords; status-only child enables bind authoritative CPC evidence; bidding changes require a paused campaign and reject Enhanced CPC. Search enablement strictly binds every child row plus approved HTTPS final URLs, and aggregate-budget evidence fails closed on malformed or conflicting rows. Typed forecasts preserve match types and accept at most 20 unique geo IDs. Recommendation actions fail closed when `validate_only` is unavailable.
- Adds hidden-input system-keyring onboarding plus policy, operation, profile, and secret-management CLI commands.
- Keeps billing, account linking, user access and lists, offline jobs, conversion uploads, User Data services, schedulers, hosted transports, and autonomous optimization outside the release boundary.

Publication remains gated on the explicitly risk-accepted exact-head production mutation matrix, production read-only regression, paused Performance Max/App acceptance, bounded Search serving evidence, clean Codex/Claude install, CI/security checks, SBOM, checksums, and provenance. Google recommends a separate test hierarchy; this release does not claim that evidence.
