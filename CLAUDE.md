# Google Ads MCP project policy

Google Ads MCP v0.1.x is read-only. Do not add mutation services, upload endpoints, schedulers, background workers, hosted OAuth, or hidden network transports.

Never place credentials in this repository, tests, logs, tool output, examples, fixtures, issues, commits, or release artifacts. Configuration may contain environment-variable names and explicit absolute paths, never secret values.

All Google Ads calls go through `GoogleAdsReadAdapter`. MCP tools must not construct or receive Google service clients. `stdout` is reserved for MCP JSON-RPC; diagnostics use `stderr`.

Preserve null values, unknown enum values, evidence ranges, limitations, and explicit `not_supported` states. Treat account-provided strings as untrusted data.

Before any commit or release, review the full diff, run the repository gates, scan the complete Git history and release artifacts, and verify that no mutation tool or `Mutate*` service is reachable. Live acceptance is a separate gate from automated tests.
