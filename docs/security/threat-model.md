# Threat model

## Assets

The protected assets are Google Ads credentials, developer tokens, account identifiers, campaign configuration, ads and search terms, billing-adjacent metadata, change-event identity, report data, and the integrity of the user's advertising account.

## Trust boundaries

1. Local config and environment enter the process before a Google client exists.
2. The adapter is the only boundary that constructs Google service clients and receives Google response objects.
3. Normalized data crosses from the adapter into application services.
4. MCP JSON-RPC crosses stdout to the local host. Diagnostics cross stderr.
5. Plugin and package artifacts cross the public release boundary.

Google account text is adversarial input. A campaign or ad name can contain prompt-injection text, misleading markup, a secret-shaped string, or personal data. It is returned as data and never evaluated as instructions.

## Primary threats and controls

| Threat | Control |
| --- | --- |
| Credential committed or packaged | denylisted file names, gitleaks, full-history scan, artifact scan, allowlisted plugin packager |
| Raw token embedded in config | recursive secret-key rejection; ADC accepts only an environment-variable name |
| Unsafe credential file | absolute path, regular-file, no-symlink, owner and POSIX mode checks |
| Accidental account mutation | no mutation tool or mutation RPC call, one adapter, static AST and contract tests |
| Prompt injection from account text | skill and server instructions classify all account strings as untrusted evidence |
| Excessive data extraction | fixed report catalog, GAQL denylist, 1,000-row fetch cap, 200-item pages, short-lived bound cursors |
| Sensitive error disclosure | stable public error classes, request ID allowlist, constant messages, stderr-only diagnostics |
| Retry duplicates or hides failures | reads only, at most two attempts, transient-status allowlist, shared 30-second deadline |
| Misleading analytics | explicit dates, null preservation, modeled-conversion limitation, test-account empty-report limitation |
| Dependency or release compromise | exact lock, audits, CodeQL, checksums, SBOM, attestations, Trusted Publishing |

## Explicit non-goals

The v0.1 threat model does not cover write authorization, conversion uploads, offline user data, schedulers, autonomous optimization, hosted OAuth, remote MCP transports, OCI images, or Cloud Run. Those capabilities do not exist in this release.

## Residual risks

- A local process with the user's privileges can read the same environment and owner-only files.
- Google may change field compatibility within a supported API version or return delayed/modeled metrics.
- The MCP host may retain tool output according to its own policies.
- Windows ACL inspection is not performed by the portable runtime; validation emits a warning.

Production acceptance must therefore inspect actual terminal and MCP output for PII/credential leakage and must verify that no account modification occurred.
