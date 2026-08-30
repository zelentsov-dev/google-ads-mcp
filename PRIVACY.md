# Privacy disclosure

Google Ads MCP runs locally over stdio. It does not operate a hosted service, store OAuth credentials, or send account data to a project-controlled backend.

When a user invokes a tool, the server sends the bounded read or typed mutation request needed to Google Ads API. Results are normalized and returned to the local MCP client. Cursors retain at most 1,000 normalized rows in process memory for five minutes and disappear when the process exits. No background processing occurs.

Write policies, an installation signing key, and the SQLite operation journal remain in the user's platform config/state directories. The journal stores normalized before/after evidence, validation, verification, request IDs, and a receipt hash; it does not store the raw receipt or credentials. The developer token can remain in the operating system's secure keyring.

Campaign names, ads, assets, search terms, placements, change events, customer metadata, and metrics may be commercially sensitive or personal data. They remain subject to the user's Google Ads permissions and the privacy behavior of the MCP host/model the user chooses. User identity in change events is omitted by default.

The project does not intentionally collect telemetry. Diagnostics contain product version and sanitized error codes, not queries, headers, credentials, raw protobufs, account text, or report rows.
