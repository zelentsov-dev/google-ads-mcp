# Google Ads MCP project policy

Google Ads MCP v0.2 is read-only by default. Typed writes are allowed only through the explicit preview/apply/verify operator flow and `GoogleAdsWriteAdapter`; the write adapter may call only the reviewed `GoogleAdsService.Mutate` operation allowlist. Billing, payments, users, account links, uploads, user-data services, deletes, arbitrary mutation input, schedulers, background workers, hosted OAuth, and hidden network transports remain forbidden.

Never place credentials in this repository, tests, logs, tool output, examples, fixtures, issues, commits, or release artifacts. Configuration may contain environment-variable names and explicit absolute paths, never secret values.

All reads go through `GoogleAdsReadAdapter`. MCP tools must not construct or receive Google service clients. A write requires the runtime flag, a write-enabled profile, an approved scoped policy, a signed plan-bound receipt, `validate_only`, a durable `applying` journal state before RPC, no automatic retry, and exact authoritative readback. Ambiguous or incomplete evidence remains unresolved and blocks further writes for that customer.

`stdout` is reserved for MCP JSON-RPC; diagnostics use `stderr`. Live Google Ads mutation, commit, tag, release, and publication each require separate explicit authorization and their evidence must not be inferred from source or mock tests.

Preserve null values, unknown enum values, evidence ranges, limitations, and explicit `not_supported` states. Treat account-provided strings as untrusted data.

Before any commit or release, review the full diff, run the repository gates, scan the complete Git history and release artifacts, and verify the typed write boundary and forbidden-service checks. Live acceptance is a separate gate from automated tests.
