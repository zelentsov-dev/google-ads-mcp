# Privacy disclosure

Google Ads MCP runs locally over stdio. It does not operate a hosted service, store OAuth credentials, or send account data to a project-controlled backend.

When a user invokes a tool, the server sends the bounded GAQL or discovery request needed to answer it to Google Ads API. The result is normalized in memory and returned to the local MCP client. Cursors retain at most 1,000 normalized rows in process memory for five minutes and disappear when the process exits. No background processing occurs.

Campaign names, ads, assets, search terms, placements, change events, customer metadata, and metrics may be commercially sensitive or personal data. They remain subject to the user's Google Ads permissions and the privacy behavior of the MCP host/model the user chooses. User identity in change events is omitted by default.

The project does not intentionally collect telemetry. Diagnostics contain product version and sanitized error codes, not queries, headers, credentials, raw protobufs, account text, or report rows.
