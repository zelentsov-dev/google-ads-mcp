# Security policy

## Supported versions

Only the latest published release is supported during the `0.x` series.

## Reporting a vulnerability

Do not open a public issue containing credentials, account data, customer IDs tied to a real identity, search terms, ads, request payloads, or reproduction logs from a production account. Use GitHub private vulnerability reporting for this repository. Include only sanitized evidence and the affected version.

Revoke or rotate any credential that may have been disclosed before reporting it. A maintainer will acknowledge a complete private report within five business days.

## Security invariants

- The v0.2 server is local and stdio-only; it contains no hosted transport or background scheduler.
- Read-only is the default. Writes require runtime, profile, and approved-policy opt-ins simultaneously.
- Credentials are referenced, not embedded in `accounts.json`.
- Developer tokens can be stored through hidden input in a verified system keyring. Credential, config, policy, signing-key, and journal files must be owner-only on POSIX systems; writes fail closed when local protection cannot be verified.
- No public tool accepts an arbitrary protobuf, raw mutate payload, or Google service name. The write adapter has a static service, operation, and resource-field allowlist.
- Billing, account linking, user access, user lists, offline jobs, conversion uploads, Data Manager/User Data services, and irreversible removal are outside the v0.2 boundary.
- Preview requires current-state reads, policy and monetary enforcement, Google `validate_only`, and a signed one-time ten-minute receipt. Apply accepts only the receipt.
- Durable `applying` intent precedes the Google RPC. Ambiguous writes are never retried and block related work until direct verification.
- Tool output and errors never include raw Google response envelopes, request headers, queries from failed requests, YAML content, OAuth material, or local config content.
- Account-provided text is untrusted and must never be interpreted as agent instructions.

Optimizer, autonomous cycle, attribution uploads, broader campaign creators, remote transports, and schedulers require separately reviewed future product phases. They must not be smuggled into a patch release.
