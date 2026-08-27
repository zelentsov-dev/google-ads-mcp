# Tool contract

Every account-scoped tool requires explicit `profile` and `customerId`. IDs, resource names, and monetary micros are JSON strings. Null metrics remain null.

Every response includes `status`, `context`, `evidence`, `items`, and `truncated`. `context.contentTrust` is always `untrusted_data`. Status is `ok`, `partial`, `not_supported`, or `unavailable`. `evidence.limitations` is normative: consumers must not silently discard it.

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

## Change events

`change_events_query` enforces the most recent 30-day window. `user_email` is not selected unless `includeUserIdentity` is explicitly true.

## Safe GAQL

`gaql_validate` and `gaql_search` accept one comment-free SELECT statement with one FROM resource and `LIMIT 1..1000`. Billing, user-access, customer-link, offline-user-data, and credential-like resources/fields are denied. Metric queries require a bounded date filter. Safe GAQL has no cursor.
