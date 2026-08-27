# Google Ads MCP

Google Ads MCP is a local, security-first, read-only MCP server and Codex plugin for inspecting Google Ads accounts. Version `0.1.0` exposes bounded inventory, reporting, metadata, change-history, and safe GAQL tools. It contains no write, upload, scheduling, delete, optimizer, or hosted transport capability.

The public Python distribution is named `google-ads-operator-mcp`. The product and command remain `Google Ads MCP` and `google-ads-mcp`.

## Security boundary

- Every account-scoped request names both a profile and a customer ID.
- Google credentials remain in Application Default Credentials or an explicit owner-only `google-ads.yaml` outside the repository.
- `accounts.json` may contain only an environment-variable name for the developer token, never the token itself.
- `allowWrites` must be `false`; any other value is rejected.
- Only `GoogleAdsService`, `CustomerService`, and `GoogleAdsFieldService` are constructed by the production adapter.
- A tool receives normalized rows, never a Google client or raw response envelope.
- MCP mode reserves stdout for JSON-RPC. Diagnostics and sanitized failure codes go to stderr.

Read [SECURITY.md](SECURITY.md), [PRIVACY.md](PRIVACY.md), and the [threat model](docs/security/threat-model.md) before connecting a production account.

## Requirements

- Python 3.11, 3.12, or 3.13
- [uv](https://docs.astral.sh/uv/)
- Google Ads API access with a developer token
- ADC, or a separately stored owner-only `google-ads.yaml`

Use Google's official guidance for the [developer token](https://developers.google.com/google-ads/api/docs/get-started/dev-token), [Python client configuration](https://developers.google.com/google-ads/api/docs/client-libs/python/configuration), and [Application Default Credentials](https://cloud.google.com/docs/authentication/provide-credentials-adc).

## Local setup

```bash
uv sync --frozen --all-groups
uv run --frozen google-ads-mcp version
uv run --frozen google-ads-mcp config init
```

After the public release, the standalone CLI can also be installed with `uv tool install google-ads-operator-mcp==0.1.0`. Use the release plugin archive, not a source-tree copy, for the final clean-plugin acceptance gate.

Edit `~/.config/google-ads-mcp/accounts.json`, replace the public account IDs, then restore owner-only permissions:

Expose `GOOGLE_ADS_DEVELOPER_TOKEN` from your local secret manager without putting its value in the repository or shell history, then run:

```bash
chmod 600 ~/.config/google-ads-mcp/accounts.json
gcloud auth application-default login
uv run --frozen google-ads-mcp config validate
uv run --frozen google-ads-mcp auth doctor --profile production-read-only
```

Do not paste the developer token, OAuth refresh token, client secret, ADC JSON, or YAML contents into this repository, an issue, terminal transcript, or chat.

## Configuration

Config precedence is `--config`, then `GOOGLE_ADS_MCP_CONFIG`, then `~/.config/google-ads-mcp/accounts.json`.

```json
{
  "profiles": [
    {
      "name": "production-read-only",
      "loginCustomerId": "1234567890",
      "defaultCustomerId": "0987654321",
      "auth": {
        "type": "adc",
        "developerTokenEnv": "GOOGLE_ADS_DEVELOPER_TOKEN"
      },
      "allowWrites": false
    }
  ]
}
```

For an existing Google client YAML, use `{"type":"googleAdsYaml","path":"/absolute/owner-only/google-ads.yaml"}`. Relative paths and symbolic links are rejected. The server never searches the working directory or repository for YAML credentials.

## CLI

```text
google-ads-mcp version
google-ads-mcp config init
google-ads-mcp config validate
google-ads-mcp auth doctor --profile <name>
google-ads-mcp accounts discover --profile <name>
google-ads-mcp serve --stdio
```

## MCP tools

The fixed v0.1 surface is:

```text
auth_check                customers_list
customer_get              customer_hierarchy
account_health            campaigns_query
campaign_inventory        resource_metadata
reports_catalog           report_run
change_events_query       gaql_validate
gaql_search
```

Typed lists return at most 200 items per page. A single Google read is capped at 1,000 normalized rows. Five-minute cursors are bound to the profile and exact request, with at most 20 active cursors per profile. Safe GAQL has no cursor and requires a single comment-free `SELECT`, a non-sensitive resource, and `LIMIT 1..1000`.

See [docs/tools.md](docs/tools.md) for parameters, evidence semantics, and report behavior.

## Codex plugin

The repository root is the plugin root. `.mcp.json` calls `./bin/google-ads-mcp-launcher`, which only executes the frozen uv environment over stdio. The implicit `google-ads-operator` skill treats all account strings as untrusted data and never proposes unavailable mutation tools.

The repository intentionally does not create or modify a personal marketplace entry. Release-archive installation is validated separately during acceptance.

## Verification

```bash
uv run --frozen ruff check .
uv run --frozen pyright
uv run --frozen pytest
uv run --frozen python scripts/validate_read_only.py
uv run --frozen python scripts/validate_gaql_templates.py
uv run --frozen python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
uv run --frozen python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/google-ads-operator
```

Automated verification is not live-account acceptance. The test-account and production-read-only gates in [docs/acceptance.md](docs/acceptance.md) remain required before `v0.1.0`.

## License

Apache-2.0. Google Ads is a trademark of Google LLC. This project is independent and is not endorsed by Google.
