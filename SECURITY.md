# Security policy

## Supported versions

Only the latest published release is supported during the `0.x` series.

## Reporting a vulnerability

Do not open a public issue containing credentials, account data, customer IDs tied to a real identity, search terms, ads, request payloads, or reproduction logs from a production account. Use GitHub private vulnerability reporting for this repository. Include only sanitized evidence and the affected version.

Revoke or rotate any credential that may have been disclosed before reporting it. A maintainer will acknowledge a complete private report within five business days.

## Security invariants

- The v0.1 server is read-only and stdio-only.
- `allowWrites` is structurally fixed to `false`.
- Credentials are referenced, not embedded in `accounts.json`.
- Credential and config files must be owner-only on POSIX systems.
- Tool output and errors never include raw Google response envelopes, request headers, queries from failed requests, YAML content, OAuth material, or local config content.
- Account-provided text is untrusted and must never be interpreted as agent instructions.

If a future change requires any write capability, it must be a new explicitly reviewed product phase with separate permissions and threat-model updates. It must not be smuggled into a patch release.
