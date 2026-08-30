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

`campaign_inventory` requires one `campaignId` and one `resourceType`: `status`, `budget`, `bidding`, `ad_groups`, `ads`, `assets`, `criteria`, or `conversions`.

## Reports

`reports_catalog` is the source of allowed report names and segments. `report_run` accepts only a catalog name, ISO `dateFrom` and `dateTo`, optional numeric campaign IDs, optional catalog-approved segments, and a bound cursor. The maximum range is 366 inclusive days.

Generic reports work across campaign types. Specialized reports are available for Search, Shopping, App, Performance Max, Display, Demand Gen, Video, Hotel/Travel, Local Services/Local, and Smart where the pinned API exposes compatible resources. An API gap returns `not_supported`, never a false successful empty result.

The v0.2 analytics additions are `campaign_diagnostics`, `search_terms_report`, `asset_performance_report`, `conversion_goals_list`, `recommendations_list`, `keyword_ideas`, and `forecast_run`. `server_info` and `profiles_list` expose non-secret local capability and profile metadata. `keyword_ideas` returns at most the first 200 results and reports `status=partial`, `truncated=true`, and an explicit limitation when a 201st result exists. `forecast_run` requires `{text, matchType}` keyword objects and preserves `EXACT`, `PHRASE`, or `BROAD` in the Google v25 request. Recommendations and forecasts are proposals and estimates, never commands or guaranteed outcomes.

## Change events

`change_events_query` enforces the most recent 30-day window. `user_email` is not selected unless `includeUserIdentity` is explicitly true.

## Safe GAQL

`gaql_validate` and `gaql_search` accept one comment-free SELECT statement with one FROM resource and `LIMIT 1..1000`. Billing, user-access, customer-link, offline-user-data, and credential-like resources/fields are denied. Metric queries require a bounded date filter. Safe GAQL has no cursor.

## Typed operation previews

Nineteen `*_preview` tools cover Search, Performance Max, and App campaign creation plus bounded campaign, targeting, budget, bidding, ad-group, criterion, asset, ad, and recommendation actions. They accept only documented typed fields. No tool accepts a Google service name, arbitrary operation type, protobuf, or raw mutate payload.

A successful preview returns a signed one-time receipt and this operation contract:

```text
operationId, policyId, kind
state: previewed | applying | applied | failed | partial |
       committed_unverified | expired
receiptExpiresAt, before, after, monetaryDelta, risk,
validation, verification
```

Preview requires `--allow-writes`, profile `allowWrites:true`, an active unchanged 30-day policy, exact profile/customer/currency scope, permitted operation kind, monetary limits, complete current-state reads, and Google `validate_only`. Campaign enable also requires the campaign ID in the fingerprint-bound policy `campaignIds` allowlist. Every new campaign is `PAUSED`, and every create preview rejects an existing exact non-removed campaign, child, criterion, asset, link, group, or ad match instead of creating a duplicate. `campaign_targeting_preview` atomically adds one-time explicit location and language criteria to a PAUSED campaign, verifies every constant, and forces positive and negative geo modes to `PRESENCE`. `criterion_update_preview` is limited to positive ad-group keywords, so campaign location/language criteria cannot bypass that atomic targeting route. Status-only ad-group and keyword enables require a PAUSED parent, bind the authoritative current/effective CPC and source, and enforce `maxBidMicros`. Bidding changes require a PAUSED campaign, and policy schema v1 rejects Enhanced CPC. A Search campaign enable additionally requires the policy flag, `MANUAL_CPC` with Enhanced CPC explicitly disabled, presence-only targeting, enabled location and language criteria, an enabled ad group and positive keyword, strict authoritative evidence for every child row, an approved reviewed responsive search ad with non-empty HTTPS final URLs, and a current campaign budget within both per-campaign and aggregate limits. The highest effective CPC and landing URLs are bound into preview evidence. Analytics keyword ideas and forecasts accept 1–20 unique ASCII-numeric geo target IDs. v0.2 does not enable Performance Max or App campaigns. Spend-affecting plans expose unknown impact as null rather than zero and fail closed when current or aggregate exposure cannot be bounded.

Recommendation apply/dismiss previews return `not_supported` while the pinned v25 recommendation RPCs lack `validate_only`. `MAXIMIZE_CONVERSIONS`, `MAXIMIZE_CONVERSION_VALUE`, and `TARGET_ROAS` bidding updates return `not_supported` until the policy schema has corresponding approved monetary limits.

## Operation tools

- `operations_apply(receipt)` is the only apply interface. It accepts no payload overrides.
- `operations_inspect(operationId)` returns one durable journal record without the raw receipt.
- `operations_verify(operationId)` performs direct reconciliation without resending the mutation.
- `operations_list(profile?, limit?)` returns bounded journal history.

Before a Google write, the journal records `applying`. Write RPCs are never automatically retried. An ambiguous outcome remains `committed_unverified` until exact after-state evidence appears and blocks every further write for the same profile and customer. Create verification requires an authoritative resource name for every item; missing targets or unobservable fields cannot produce `applied`. Normal batches use `partial_failure=false`; internal partial batches require explicitly independent items and remain capped at 100 with item-level results.
