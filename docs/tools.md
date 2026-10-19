# Tool contract

Every account-scoped tool requires explicit `profile` and `customerId`. IDs, resource names, and monetary micros are JSON strings. Null metrics remain null.

Every response includes `status`, `context`, `evidence`, `items`, and `truncated`. `context.contentTrust` is always `untrusted_data`. Status is `ok`, `partial`, `not_supported`, `unavailable`, or `blocked`. Operation responses additionally include `operation`. `evidence.limitations` is normative: consumers must not silently discard it.

## Discovery and account tools

- `auth_check(profile, customerId)` performs a minimal read.
- `customers_list(profile)` lists accessible customer resource names.
- `customer_get(profile, customerId)` returns account currency, time zone, status, manager, and test-account evidence.
- `customer_hierarchy(profile, customerId)` reads manager-client relationships.
- `account_health(profile, customerId)` returns a bounded configuration snapshot, not a profitability verdict.

## Inventory

`campaigns_query` supports bounded status and channel-type filters. Unknown future channel values remain present with `recognized:false` and `unsupported_specialization`.

`campaign_inventory` requires one `campaignId` and one `resourceType`: `status`, `budget`, `bidding`, `ad_groups`, `ads`, `assets`, `app_assets`, `asset_group_assets`, `criteria`, `keywords`, or `conversions`. When `resourceType="assets"` returns empty on `campaign_asset`, it automatically falls back to `ad_group_ad_asset_view` (App campaigns) and `asset_group_asset` (Performance Max).

## Reports

`reports_catalog` is the source of allowed report names and segments (14 templates, including `asset_performance`, `app_asset_performance`, `pmax_asset_performance`, and `geographic_performance`). `report_run` accepts only a catalog name, ISO `dateFrom` and `dateTo`, optional numeric campaign IDs, optional catalog-approved segments, and a bound cursor. The maximum range is 366 inclusive days.

Generic reports work across campaign types. Specialized reports are available for Search, Shopping, App, Performance Max, Display, Demand Gen, Video, Hotel/Travel, Local Services/Local, and Smart where the pinned API exposes compatible resources. `asset_performance_report` automatically falls back from `campaign_asset` to `ad_group_ad_asset_view` and `asset_group_asset` when `campaign_asset` is empty. An API gap returns `not_supported`, never a false successful empty result.

The v0.2 analytics additions are `campaign_diagnostics`, `search_terms_report`, `asset_performance_report`, `conversion_goals_list`, `recommendations_list`, `keyword_ideas`, and `forecast_run`. `server_info` and `profiles_list` expose non-secret local capability and profile metadata. `keyword_ideas` returns at most the first 200 results and reports `status=partial`, `truncated=true`, and an explicit limitation when a 201st result exists. `forecast_run` requires `{text, matchType}` keyword objects and preserves `EXACT`, `PHRASE`, or `BROAD` in the Google v25 request. Recommendations and forecasts are proposals and estimates, never commands or guaranteed outcomes.

## Change events

`change_events_query` enforces the most recent 30-day window. `user_email` is not selected unless `includeUserIdentity` is explicitly true.

## Safe GAQL

`gaql_validate` and `gaql_search` accept one comment-free SELECT statement with one FROM resource and `LIMIT 1..1000`. Billing, user-access, customer-link, offline-user-data, and credential-like resources/fields are denied. Metric queries require a bounded date filter. Safe GAQL has no cursor.

## Typed operation previews

Twenty `*_preview` tools cover Search, Performance Max, and App campaign creation plus bounded campaign, targeting, budget, bidding, ad-group, criterion, asset, ad (`ad_create_preview` and `ad_update_preview`), and recommendation actions. They accept only documented typed fields. No tool accepts a Google service name, arbitrary operation type, protobuf, or raw mutate payload.

A successful preview returns a signed one-time receipt and this operation contract:

```text
operationId, policyId, kind
state: previewed | applying | applied | failed | partial |
       committed_unverified | expired
receiptExpiresAt, before, after, monetaryDelta, risk,
validation, verification
```

Preview requires `--allow-writes`, profile `allowWrites:true`, an active unchanged 30-day policy, exact profile/customer/currency scope, permitted operation kind, monetary limits, complete current-state reads, and Google `validate_only`. Campaign enable also requires the campaign ID in the fingerprint-bound policy `campaignIds` allowlist. Every new campaign is `PAUSED`, and every create preview rejects an existing exact non-removed campaign, child, criterion, asset, link, group, or ad match instead of creating a duplicate. `campaign_targeting_preview` atomically reconciles positive location and language criteria on a `PAUSED` or `ENABLED` campaign (retaining matching criteria, removing stale positive location/language criteria, and creating newly requested criteria while preserving negative criteria), verifies every constant, and forces positive and negative geo modes to `PRESENCE`. `criterion_update_preview` is limited to positive ad-group keywords, so campaign location/language criteria cannot bypass that atomic targeting route. Status-only ad-group and keyword enables require a `PAUSED` or `ENABLED` parent campaign, bind the authoritative current/effective CPC and source, and enforce `maxBidMicros`. `campaign_bidding_preview` supports `MANUAL_CPC` (with Enhanced CPC explicitly disabled), `TARGET_CPA`, `MAXIMIZE_CONVERSIONS` (with optional `targetCpaMicros`), `MAXIMIZE_CONVERSION_VALUE` (with optional `targetRoas`), and `TARGET_ROAS` on `PAUSED` or `ENABLED` campaigns. `ad_update_preview` updates ad-group-ad status and/or mutable creative fields on `RESPONSIVE_SEARCH_AD` and `APP_AD` ads. Campaign enable supports `SEARCH`, `MULTI_CHANNEL` (`APP_CAMPAIGN`), and `PERFORMANCE_MAX` campaigns that pass channel-appropriate targeting, child-resource, creative/asset-group readiness, budget, and bidding checks. Analytics keyword ideas and forecasts accept 1–20 unique ASCII-numeric geo target IDs. Spend-affecting plans expose unknown impact as null rather than zero and fail closed when current or aggregate exposure cannot be bounded.

Recommendation apply/dismiss previews return `not_supported` while the pinned v25 recommendation RPCs lack `validate_only`.

## Operation tools

- `operations_apply(receipt)` is the only apply interface. It accepts no payload overrides.
- `operations_inspect(operationId)` returns one durable journal record without the raw receipt.
- `operations_verify(operationId)` performs direct reconciliation without resending the mutation.
- `operations_list(profile?, limit?)` returns bounded journal history.

Before a Google write, the journal records `applying`. Write RPCs are never automatically retried. An ambiguous outcome remains `committed_unverified` until exact after-state evidence appears and blocks every further write for the same profile and customer. Create verification requires an authoritative resource name for every item; missing targets or unobservable fields cannot produce `applied`. Normal batches use `partial_failure=false`; internal partial batches require explicitly independent items and remain capped at 100 with item-level results.
