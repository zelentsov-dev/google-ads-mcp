# Threat model

## Assets

The protected assets are Google Ads credentials, developer tokens, account identifiers, campaign configuration, ads and search terms, billing-adjacent metadata, change-event identity, report data, and the integrity of the user's advertising account.

## Trust boundaries

1. Local config and environment enter the process before a Google client exists.
2. The read and write adapters are the only boundaries that construct Google service clients and receive Google response objects.
3. Signed receipts, approved policies, and the SQLite operation journal cross the local write-authorization boundary.
4. Normalized data crosses from adapters into application services.
5. MCP JSON-RPC crosses stdout to the local host. Diagnostics cross stderr.
6. Plugin and package artifacts cross the public release boundary.

Google account text is adversarial input. A campaign or ad name can contain prompt-injection text, misleading markup, a secret-shaped string, or personal data. It is returned as data and never evaluated as instructions.

## Primary threats and controls

| Threat | Control |
| --- | --- |
| Credential committed or packaged | denylisted file names, gitleaks, full-history scan, artifact scan, allowlisted plugin packager |
| Raw token embedded in config or CLI history | recursive secret-key rejection; hidden input; keyring or environment-variable reference only |
| Unsafe credential file | absolute path, regular-file, no-symlink, owner and POSIX mode checks |
| Unauthorized account mutation | runtime + profile + 30-day policy gates; exact profile/customer/currency scope; fingerprint-bound campaign allowlist; monetary and enable limits |
| Broad, incomplete, or over-bid serving target | one-time typed targeting on paused campaigns; no generic campaign-criterion updates; live constant validation; forced presence-only geo modes; Enhanced CPC disabled; strict child, HTTPS final-URL, effective CPC and source checks before enable |
| Duplicate create after lost or repeated intent | exact non-removed match reads and fail-closed rejection before validate-only for every create route |
| Mutation surface escape | one receipt-only apply tool; no raw protobuf/service input; static service, operation, and field allowlists; forbidden-service scan |
| Preview/apply drift | fresh reread and state fingerprint immediately before durable apply intent |
| Duplicate write after timeout | no automatic write retry; `committed_unverified`; exact direct readback; customer-wide unresolved-write block |
| Receipt replay or substitution | HMAC-signed installation-scoped receipt bound to the exact plan, validation, policy fingerprint, current state, and ten-minute expiry; journal integrity digests; one-time use |
| Crash after mutation intent | SQLite `applying` state before RPC; restart-safe inspection and verification without resending |
| Local pathname substitution | owner-only directories and files; `O_NOFOLLOW`; retained descriptors and inode revalidation around key/journal use |
| Prompt injection from account text | skill and server instructions classify all account strings as untrusted evidence |
| Excessive data extraction | fixed report catalog, GAQL denylist, 1,000-row fetch cap, 200-item pages, 1–20 unique geo IDs for forecasts/ideas, short-lived bound cursors |
| Sensitive error disclosure | stable public error classes, request ID allowlist, constant messages, stderr-only diagnostics |
| Read retry hides failures | reads only, at most two attempts, transient-status allowlist, shared 30-second deadline; writes never retry |
| Misleading analytics | explicit dates, null preservation, modeled-conversion limitation, test-account empty-report limitation |
| Dependency or release compromise | exact lock, audits, CodeQL, checksums, SBOM, attestations, Trusted Publishing, and a pre-build tag gate requiring the exact current main SHA plus successful CI/Security/CodeQL push workflows |

## Explicit non-goals

The v0.2 threat model does not cover conversion uploads, offline user data, schedulers, autonomous optimization, external attribution, hosted OAuth, remote MCP transports, OCI images, or Cloud Run. Those capabilities do not exist in this release.

## Residual risks

- A local process with the user's privileges can read the same environment and owner-only files.
- Google may change field compatibility within a supported API version or return delayed/modeled metrics.
- The MCP host may retain tool output according to its own policies.
- Windows ACL inspection is not performed by the portable runtime; reads remain available with warnings and writes fail closed.
- Google does not provide a resource lock between preview and mutate. The reread fingerprint narrows but cannot eliminate every external race.

Production acceptance must inspect actual terminal and MCP output for PII/credential leakage and verify every canary through Google change events, the local journal, and direct readback.
