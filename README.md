# Google Ads MCP

Google Ads MCP is a local, security-first MCP server and Codex plugin for Google Ads API v25. Version `0.2.0` keeps the original 13 read tools, adds nine typed analytics tools, and introduces policy-gated write previews with one signed apply interface and durable readback verification.

The public Python distribution is named `google-ads-operator-mcp`. The product and command remain `Google Ads MCP` and `google-ads-mcp`.

## Security boundary

- Every account-scoped request names both a profile and a customer ID.
- Google credentials remain in Application Default Credentials or an explicit owner-only `google-ads.yaml` outside the repository. The recommended developer-token source is the system keyring.
- `accounts.json` contains only a keyring reference or an environment-variable name for the developer token, never the token itself.
- Writes require all three gates: `serve --allow-writes`, profile `allowWrites: true`, and an unchanged approved policy scoped to the exact profile, customer, currency, operation kind, and optional campaign allowlist. Campaign enable always requires a non-empty allowlist.
- The write adapter can construct only `GoogleAdsService.Mutate` operations from an internal field allowlist. Billing, account linking, user access, user lists, offline jobs, conversion uploads, Data Manager/User Data services, raw protobufs, and arbitrary service names are not exposed.
- Preview re-reads current state, enforces monetary limits, calls `validate_only`, and returns a signed one-time receipt that expires in ten minutes. Apply accepts only that receipt, writes durable `applying` intent before the RPC, never retries an ambiguous write, and performs direct readback.
- New Search, Performance Max, and App campaigns are created `PAUSED`. Enabling is a separate policy permission.
- A tool receives normalized rows, never a Google client or raw response envelope.
- MCP mode reserves stdout for JSON-RPC. Diagnostics and sanitized failure codes go to stderr.

Read [SECURITY.md](SECURITY.md), [PRIVACY.md](PRIVACY.md), and the [threat model](docs/security/threat-model.md) before connecting a production account.

## Empty account to first MCP call

Complete this checklist in order. Replace every placeholder before running a command. Customer IDs are the 10 digits shown in Google Ads with hyphens removed.

```text
PROFILE_NAME
PRODUCTION_MANAGER_CUSTOMER_ID
PRODUCTION_CLIENT_CUSTOMER_ID
TEST_MANAGER_CUSTOMER_ID
TEST_CLIENT_CUSTOMER_ID
GCP_PROJECT_ID
SERVICE_ACCOUNT_EMAIL
SERVICE_ACCOUNT_JSON_PATH
```

Never paste a developer token, service-account JSON, OAuth client secret, refresh token, or access token into this README, a shell command, shell history, chat, issue, log, fixture, or Git commit. Treat the developer token and service-account key as passwords.

### 1. Understand the account identities

- A **Google Account** is the human login used for Google products.
- A **Google Ads advertiser/client account** owns campaigns and billing. Its 10-digit ID is the client customer ID used as the target of API calls.
- A **Google Ads Manager Account**, formerly MCC, manages linked advertiser accounts. Its 10-digit ID is normally `loginCustomerId`.
- A **developer token** identifies the API application. It comes from the API Center of a non-test Manager Account and does not itself grant access to an advertiser.
- A **service account** is a non-human Google identity created in a Google Cloud project. Google Ads access is granted by adding its email address as a Google Ads user.

Official background: [Google Ads API onboarding](https://developers.google.com/google-ads/api/docs/get-started/onboarding), [developer token](https://developers.google.com/google-ads/api/docs/api-policy/developer-token), and [service-account workflow](https://developers.google.com/google-ads/api/docs/oauth/service-accounts).

### 2. Create the production Manager Account

Create a Manager Account from [Google Ads Manager Accounts](https://ads.google.com/home/tools/manager-accounts/). Record its ID as `PRODUCTION_MANAGER_CUSTOMER_ID`.

The API Center exists only on a non-test Manager Account. An advertiser account, ordinary Google Account, or test Manager Account cannot issue the production developer token.

### 3. Link the production advertiser

From the production Manager Account:

1. Open **Accounts → Sub-account settings**.
2. Select **+ → Link existing account**.
3. Enter `PRODUCTION_CLIENT_CUSTOMER_ID`, preview the request, and send it.

From the advertiser account:

1. Open **Admin → Access and security → Managers**.
2. Find the pending link request and accept it.

The link is not active until the advertiser accepts it. See Google's [manager linking instructions](https://support.google.com/google-ads/answer/7459601).

### 4. Apply for the developer token

Open [API Center](https://ads.google.com/aw/apicenter) while signed in to `PRODUCTION_MANAGER_CUSTOMER_ID`.

Use:

- a regularly monitored API contact email;
- the real company or individual name;
- a working public company, product, GitHub, or LinkedIn URL accepted by Google;
- `Advertiser` when the form asks who uses the tool and it manages only your own campaigns;
- the actual principal place of business and jurisdiction.

If the application asks for intended use, adapt this template without claiming features you do not actually operate:

```text
<COMPANY> uses the Google Ads API through a locally run, open-source
MCP server to inspect campaign performance, create and update advertising
resources under explicit policy and monetary limits, verify every applied
change, optimize campaigns, and produce audit reports. Credentials and
advertising data remain on the user's machine. The application does not
provide hosted OAuth, billing management, user management, or unrestricted
access to Google Ads mutation services.
```

Version `0.2.0` supports the typed, policy-gated operations documented below. It does not yet include an autonomous optimizer or scheduler; those clauses remain roadmap statements and must be identified as such if Google asks what is live today.

Developer-token access can be Test Account, Explorer, Basic, or Standard depending on Google's current review outcome. Test Account access works only with test accounts. Explorer access is production-capable but restricted. Basic access is the normal production tier with a documented daily operation limit. Re-check the current [access-level rules](https://developers.google.com/google-ads/api/docs/api-policy/access-levels) before production acceptance.

### 5. Create the Google Cloud service account

The recommended unattended local path is a service account. It avoids an interactive user OAuth refresh flow.

1. Create or select `GCP_PROJECT_ID` in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable **Google Ads API** for that project.
3. Create a user-managed service account and record `SERVICE_ACCOUNT_EMAIL`.
4. Create a JSON key only if organization policy permits it. Save it once at the absolute `SERVICE_ACCOUNT_JSON_PATH` outside this repository.
5. Restrict the key file before use:

```bash
chmod 600 "$SERVICE_ACCOUNT_JSON_PATH"
```

Google warns that long-lived service-account keys are sensitive. If the workload later runs on Google Cloud, prefer an attached service account; for other hosted environments, prefer workload identity federation. This local flow supports ADC through an explicit key path.

### 6. Grant the service account the minimum Ads role

While signed in as an administrator of `PRODUCTION_MANAGER_CUSTOMER_ID`:

1. Open **Admin → Access and security → Users**.
2. Select **+**.
3. Enter `SERVICE_ACCOUNT_EMAIL`.
4. Grant **Read only** access for analytical profiles. Grant a role capable of campaign changes only to the isolated test profile used for the v0.2 write gate.

Do not give the production service account a write-capable role merely because v0.2 supports writes. Production writes require a separately reviewed policy and canary authorization. Keep unrelated production profiles `allowWrites: false`.

### 7. Create the isolated test hierarchy

Use Google's [test-account flow](https://developers.google.com/google-ads/api/docs/best-practices/test-accounts):

1. Sign in with a Google Account that is not linked to the production Manager Account.
2. Create a test Manager Account and record `TEST_MANAGER_CUSTOMER_ID`.
3. Under that manager, select **Accounts → + → Create new account** and record `TEST_CLIENT_CUSTOMER_ID`.
4. Add the service-account email only at the access level needed for the current test phase.
5. Use the developer token from `PRODUCTION_MANAGER_CUSTOMER_ID`; do not request a token from the test manager.

Production and test hierarchies cannot be linked. Test accounts do not serve ads, have no billing, and normally return empty impressions, cost, and conversion metrics. They cannot prove optimizer quality. Use a dedicated test credential with no production access for mutation acceptance.

### 8. Install v0.2.0

For a source checkout:

```bash
uv sync --frozen --all-groups
uv run --frozen google-ads-mcp version
```

After the public release, install the standalone CLI with:

```bash
uv tool install google-ads-operator-mcp==0.2.0
google-ads-mcp version
```

Use the release plugin archive, not a source-tree copy, for final clean-plugin acceptance.

### 9. Configure the recommended service-account path

Make the key path available to ADC. This command contains only a path, not the key contents:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$SERVICE_ACCOUNT_JSON_PATH"
```

Create the owner-only MCP config:

```bash
uv run --frozen google-ads-mcp config init
```

Edit `~/.config/google-ads-mcp/accounts.json` so it contains the resolved numeric IDs:

```json
{
  "profiles": [
    {
      "name": "PROFILE_NAME",
      "loginCustomerId": "PRODUCTION_MANAGER_CUSTOMER_ID",
      "defaultCustomerId": "PRODUCTION_CLIENT_CUSTOMER_ID",
      "auth": {
        "type": "adc",
        "developerTokenKeyring": true
      },
      "allowWrites": false
    },
    {
      "name": "test-operator",
      "loginCustomerId": "TEST_MANAGER_CUSTOMER_ID",
      "defaultCustomerId": "TEST_CLIENT_CUSTOMER_ID",
      "auth": {
        "type": "adc",
        "developerTokenKeyring": true
      },
      "allowWrites": true
    }
  ]
}
```

Store the developer token through hidden input. The token is not accepted as a command-line argument and is not printed back:

```bash
uv run --frozen google-ads-mcp onboard --profile PROFILE_NAME
uv run --frozen google-ads-mcp onboard --profile test-operator
```

Validate the file and both account paths:

```bash
chmod 600 ~/.config/google-ads-mcp/accounts.json
uv run --frozen google-ads-mcp config validate
uv run --frozen google-ads-mcp auth doctor --profile PROFILE_NAME
uv run --frozen google-ads-mcp accounts discover --profile PROFILE_NAME
uv run --frozen google-ads-mcp auth doctor --profile test-operator
uv run --frozen google-ads-mcp accounts discover --profile test-operator
```

### 10. Use single-user OAuth/ADC only as fallback

If service accounts are prohibited by organization policy, use Google's single-user ADC flow with a Google user that has access to the Manager Account:

```bash
unset GOOGLE_APPLICATION_CREDENTIALS
gcloud auth application-default login \
  --scopes=https://www.googleapis.com/auth/adwords,https://www.googleapis.com/auth/cloud-platform
```

The authorization must include the Google Ads scope `https://www.googleapis.com/auth/adwords`. Some organizations or OAuth policies block this flow with **This app is blocked**. Do not weaken organization controls or paste OAuth material into config; use the service-account path if permitted, or ask the administrator to approve the official flow.

If the system keyring is unavailable, an ADC profile may instead reference a pre-existing secret-manager-provided environment variable with `"developerTokenEnv": "GOOGLE_ADS_DEVELOPER_TOKEN"`. Never put its value in the config or client registration.

An explicit owner-only `google-ads.yaml` is also supported:

```json
{
  "auth": {
    "type": "googleAdsYaml",
    "path": "/absolute/owner-only/google-ads.yaml",
    "developerTokenKeyring": true
  }
}
```

Relative paths and symbolic links are rejected. The server never searches the working directory or repository for YAML credentials.

### 11. Register the MCP without secret values

Generate a secret-free registration command for either client:

```bash
google-ads-mcp --config ~/.config/google-ads-mcp/accounts.json client install --target codex
google-ads-mcp --config ~/.config/google-ads-mcp/accounts.json client install --target claude
```

Review and execute the emitted command. The equivalent installed-CLI commands are:

```bash
codex mcp add google-ads -- google-ads-mcp serve --stdio
claude mcp add -s user google-ads -- google-ads-mcp serve --stdio
```

For a source checkout or plugin archive, register the absolute launcher path instead:

```bash
codex mcp add google-ads -- /absolute/path/to/google-ads-mcp/bin/google-ads-mcp-launcher
claude mcp add -s user google-ads -- /absolute/path/to/google-ads-mcp/bin/google-ads-mcp-launcher
```

The client process must inherit `GOOGLE_APPLICATION_CREDENTIALS`; the developer token is loaded from the system keyring. An environment-reference or owner-only YAML profile remains available as a fallback. Never add token values or JSON key contents to Codex or Claude client config.

The generated registration starts without runtime write permission. For the isolated test operator, add `--allow-writes` only after creating and approving the policy below.

Restart Codex or Claude after registration. An already-running task may not discover a newly registered MCP server.

### 12. Restart the client and run the first smoke checklist

In a new client task, run these requests in order with explicit profile and customer IDs:

```text
Use Google Ads MCP auth_check for profile PROFILE_NAME and customer PRODUCTION_CLIENT_CUSTOMER_ID.
Use customers_list for profile PROFILE_NAME and confirm the expected Manager and advertiser IDs.
Use customer_hierarchy and verify PRODUCTION_MANAGER_CUSTOMER_ID manages PRODUCTION_CLIENT_CUSTOMER_ID.
Use account_health and report the exact currency code and time zone.
Use campaigns_query and campaign_inventory without changing anything.
Use reports_catalog, then run one bounded report with an explicit completed date range.
Use resource_metadata for campaign.start_date_time and campaign.end_date_time.
Use change_events_query and confirm no change is attributable to this MCP session.
Use server_info and confirm v0.2.0, 22 read/analytics tools, ten-minute receipts, and runtime write opt-in.
List the Google Ads MCP tools and confirm there are exactly 45. Confirm no tool accepts a raw protobuf, mutate payload, Google service name, billing/user operation, upload, delete, or scheduler request.
```

Repeat the auth, discovery, hierarchy, inventory, report, and no-change checks with `test-operator` and `TEST_CLIENT_CUSTOMER_ID`. Empty serving metrics are expected on a test account.

Automated verification is not live-account acceptance. The exact release SHA must separately pass the [live-account, production-read-only, plugin, CI, and security gates](docs/acceptance.md). Google test accounts remain the recommended default; the documented v0.2.0 production-only exception is owner-approved and must be labeled as such.

## Enable the Safe Operator on the isolated test account

Create an owner-only empty policy file:

```bash
google-ads-mcp policies init
```

Edit `~/.config/google-ads-mcp/write-policies.json`. Monetary limits have no automatic defaults; choose explicit values in the customer's currency micros. A narrow first policy looks like this:

```json
{
  "schemaVersion": 1,
  "policies": [
    {
      "policyId": "test-safe",
      "profile": "test-operator",
      "customerId": "TEST_CLIENT_CUSTOMER_ID",
      "currencyCode": "USD",
      "permissions": [
        "search_campaign_create",
        "performance_max_campaign_create",
        "app_campaign_create",
        "campaign_update",
        "campaign_targeting",
        "campaign_status",
        "campaign_budget",
        "campaign_bidding",
        "ad_group_create",
        "ad_group_update",
        "keyword_create",
        "negative_keyword_create",
        "criterion_update",
        "asset_create",
        "asset_link",
        "asset_group_create",
        "ad_create"
      ],
      "allowCampaignEnable": false,
      "campaignIds": [],
      "limits": {
        "maxAggregateDailyBudgetMicros": "10000000",
        "maxCampaignDailyBudgetMicros": "5000000",
        "maxSpendChangeMicrosPerOperation": "1000000",
        "maxBidMicros": "500000"
      },
      "approval": {
        "status": "pending",
        "approvedAt": null,
        "expiresAt": null,
        "policyFingerprint": null
      }
    }
  ]
}
```

Replace `USD` with the exact `account_health` currency and replace the customer placeholder with ten digits. Keep `campaignIds` empty while enable is forbidden; an enable-capable pilot policy must list only its explicitly approved campaign IDs. Validate and approve the unchanged policy for 30 days:

`criterion_update_preview` is intentionally limited to positive ad-group keywords. Campaign location and language criteria cannot be changed through this generic route; use the atomic, one-time `campaign_targeting_preview` while the campaign is `PAUSED`. Enabling a paused ad group or keyword binds its authoritative current/effective CPC into the preview and checks it against `maxBidMicros`. Bidding-strategy changes also require a `PAUSED` campaign, and policy schema v1 always rejects Enhanced CPC.

```bash
chmod 600 ~/.config/google-ads-mcp/write-policies.json
google-ads-mcp policies validate
google-ads-mcp policies approve --policy-id test-safe
google-ads-mcp policies status
```

Restart the test MCP registration with `google-ads-mcp serve --stdio --allow-writes`. Then use this sequence:

```text
Call a typed *_preview tool with profile test-operator, TEST_CLIENT_CUSTOMER_ID, and policyId test-safe.
Inspect before, after, monetaryDelta, risk, validation, and receiptExpiresAt.
Only if the preview is correct, pass its unchanged receipt to operations_apply.
Call operations_inspect and operations_verify until the operation is applied, failed, or otherwise resolved.
Confirm the created campaign remains PAUSED and inspect Google Ads change events.
Attempt to reuse the receipt and confirm receipt_replayed without another Google write.
```

If apply times out or the response is ambiguous, the server does not retry. The operation remains `committed_unverified` until exact direct verification proves the requested after-state; one immediate before-state read does not resolve it as failed. While it is unresolved, all further writes for that profile and customer remain blocked. Recommendation apply/dismiss previews currently return `not_supported` because the pinned v25 recommendation RPCs do not expose `validate_only`.

## Troubleshooting

### `The API Center is only available to manager accounts`

You are signed in to an advertiser, ordinary Google Account, or test account. Switch to the non-test production Manager Account and reopen [API Center](https://ads.google.com/aw/apicenter).

### `This app is blocked`

The single-user OAuth/ADC flow is blocked by Google or organization policy. Prefer the service-account flow. Do not bypass the OAuth policy or move client secrets and refresh tokens into the repository.

### Authentication succeeds but the customer is denied

Check all of the following:

- `loginCustomerId` is the 10-digit Manager Account ID without hyphens, not the advertiser ID;
- the target `customerId` is the advertiser/client ID;
- the Manager → advertiser link was accepted;
- `SERVICE_ACCOUNT_EMAIL` appears under the Manager Account's **Access and security → Users**;
- the service account has Read only access for analytics or an explicitly approved write-capable role for the isolated operator profile;
- the developer token belongs to the production Manager Account associated with the Google Cloud project used for the request.

### Test Account, Explorer, Basic, or Standard access error

Read the current access level in API Center. Test Account access cannot query production. Explorer access is restricted. Basic and Standard require Google approval and ongoing API-policy compliance. Do not treat a pending or test-only token as production-ready.

### Test reports contain no impressions, cost, or conversions

This is expected: test accounts do not serve ads. Use them for configuration and safety acceptance, not serving-data or optimizer-quality claims.

### Currency or time zone is unexpected

Stop before comparing money or date ranges. Confirm the selected customer ID with `account_health`. Money values are customer-currency micros, and report dates use the customer's time zone. Do not silently convert or merge accounts with different currency/time-zone contexts.

### Codex or Claude cannot see the MCP

Restart the client and open a new task after registration. Confirm the command path is absolute or available on the client's `PATH`, the config is owner-only, the system keyring is accessible to the client process, and ADC can read `GOOGLE_APPLICATION_CREDENTIALS`. Never print secret values while diagnosing.

### Write preview returns `blocked`

Check the runtime flag, profile opt-in, exact policy scope, 30-day approval fingerprint, currency, monetary limits, campaign-enable permission, and unresolved customer operations. A block is a safety result; do not bypass it with raw Google API calls.

### Operation is `committed_unverified`

Do not resend the mutation. Use `operations_inspect` and `operations_verify`. The durable `applying` intent exists specifically because a timeout cannot prove whether Google committed the operation.

## Requirements

- Python 3.11, 3.12, or 3.13
- [uv](https://docs.astral.sh/uv/)
- Google Ads API access with a developer token
- Service-account ADC, single-user ADC fallback, or a separately stored owner-only `google-ads.yaml`

Config precedence is `--config`, then `GOOGLE_ADS_MCP_CONFIG`, then `~/.config/google-ads-mcp/accounts.json`.

## CLI

```text
google-ads-mcp version
google-ads-mcp onboard --profile <name>
google-ads-mcp config init
google-ads-mcp config validate
google-ads-mcp auth doctor --profile <name>
google-ads-mcp accounts discover --profile <name>
google-ads-mcp secrets set-developer-token --profile <name>
google-ads-mcp profiles list
google-ads-mcp policies init|validate|approve|revoke|status
google-ads-mcp operations list|inspect|verify
google-ads-mcp client install --target codex|claude
google-ads-mcp serve --stdio [--allow-writes]
```

## MCP tools

The fixed v0.2.0 surface contains 45 tools:

```text
# Original 13 reads
auth_check                customers_list
customer_get              customer_hierarchy
account_health            campaigns_query
campaign_inventory        resource_metadata
reports_catalog           report_run
change_events_query       gaql_validate
gaql_search

# Nine analytics tools
server_info               profiles_list
campaign_diagnostics      search_terms_report
asset_performance_report  conversion_goals_list
recommendations_list      keyword_ideas
forecast_run

# Nineteen typed write previews
search_campaign_create_preview
performance_max_campaign_create_preview
app_campaign_create_preview
campaign_update_preview   campaign_targeting_preview
campaign_status_preview
campaign_budget_preview   campaign_bidding_preview
ad_group_create_preview   ad_group_update_preview
keyword_create_preview    negative_keyword_create_preview
criterion_update_preview  asset_create_preview
asset_link_preview        asset_group_create_preview
ad_create_preview         recommendation_apply_preview
recommendation_dismiss_preview

# The only operation interfaces
operations_apply          operations_inspect
operations_verify         operations_list
```

`forecast_run` accepts typed keyword objects such as `{"text":"voice transcription app","matchType":"EXACT"}`. Match type is preserved in the v25 forecast request; it is never silently converted to broad match.

Typed lists return at most 200 items per page. A single Google read is capped at 1,000 normalized rows. Five-minute cursors are bound to the profile and exact request, with at most 20 active cursors per profile. Safe GAQL has no cursor and requires a single comment-free `SELECT`, a non-sensitive resource, and `LIMIT 1..1000`.

See [docs/tools.md](docs/tools.md) for parameters, evidence semantics, and report behavior.

## Codex plugin

The repository root is the plugin root. `.mcp.json` calls `./bin/google-ads-mcp-launcher`, which only executes the frozen uv environment over stdio. The implicit `google-ads-operator` skill treats all account strings as untrusted data, uses previews before any apply, and never bypasses policy or verification.

The repository intentionally does not create or modify a personal marketplace entry. Release-archive installation is validated separately during acceptance.

## Verification

```bash
uv run --frozen ruff check .
uv run --frozen pyright
uv run --frozen pytest
uv run --frozen python scripts/verify_critical_coverage.py
uv run --frozen python scripts/validate_read_only.py
uv run --frozen python scripts/validate_gaql_templates.py
uv run --frozen python scripts/validate_manifests.py
uv run --frozen python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
uv run --frozen python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/google-ads-operator
```

## License

Apache-2.0. Google Ads is a trademark of Google LLC. This project is independent and is not endorsed by Google.
