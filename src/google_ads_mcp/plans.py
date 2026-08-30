from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from google_ads_mcp.errors import NotSupportedError, ValidationError
from google_ads_mcp.write_models import MutationItem, WriteKind, WritePlan

_RESOURCE_NAME = re.compile(
    r"^customers/([0-9]{10})/([A-Za-z]+)/(-?[A-Za-z0-9_.~]+)$"
)
_MATCH_TYPES = frozenset({"BROAD", "PHRASE", "EXACT"})
_STATUSES = frozenset({"ENABLED", "PAUSED"})
_AGGREGATE_BUDGET_QUERY = (
    "SELECT campaign.id, campaign.status, campaign.campaign_budget, "
    "campaign_budget.resource_name, campaign_budget.amount_micros "
    "FROM campaign WHERE campaign.status != 'REMOVED' LIMIT 1000"
)


def _id(value: object, *, field: str) -> str:
    rendered = str(value)
    if not rendered.isascii() or not rendered.isdigit():
        raise ValidationError(f"{field} must contain digits only")
    return rendered


def _micros(value: object, *, field: str, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    rendered = str(value)
    if not rendered.isascii() or not rendered.isdigit() or int(rendered) <= 0:
        raise ValidationError(f"{field} must be a positive integer string")
    return int(rendered)


def _name(value: object, *, field: str = "name", maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    rendered = value.strip()
    if (
        not rendered
        or len(rendered) > maximum
        or any(ord(character) < 32 for character in rendered)
    ):
        raise ValidationError(f"{field} must contain between 1 and {maximum} safe characters")
    return rendered


def _url(value: object, *, field: str = "finalUrl") -> str:
    if not isinstance(value, str) or not value.startswith(("https://", "http://")):
        raise ValidationError(f"{field} must be an HTTP or HTTPS URL")
    if len(value) > 2048 or any(character.isspace() for character in value):
        raise ValidationError(f"{field} is too long or malformed")
    return value


def _resource(value: object, *, customer_id: str, collection: str, field: str) -> str:
    rendered = str(value)
    match = _RESOURCE_NAME.fullmatch(rendered)
    if match is None or match.group(1) != customer_id or match.group(2) != collection:
        raise ValidationError(f"{field} must be a {collection} resource name for this customer")
    return rendered


def _resource_name(customer_id: str, collection: str, identifier: str | int) -> str:
    return f"customers/{customer_id}/{collection}/{identifier}"


def _quoted(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _texts(
    values: Iterable[object], *, field: str, minimum: int, maximum: int, item_limit: int
) -> tuple[str, ...]:
    result = tuple(_name(value, field=field, maximum=item_limit) for value in values)
    if len(result) < minimum or len(result) > maximum:
        raise ValidationError(f"{field} must contain between {minimum} and {maximum} values")
    return result


def _ids(
    values: object, *, field: str, minimum: int, maximum: int
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValidationError(f"{field} must be an array")
    result = tuple(_id(value, field=field) for value in values)
    if len(result) < minimum or len(result) > maximum:
        raise ValidationError(f"{field} must contain between {minimum} and {maximum} values")
    if len(set(result)) != len(result):
        raise ValidationError(f"{field} must not contain duplicates")
    return result


def _campaign_declaration(contains_eu_political_advertising: bool) -> str:
    return (
        "CONTAINS_EU_POLITICAL_ADVERTISING"
        if contains_eu_political_advertising
        else "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
    )


def _campaign_datetime(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must use yyyy-MM-dd HH:mm:ss")
    try:
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ValidationError(f"{field} must use yyyy-MM-dd HH:mm:ss") from exc
    return value


def state_queries(
    kind: WriteKind, customer_id: str, payload: dict[str, Any]
) -> tuple[tuple[str, str], ...]:
    aggregate = ("aggregate_budgets", _AGGREGATE_BUDGET_QUERY)
    if kind.endswith("campaign_create"):
        name = _quoted(_name(payload.get("name")))
        return (
            (
                "campaign_name",
                "SELECT campaign.id, campaign.name, campaign.status FROM campaign "
                f"WHERE campaign.name = '{name}' AND campaign.status != 'REMOVED' LIMIT 2",
            ),
            aggregate,
        )
    if kind == "campaign_targeting":
        campaign_id = _id(payload.get("campaignId"), field="campaignId")
        geo_ids = _ids(
            payload.get("geoTargetIds"),
            field="geoTargetIds",
            minimum=1,
            maximum=20,
        )
        language_ids = _ids(
            payload.get("languageIds"),
            field="languageIds",
            minimum=1,
            maximum=10,
        )
        return (
            (
                "campaign_targeting_parent",
                "SELECT campaign.id, campaign.status, campaign.advertising_channel_type, "
                "campaign.campaign_budget, campaign_budget.resource_name, "
                "campaign_budget.amount_micros, "
                "campaign.bidding_strategy_type, "
                "campaign.manual_cpc.enhanced_cpc_enabled, "
                "campaign.geo_target_type_setting.positive_geo_target_type, "
                "campaign.geo_target_type_setting.negative_geo_target_type FROM campaign "
                f"WHERE campaign.id = {campaign_id} LIMIT 1",
            ),
            (
                "campaign_targeting_existing",
                "SELECT campaign_criterion.criterion_id, campaign_criterion.resource_name, "
                "campaign_criterion.type, campaign_criterion.status, "
                "campaign_criterion.negative, "
                "campaign_criterion.location.geo_target_constant, "
                "campaign_criterion.language.language_constant FROM campaign_criterion "
                f"WHERE campaign.id = {campaign_id} "
                "AND campaign_criterion.type IN ('LOCATION', 'LANGUAGE') "
                "AND campaign_criterion.status != 'REMOVED' LIMIT 1000",
            ),
            (
                "geo_target_constants",
                "SELECT geo_target_constant.id, geo_target_constant.resource_name, "
                "geo_target_constant.name, geo_target_constant.country_code, "
                "geo_target_constant.target_type, geo_target_constant.status "
                "FROM geo_target_constant WHERE geo_target_constant.id IN "
                f"({', '.join(geo_ids)}) LIMIT {len(geo_ids)}",
            ),
            (
                "language_constants",
                "SELECT language_constant.id, language_constant.resource_name, "
                "language_constant.name, language_constant.code, "
                "language_constant.targetable FROM language_constant "
                "WHERE language_constant.id IN "
                f"({', '.join(language_ids)}) LIMIT {len(language_ids)}",
            ),
        )
    if kind == "campaign_status" and str(payload.get("status")) == "ENABLED":
        campaign_id = _id(payload.get("campaignId"), field="campaignId")
        return (
            (
                "campaign_enable_parent",
                "SELECT campaign.id, campaign.status, campaign.advertising_channel_type, "
                "campaign.campaign_budget, campaign_budget.resource_name, "
                "campaign_budget.amount_micros, "
                "campaign.bidding_strategy_type, "
                "campaign.manual_cpc.enhanced_cpc_enabled, "
                "campaign.geo_target_type_setting.positive_geo_target_type, "
                "campaign.geo_target_type_setting.negative_geo_target_type FROM campaign "
                f"WHERE campaign.id = {campaign_id} LIMIT 1",
            ),
            (
                "campaign_enable_targeting",
                "SELECT campaign_criterion.criterion_id, campaign_criterion.resource_name, "
                "campaign_criterion.type, "
                "campaign_criterion.status, campaign_criterion.negative, "
                "campaign_criterion.location.geo_target_constant, "
                "campaign_criterion.language.language_constant FROM campaign_criterion "
                f"WHERE campaign.id = {campaign_id} "
                "AND campaign_criterion.type IN ('LOCATION', 'LANGUAGE') "
                "AND campaign_criterion.status != 'REMOVED' LIMIT 1000",
            ),
            (
                "campaign_enable_ad_groups",
                "SELECT campaign.id, ad_group.id, ad_group.status, "
                "ad_group.cpc_bid_micros FROM ad_group "
                f"WHERE campaign.id = {campaign_id} "
                "AND ad_group.status != 'REMOVED' LIMIT 1000",
            ),
            (
                "campaign_enable_keywords",
                "SELECT campaign.id, ad_group.id, ad_group.status, "
                "ad_group_criterion.criterion_id, ad_group_criterion.status, "
                "ad_group_criterion.negative, ad_group_criterion.type, "
                "ad_group_criterion.keyword.text, "
                "ad_group_criterion.keyword.match_type, "
                "ad_group_criterion.effective_cpc_bid_micros, "
                "ad_group_criterion.effective_cpc_bid_source FROM keyword_view "
                f"WHERE campaign.id = {campaign_id} "
                "AND ad_group_criterion.status != 'REMOVED' LIMIT 1000",
            ),
            (
                "campaign_enable_ads",
                "SELECT campaign.id, ad_group.id, ad_group.status, "
                "ad_group_ad.ad.id, ad_group_ad.status, ad_group_ad.ad.type, "
                "ad_group_ad.ad.final_urls, "
                "ad_group_ad.policy_summary.approval_status, "
                "ad_group_ad.policy_summary.review_status FROM ad_group_ad "
                f"WHERE campaign.id = {campaign_id} "
                "AND ad_group_ad.status != 'REMOVED' LIMIT 1000",
            ),
        )
    if kind in {"campaign_update", "campaign_status", "campaign_bidding"}:
        campaign_id = _id(payload.get("campaignId"), field="campaignId")
        return (
            (
                "campaign",
                "SELECT campaign.id, campaign.name, campaign.status, campaign.start_date_time, "
                "campaign.end_date_time, campaign.final_url_suffix, campaign.campaign_budget, "
                "campaign_budget.resource_name, campaign_budget.amount_micros, "
                "campaign.bidding_strategy_type, campaign.manual_cpc.enhanced_cpc_enabled, "
                "campaign.target_cpa.target_cpa_micros, campaign.target_roas.target_roas "
                f"FROM campaign WHERE campaign.id = {campaign_id} LIMIT 1",
            ),
        )
    if kind == "campaign_budget":
        budget_id = _id(payload.get("budgetId"), field="budgetId")
        return (
            (
                "campaign_budget",
                "SELECT campaign_budget.id, campaign_budget.name, campaign_budget.amount_micros, "
                "campaign_budget.explicitly_shared, campaign_budget.status FROM campaign_budget "
                f"WHERE campaign_budget.id = {budget_id} LIMIT 1",
            ),
            aggregate,
        )
    if kind.startswith("ad_group_"):
        identifier = payload.get("adGroupId")
        if kind == "ad_group_create":
            campaign_id = _id(payload.get("campaignId"), field="campaignId")
            name = _quoted(_name(payload.get("name")))
            return (
                (
                    "ad_group_name",
                    "SELECT ad_group.id, ad_group.name, ad_group.status FROM ad_group "
                    f"WHERE campaign.id = {campaign_id} AND ad_group.name = '{name}' "
                    "AND ad_group.status != 'REMOVED' LIMIT 2",
                ),
                _parent_campaign_budget_query("campaign", campaign_id),
            )
        ad_group_id = _id(identifier, field="adGroupId")
        return (
            (
                "ad_group",
                "SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.cpc_bid_micros, "
                "ad_group.cpm_bid_micros, ad_group.cpv_bid_micros, ad_group.target_cpa_micros, "
                "ad_group.target_roas FROM ad_group "
                f"WHERE ad_group.id = {ad_group_id} LIMIT 1",
            ),
            _parent_campaign_budget_query("ad_group", ad_group_id),
        )
    if kind in {"keyword_create", "negative_keyword_create"}:
        text = _quoted(_name(payload.get("text"), field="text", maximum=80))
        parent_id = _id(payload.get("adGroupId") or payload.get("campaignId"), field="parentId")
        if kind == "keyword_create" or payload.get("level") == "AD_GROUP":
            return (
                (
                    "criterion_text",
                    "SELECT ad_group_criterion.criterion_id, ad_group_criterion.status, "
                    "ad_group_criterion.negative, ad_group_criterion.keyword.text, "
                    "ad_group_criterion.keyword.match_type FROM keyword_view "
                    f"WHERE ad_group.id = {parent_id} "
                    f"AND ad_group_criterion.keyword.text = '{text}' "
                    "AND ad_group_criterion.status != 'REMOVED' LIMIT 2",
                ),
                _parent_campaign_budget_query("ad_group", parent_id),
            )
        return (
            (
                "criterion_text",
                "SELECT campaign_criterion.criterion_id, campaign_criterion.status, "
                "campaign_criterion.negative, "
                "campaign_criterion.keyword.text, campaign_criterion.keyword.match_type "
                "FROM campaign_criterion "
                f"WHERE campaign.id = {parent_id} "
                f"AND campaign_criterion.keyword.text = '{text}' "
                "AND campaign_criterion.status != 'REMOVED' LIMIT 2",
            ),
            _parent_campaign_budget_query("campaign", parent_id),
        )
    if kind == "criterion_update":
        criterion_id = _id(payload.get("criterionId"), field="criterionId")
        level = str(payload.get("level"))
        if level != "AD_GROUP":
            raise ValidationError("criterion_update_preview supports AD_GROUP keywords only")
        parent_id = _id(payload.get("adGroupId"), field="adGroupId")
        return (
            (
                "criterion",
                "SELECT ad_group_criterion.criterion_id, ad_group_criterion.status, "
                "ad_group_criterion.negative, ad_group_criterion.type, "
                "ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
                "ad_group_criterion.effective_cpc_bid_micros, "
                "ad_group_criterion.effective_cpc_bid_source FROM ad_group_criterion "
                f"WHERE ad_group.id = {parent_id} "
                f"AND ad_group_criterion.criterion_id = {criterion_id} LIMIT 1",
            ),
            _parent_campaign_budget_query("ad_group", parent_id),
        )
    if kind == "asset_create":
        name = _quoted(_name(payload.get("name")))
        return (
            (
                "asset_name",
                "SELECT asset.id, asset.name, asset.type FROM asset "
                f"WHERE asset.name = '{name}' LIMIT 2",
            ),
        )
    if kind == "asset_link":
        owner_type = str(payload.get("ownerType"))
        owner_id = _id(payload.get("ownerId"), field="ownerId")
        link_query = _asset_link_query(customer_id, payload)
        parent_resource = {
            "CAMPAIGN": "campaign",
            "AD_GROUP": "ad_group",
            "ASSET_GROUP": "asset_group",
        }[owner_type]
        return (
            ("asset_link", link_query),
            _parent_campaign_budget_query(parent_resource, owner_id),
        )
    if kind == "asset_group_create":
        campaign_id = _id(payload.get("campaignId"), field="campaignId")
        name = _quoted(_name(payload.get("name")))
        return (
            (
                "asset_group_name",
                "SELECT asset_group.id, asset_group.name, asset_group.status FROM asset_group "
                f"WHERE campaign.id = {campaign_id} AND asset_group.name = '{name}' "
                "AND asset_group.status != 'REMOVED' LIMIT 2",
            ),
            _parent_campaign_budget_query("campaign", campaign_id),
        )
    if kind == "ad_create":
        ad_group_id = _id(payload.get("adGroupId"), field="adGroupId")
        name = _quoted(_name(payload.get("name")))
        return (
            (
                "ad_name",
                "SELECT ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.status, "
                "ad_group_ad.ad.type FROM ad_group_ad "
                f"WHERE ad_group.id = {ad_group_id} "
                f"AND ad_group_ad.ad.name = '{name}' "
                "AND ad_group_ad.status != 'REMOVED' LIMIT 2",
            ),
        )
    recommendation = _resource(
        payload.get("recommendationResourceName"),
        customer_id=customer_id,
        collection="recommendations",
        field="recommendationResourceName",
    )
    return (
        (
            "recommendation",
            "SELECT recommendation.resource_name, recommendation.type, recommendation.dismissed "
            f"FROM recommendation WHERE recommendation.resource_name = '{recommendation}' LIMIT 1",
        ),
    )


def _asset_link_query(customer_id: str, payload: dict[str, Any]) -> str:
    owner_type = str(payload.get("ownerType"))
    asset_name = _resource(
        payload.get("assetResourceName"),
        customer_id=customer_id,
        collection="assets",
        field="assetResourceName",
    )
    if owner_type == "CAMPAIGN":
        owner_id = _id(payload.get("ownerId"), field="ownerId")
        return (
            "SELECT campaign.id, campaign_asset.resource_name, campaign_asset.status, "
            "campaign_asset.field_type "
            f"FROM campaign_asset WHERE campaign.id = {owner_id} "
            f"AND campaign_asset.asset = '{asset_name}' "
            "AND campaign_asset.status != 'REMOVED' LIMIT 1"
        )
    if owner_type == "AD_GROUP":
        owner_id = _id(payload.get("ownerId"), field="ownerId")
        return (
            "SELECT ad_group.id, ad_group_asset.resource_name, ad_group_asset.status, "
            "ad_group_asset.field_type "
            f"FROM ad_group_asset WHERE ad_group.id = {owner_id} "
            f"AND ad_group_asset.asset = '{asset_name}' "
            "AND ad_group_asset.status != 'REMOVED' LIMIT 1"
        )
    if owner_type == "ASSET_GROUP":
        owner_id = _id(payload.get("ownerId"), field="ownerId")
        return (
            "SELECT asset_group.id, asset_group_asset.resource_name, "
            "asset_group_asset.status, asset_group_asset.field_type FROM asset_group_asset "
            f"WHERE asset_group.id = {owner_id} "
            f"AND asset_group_asset.asset = '{asset_name}' "
            "AND asset_group_asset.status != 'REMOVED' LIMIT 1"
        )
    raise ValidationError("ownerType must be CAMPAIGN, AD_GROUP, or ASSET_GROUP")


def _parent_campaign_budget_query(
    parent_resource: str, parent_id: str
) -> tuple[str, str]:
    parent_field = f"{parent_resource}.id"
    if parent_resource != "campaign":
        return (
            "parent_campaign_budget",
            f"SELECT {parent_field}, campaign.id, campaign.status, "
            f"campaign.campaign_budget FROM {parent_resource} "
            f"WHERE {parent_field} = {parent_id} LIMIT 1",
        )
    return (
        "parent_campaign_budget",
        "SELECT campaign.id, campaign.status, campaign.campaign_budget, "
        "campaign_budget.resource_name, "
        "campaign_budget.amount_micros FROM campaign "
        f"WHERE {parent_field} = {parent_id} LIMIT 1",
    )


def aggregate_budget_query() -> str:
    return _AGGREGATE_BUDGET_QUERY


class PlanBuilder:
    def build(
        self,
        kind: WriteKind,
        *,
        profile: str,
        customer_id: str,
        policy_id: str,
        payload: dict[str, Any],
        before: dict[str, Any],
    ) -> WritePlan:
        builder = getattr(self, f"_build_{kind}", None)
        if builder is None:
            raise ValidationError("Unsupported typed write operation")
        return builder(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            payload=payload,
            before=before,
        )

    @staticmethod
    def _plan(
        *,
        profile: str,
        customer_id: str,
        policy_id: str,
        kind: WriteKind,
        related_key: str,
        before: dict[str, Any],
        after: dict[str, Any],
        items: list[MutationItem],
        readback_queries: tuple[str, ...],
        spend_delta: int | None = None,
        spend_affecting: bool = False,
    ) -> WritePlan:
        return WritePlan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind=kind,
            related_key=related_key,
            before=before,
            after=after,
            items=tuple(items),
            readback_queries=readback_queries,
            monetary_delta={
                "spendMicros": (
                    str(spend_delta)
                    if spend_delta is not None
                    else (None if spend_affecting else "0")
                )
            },
            risk={
                "spendAffecting": spend_affecting,
                "createsEnabledCampaign": False,
                "destructive": False,
            },
        )

    def _build_search_campaign_create(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        name = _name(payload.get("name"))
        _reject_existing_match(before, "A non-removed campaign with this name")
        budget = _micros(payload.get("dailyBudgetMicros"), field="dailyBudgetMicros")
        bid = _micros(payload.get("cpcBidMicros"), field="cpcBidMicros")
        assert budget is not None
        assert bid is not None
        ad_group_name = _name(payload.get("adGroupName"), field="adGroupName")
        final_url = _url(payload.get("finalUrl"))
        headlines = _texts(
            payload.get("headlines", ()), field="headlines", minimum=3, maximum=15, item_limit=30
        )
        descriptions = _texts(
            payload.get("descriptions", ()),
            field="descriptions",
            minimum=2,
            maximum=4,
            item_limit=90,
        )
        keywords = _texts(
            payload.get("keywords", ()), field="keywords", minimum=1, maximum=50, item_limit=80
        )
        match_type = str(payload.get("keywordMatchType", "PHRASE"))
        if match_type not in _MATCH_TYPES:
            raise ValidationError("keywordMatchType is invalid")
        budget_name = _resource_name(customer_id, "campaignBudgets", -1)
        campaign_name = _resource_name(customer_id, "campaigns", -2)
        ad_group_resource = _resource_name(customer_id, "adGroups", -3)
        items = [
            MutationItem(
                "campaign_budget_operation",
                "create",
                {
                    "resource_name": budget_name,
                    "amount_micros": budget,
                    "delivery_method": "STANDARD",
                    "explicitly_shared": False,
                },
                correlation_id="budget",
            ),
            MutationItem(
                "campaign_operation",
                "create",
                {
                    "resource_name": campaign_name,
                    "name": name,
                    "campaign_budget": budget_name,
                    "status": "PAUSED",
                    "advertising_channel_type": "SEARCH",
                    "network_settings": {
                        "target_google_search": True,
                        "target_search_network": True,
                        "target_content_network": False,
                        "target_partner_search_network": False,
                    },
                    "manual_cpc": {"enhanced_cpc_enabled": False},
                    "contains_eu_political_advertising": _campaign_declaration(
                        bool(payload.get("containsEuPoliticalAdvertising"))
                    ),
                },
                correlation_id="campaign",
            ),
            MutationItem(
                "ad_group_operation",
                "create",
                {
                    "resource_name": ad_group_resource,
                    "campaign": campaign_name,
                    "name": ad_group_name,
                    "status": "ENABLED",
                    "type_": "SEARCH_STANDARD",
                    "cpc_bid_micros": bid,
                },
                correlation_id="ad-group",
            ),
        ]
        for index, keyword in enumerate(keywords):
            items.append(
                MutationItem(
                    "ad_group_criterion_operation",
                    "create",
                    {
                        "ad_group": ad_group_resource,
                        "status": "ENABLED",
                        "negative": False,
                        "keyword": {"text": keyword, "match_type": match_type},
                    },
                    correlation_id=f"keyword-{index + 1}",
                )
            )
        items.append(
            MutationItem(
                "ad_group_ad_operation",
                "create",
                {
                    "ad_group": ad_group_resource,
                    "status": "ENABLED",
                    "ad": {
                        "name": f"{name} responsive search ad",
                        "final_urls": [final_url],
                        "responsive_search_ad": {
                            "headlines": [{"text": text} for text in headlines],
                            "descriptions": [{"text": text} for text in descriptions],
                        },
                    },
                },
                correlation_id="ad",
            )
        )
        after = {
            "name": name,
            "status": "PAUSED",
            "dailyBudgetMicros": str(budget),
            "bidMicros": str(bid),
            "advertisingChannelType": "SEARCH",
        }
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="search_campaign_create",
            related_key=f"campaign-name:{name.casefold()}",
            before=before,
            after=after,
            items=items,
            readback_queries=tuple(
                query for _, query in state_queries("search_campaign_create", customer_id, payload)
            ),
            spend_delta=budget,
            spend_affecting=True,
        )

    def _build_performance_max_campaign_create(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        name = _name(payload.get("name"))
        _reject_existing_match(before, "A non-removed campaign with this name")
        budget = _micros(payload.get("dailyBudgetMicros"), field="dailyBudgetMicros")
        assert budget is not None
        final_url = _url(payload.get("finalUrl"))
        asset_group_name = _name(payload.get("assetGroupName"), field="assetGroupName")
        headlines = _texts(
            payload.get("headlines", ()), field="headlines", minimum=3, maximum=15, item_limit=30
        )
        long_headlines = _texts(
            payload.get("longHeadlines", ()),
            field="longHeadlines",
            minimum=1,
            maximum=5,
            item_limit=90,
        )
        descriptions = _texts(
            payload.get("descriptions", ()),
            field="descriptions",
            minimum=2,
            maximum=5,
            item_limit=90,
        )
        business_name = _name(payload.get("businessName"), field="businessName", maximum=25)
        image_groups = (
            ("landscapeImageAssets", "MARKETING_IMAGE"),
            ("squareImageAssets", "SQUARE_MARKETING_IMAGE"),
            ("logoAssets", "LOGO"),
        )
        existing_assets: list[tuple[str, str]] = []
        for field, field_type in image_groups:
            raw_names = payload.get(field)
            if not isinstance(raw_names, (list, tuple)) or not raw_names:
                raise ValidationError(f"{field} must contain at least one asset resource name")
            existing_assets.extend(
                (
                    _resource(
                        item,
                        customer_id=customer_id,
                        collection="assets",
                        field=field,
                    ),
                    field_type,
                )
                for item in raw_names
            )
        youtube_assets = tuple(payload.get("youtubeVideoAssets", ()))
        existing_assets.extend(
            (
                _resource(
                    item,
                    customer_id=customer_id,
                    collection="assets",
                    field="youtubeVideoAssets",
                ),
                "YOUTUBE_VIDEO",
            )
            for item in youtube_assets
        )
        budget_name = _resource_name(customer_id, "campaignBudgets", -1)
        campaign_name = _resource_name(customer_id, "campaigns", -2)
        asset_group_name_resource = _resource_name(customer_id, "assetGroups", -3)
        items = [
            MutationItem(
                "campaign_budget_operation",
                "create",
                {
                    "resource_name": budget_name,
                    "amount_micros": budget,
                    "delivery_method": "STANDARD",
                    "explicitly_shared": False,
                },
                correlation_id="budget",
            ),
            MutationItem(
                "campaign_operation",
                "create",
                {
                    "resource_name": campaign_name,
                    "name": name,
                    "campaign_budget": budget_name,
                    "status": "PAUSED",
                    "advertising_channel_type": "PERFORMANCE_MAX",
                    "maximize_conversions": {},
                    "contains_eu_political_advertising": _campaign_declaration(
                        bool(payload.get("containsEuPoliticalAdvertising"))
                    ),
                },
                correlation_id="campaign",
            ),
        ]
        text_assets = [
            *((text, "HEADLINE") for text in headlines),
            *((text, "LONG_HEADLINE") for text in long_headlines),
            *((text, "DESCRIPTION") for text in descriptions),
            (business_name, "BUSINESS_NAME"),
        ]
        linked_assets: list[tuple[str, str]] = []
        for index, (text, field_type) in enumerate(text_assets, start=10):
            resource_name = _resource_name(customer_id, "assets", -index)
            items.append(
                MutationItem(
                    "asset_operation",
                    "create",
                    {"resource_name": resource_name, "text_asset": {"text": text}},
                    correlation_id=f"asset-{index}",
                )
            )
            linked_assets.append((resource_name, field_type))
        items.append(
            MutationItem(
                "asset_group_operation",
                "create",
                {
                    "resource_name": asset_group_name_resource,
                    "campaign": campaign_name,
                    "name": asset_group_name,
                    "final_urls": [final_url],
                    "status": "ENABLED",
                },
                correlation_id="asset-group",
            )
        )
        for index, (asset, field_type) in enumerate(linked_assets + existing_assets, start=1):
            items.append(
                MutationItem(
                    "asset_group_asset_operation",
                    "create",
                    {
                        "asset_group": asset_group_name_resource,
                        "asset": asset,
                        "field_type": field_type,
                    },
                    correlation_id=f"asset-link-{index}",
                )
            )
        after = {
            "name": name,
            "status": "PAUSED",
            "dailyBudgetMicros": str(budget),
            "advertisingChannelType": "PERFORMANCE_MAX",
            "assetGroupName": asset_group_name,
        }
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="performance_max_campaign_create",
            related_key=f"campaign-name:{name.casefold()}",
            before=before,
            after=after,
            items=items,
            readback_queries=tuple(
                query
                for _, query in state_queries(
                    "performance_max_campaign_create", customer_id, payload
                )
            ),
            spend_delta=budget,
            spend_affecting=True,
        )

    def _build_app_campaign_create(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        name = _name(payload.get("name"))
        _reject_existing_match(before, "A non-removed campaign with this name")
        budget = _micros(payload.get("dailyBudgetMicros"), field="dailyBudgetMicros")
        target_cpa = _micros(payload.get("targetCpaMicros"), field="targetCpaMicros")
        assert budget is not None
        assert target_cpa is not None
        app_id = _name(payload.get("appId"), field="appId", maximum=255)
        app_store = str(payload.get("appStore"))
        if app_store not in {"APPLE_APP_STORE", "GOOGLE_APP_STORE"}:
            raise ValidationError("appStore is invalid")
        ad_group_name = _name(payload.get("adGroupName"), field="adGroupName")
        ad_name = _name(payload.get("adName"), field="adName")
        headlines = _texts(
            payload.get("headlines", ()), field="headlines", minimum=2, maximum=5, item_limit=30
        )
        descriptions = _texts(
            payload.get("descriptions", ()),
            field="descriptions",
            minimum=1,
            maximum=5,
            item_limit=90,
        )
        image_assets = tuple(
            _resource(
                item,
                customer_id=customer_id,
                collection="assets",
                field="imageAssets",
            )
            for item in payload.get("imageAssets", ())
        )
        video_assets = tuple(
            _resource(
                item,
                customer_id=customer_id,
                collection="assets",
                field="youtubeVideoAssets",
            )
            for item in payload.get("youtubeVideoAssets", ())
        )
        if not image_assets and not video_assets:
            raise ValidationError("App campaign ad requires imageAssets or youtubeVideoAssets")
        budget_name = _resource_name(customer_id, "campaignBudgets", -1)
        campaign_name = _resource_name(customer_id, "campaigns", -2)
        ad_group_resource = _resource_name(customer_id, "adGroups", -3)
        items = [
            MutationItem(
                "campaign_budget_operation",
                "create",
                {
                    "resource_name": budget_name,
                    "amount_micros": budget,
                    "delivery_method": "STANDARD",
                    "explicitly_shared": False,
                },
                correlation_id="budget",
            ),
            MutationItem(
                "campaign_operation",
                "create",
                {
                    "resource_name": campaign_name,
                    "name": name,
                    "campaign_budget": budget_name,
                    "status": "PAUSED",
                    "advertising_channel_type": "MULTI_CHANNEL",
                    "advertising_channel_sub_type": "APP_CAMPAIGN",
                    "app_campaign_setting": {
                        "app_id": app_id,
                        "app_store": app_store,
                        "bidding_strategy_goal_type": "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
                    },
                    "target_cpa": {"target_cpa_micros": target_cpa},
                    "contains_eu_political_advertising": _campaign_declaration(
                        bool(payload.get("containsEuPoliticalAdvertising"))
                    ),
                },
                correlation_id="campaign",
            ),
            MutationItem(
                "ad_group_operation",
                "create",
                {
                    "resource_name": ad_group_resource,
                    "campaign": campaign_name,
                    "name": ad_group_name,
                    "status": "ENABLED",
                },
                correlation_id="ad-group",
            ),
            MutationItem(
                "ad_group_ad_operation",
                "create",
                {
                    "ad_group": ad_group_resource,
                    "status": "ENABLED",
                    "ad": {
                        "name": ad_name,
                        "app_ad": {
                            "headlines": [{"text": text} for text in headlines],
                            "descriptions": [{"text": text} for text in descriptions],
                            "images": [{"asset": item} for item in image_assets],
                            "youtube_videos": [{"asset": item} for item in video_assets],
                        },
                    },
                },
                correlation_id="ad",
            ),
        ]
        after = {
            "name": name,
            "status": "PAUSED",
            "dailyBudgetMicros": str(budget),
            "targetCpaMicros": str(target_cpa),
            "advertisingChannelType": "MULTI_CHANNEL",
            "advertisingChannelSubType": "APP_CAMPAIGN",
        }
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="app_campaign_create",
            related_key=f"campaign-name:{name.casefold()}",
            before=before,
            after=after,
            items=items,
            readback_queries=tuple(
                query for _, query in state_queries("app_campaign_create", customer_id, payload)
            ),
            spend_delta=budget,
            spend_affecting=True,
        )

    def _build_campaign_update(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        campaign_id = _id(payload.get("campaignId"), field="campaignId")
        resource: dict[str, Any] = {
            "resource_name": _resource_name(customer_id, "campaigns", campaign_id)
        }
        mask: list[str] = []
        after: dict[str, Any] = {"campaignId": campaign_id}
        for public, proto in {
            "name": "name",
            "startDateTime": "start_date_time",
            "endDateTime": "end_date_time",
            "finalUrlSuffix": "final_url_suffix",
        }.items():
            if payload.get(public) is not None:
                if public in {"startDateTime", "endDateTime"}:
                    value = _campaign_datetime(payload[public], field=public)
                else:
                    value = _name(payload[public], field=public, maximum=2048)
                resource[proto] = value
                mask.append(proto)
                after[public] = value
        if (
            "startDateTime" in after
            and "endDateTime" in after
            and after["endDateTime"] <= after["startDateTime"]
        ):
            raise ValidationError("endDateTime must be later than startDateTime")
        if not mask:
            raise ValidationError("campaign_update_preview requires at least one changed field")
        spend_affecting = bool({"start_date_time", "end_date_time"} & set(mask))
        current_budget = _find_integer(before, "amount_micros")
        if spend_affecting and current_budget is not None:
            after["currentCampaignDailyBudgetMicros"] = str(current_budget)
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="campaign_update",
            related_key=f"campaign:{campaign_id}",
            before=before,
            after=after,
            items=[
                MutationItem(
                    "campaign_operation",
                    "update",
                    resource,
                    tuple(mask),
                    "campaign",
                )
            ],
            readback_queries=tuple(
                query for _, query in state_queries("campaign_update", customer_id, payload)
            ),
            spend_affecting=spend_affecting,
        )

    def _build_campaign_targeting(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        campaign_id = _id(payload.get("campaignId"), field="campaignId")
        geo_ids = _ids(
            payload.get("geoTargetIds"),
            field="geoTargetIds",
            minimum=1,
            maximum=20,
        )
        language_ids = _ids(
            payload.get("languageIds"),
            field="languageIds",
            minimum=1,
            maximum=10,
        )
        parent, budget = _campaign_state(before, campaign_id, require_channel=True)
        if str(parent.get("status")) != "PAUSED":
            raise ValidationError(
                "Campaign targeting can only be added while the campaign is PAUSED"
            )
        if _targeting_rows(before):
            raise ValidationError("Campaign already has location or language targeting")
        _require_geo_constants(before, geo_ids)
        _require_language_constants(before, language_ids)
        campaign_resource = _resource_name(customer_id, "campaigns", campaign_id)
        items = [
            MutationItem(
                "campaign_operation",
                "update",
                {
                    "resource_name": campaign_resource,
                    "geo_target_type_setting": {
                        "positive_geo_target_type": "PRESENCE",
                        "negative_geo_target_type": "PRESENCE",
                    },
                },
                (
                    "geo_target_type_setting.positive_geo_target_type",
                    "geo_target_type_setting.negative_geo_target_type",
                ),
                "campaign-geo-mode",
            )
        ]
        for index, geo_id in enumerate(geo_ids, start=1):
            items.append(
                MutationItem(
                    "campaign_criterion_operation",
                    "create",
                    {
                        "campaign": campaign_resource,
                        "status": "ENABLED",
                        "negative": False,
                        "location": {
                            "geo_target_constant": f"geoTargetConstants/{geo_id}"
                        },
                    },
                    correlation_id=f"location-{index}",
                )
            )
        for index, language_id in enumerate(language_ids, start=1):
            items.append(
                MutationItem(
                    "campaign_criterion_operation",
                    "create",
                    {
                        "campaign": campaign_resource,
                        "status": "ENABLED",
                        "negative": False,
                        "language": {
                            "language_constant": f"languageConstants/{language_id}"
                        },
                    },
                    correlation_id=f"language-{index}",
                )
            )
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="campaign_targeting",
            related_key=f"campaign:{campaign_id}",
            before=before,
            after={
                "campaignId": campaign_id,
                "geoTargetIds": list(geo_ids),
                "languageIds": list(language_ids),
                "positiveGeoTargetType": "PRESENCE",
                "negativeGeoTargetType": "PRESENCE",
                "currentCampaignDailyBudgetMicros": str(budget),
            },
            items=items,
            readback_queries=tuple(
                query for _, query in state_queries("campaign_targeting", customer_id, payload)
            ),
            spend_affecting=True,
        )

    def _build_campaign_status(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        campaign_id = _id(payload.get("campaignId"), field="campaignId")
        status = str(payload.get("status"))
        if status not in _STATUSES:
            raise ValidationError("status must be ENABLED or PAUSED")
        current_budget: int | None = None
        current_bid: int | None = None
        landing_urls: tuple[str, ...] = ()
        if status == "ENABLED":
            current_budget, current_bid, landing_urls = _require_search_enable_readiness(
                before, campaign_id
            )
        after: dict[str, Any] = {"campaignId": campaign_id, "status": status}
        if status == "ENABLED" and current_budget is not None:
            after["currentCampaignDailyBudgetMicros"] = str(current_budget)
        if current_bid is not None:
            after["bidMicros"] = str(current_bid)
        if landing_urls:
            after["landingPageUrls"] = list(landing_urls)
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="campaign_status",
            related_key=f"campaign:{campaign_id}",
            before=before,
            after=after,
            items=[
                MutationItem(
                    "campaign_operation",
                    "update",
                    {
                        "resource_name": _resource_name(customer_id, "campaigns", campaign_id),
                        "status": status,
                    },
                    ("status",),
                    "campaign",
                )
            ],
            readback_queries=tuple(
                query for _, query in state_queries("campaign_status", customer_id, payload)
            ),
            spend_affecting=status == "ENABLED",
        )

    def _build_campaign_budget(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        budget_id = _id(payload.get("budgetId"), field="budgetId")
        amount = _micros(payload.get("dailyBudgetMicros"), field="dailyBudgetMicros")
        assert amount is not None
        previous = _find_integer(before, "amount_micros") or 0
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="campaign_budget",
            related_key=f"campaign-budget:{budget_id}",
            before=before,
            after={"budgetId": budget_id, "dailyBudgetMicros": str(amount)},
            items=[
                MutationItem(
                    "campaign_budget_operation",
                    "update",
                    {
                        "resource_name": _resource_name(customer_id, "campaignBudgets", budget_id),
                        "amount_micros": amount,
                    },
                    ("amount_micros",),
                    "campaign-budget",
                )
            ],
            readback_queries=tuple(
                query for _, query in state_queries("campaign_budget", customer_id, payload)
            ),
            spend_delta=amount - previous,
            spend_affecting=True,
        )

    def _build_campaign_bidding(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        campaign_id = _id(payload.get("campaignId"), field="campaignId")
        campaign, current_budget = _campaign_state(before, campaign_id)
        if str(campaign.get("status")) != "PAUSED":
            raise ValidationError(
                "Campaign bidding can only be changed while the campaign is PAUSED"
            )
        strategy = str(payload.get("strategy"))
        resource: dict[str, Any] = {
            "resource_name": _resource_name(customer_id, "campaigns", campaign_id)
        }
        mask: tuple[str, ...]
        after: dict[str, Any] = {"campaignId": campaign_id, "strategy": strategy}
        if strategy == "MANUAL_CPC":
            if payload.get("enhancedCpcEnabled", False) is not False:
                raise ValidationError("Enhanced CPC is not supported by policy schema v1")
            resource["manual_cpc"] = {"enhanced_cpc_enabled": False}
            mask = ("manual_cpc",)
        elif strategy in {
            "MAXIMIZE_CONVERSIONS",
            "MAXIMIZE_CONVERSION_VALUE",
            "TARGET_ROAS",
        }:
            raise NotSupportedError(
                "This bidding strategy has no corresponding approved monetary limit "
                "in policy schema v1"
            )
        elif strategy == "TARGET_CPA":
            amount = _micros(payload.get("targetCpaMicros"), field="targetCpaMicros")
            assert amount is not None
            resource["target_cpa"] = {"target_cpa_micros": amount}
            after["targetCpaMicros"] = str(amount)
            mask = ("target_cpa",)
        else:
            raise ValidationError("strategy is not supported by the typed bidding preview")
        after["currentCampaignDailyBudgetMicros"] = str(current_budget)
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="campaign_bidding",
            related_key=f"campaign:{campaign_id}",
            before=before,
            after=after,
            items=[
                MutationItem("campaign_operation", "update", resource, mask, "campaign-bidding")
            ],
            readback_queries=tuple(
                query for _, query in state_queries("campaign_bidding", customer_id, payload)
            ),
            spend_affecting=True,
        )

    def _build_ad_group_create(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        campaign_id = _id(payload.get("campaignId"), field="campaignId")
        name = _name(payload.get("name"))
        _reject_existing_match(before, "A non-removed ad group with this name")
        bid = _micros(payload.get("cpcBidMicros"), field="cpcBidMicros", optional=True)
        resource: dict[str, Any] = {
            "campaign": _resource_name(customer_id, "campaigns", campaign_id),
            "name": name,
            "status": "PAUSED",
            "type_": "SEARCH_STANDARD",
        }
        after: dict[str, Any] = {"campaignId": campaign_id, "name": name, "status": "PAUSED"}
        if bid is not None:
            resource["cpc_bid_micros"] = bid
            after["bidMicros"] = str(bid)
            _set_parent_campaign_budget(after, before, "campaign", campaign_id)
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="ad_group_create",
            related_key=f"campaign:{campaign_id}:ad-group-name:{name.casefold()}",
            before=before,
            after=after,
            items=[
                MutationItem("ad_group_operation", "create", resource, correlation_id="ad-group")
            ],
            readback_queries=tuple(
                query for _, query in state_queries("ad_group_create", customer_id, payload)
            ),
            spend_affecting=bid is not None,
        )

    def _build_ad_group_update(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        ad_group_id = _id(payload.get("adGroupId"), field="adGroupId")
        resource: dict[str, Any] = {
            "resource_name": _resource_name(customer_id, "adGroups", ad_group_id)
        }
        after: dict[str, Any] = {"adGroupId": ad_group_id}
        mask: list[str] = []
        if payload.get("name") is not None:
            resource["name"] = _name(payload["name"])
            after["name"] = resource["name"]
            mask.append("name")
        if payload.get("status") is not None:
            status = str(payload["status"])
            if status not in _STATUSES:
                raise ValidationError("status must be ENABLED or PAUSED")
            resource["status"] = status
            after["status"] = status
            mask.append("status")
        if payload.get("cpcBidMicros") is not None:
            bid = _micros(payload["cpcBidMicros"], field="cpcBidMicros")
            resource["cpc_bid_micros"] = bid
            after["bidMicros"] = str(bid)
            mask.append("cpc_bid_micros")
        if not mask:
            raise ValidationError("ad_group_update_preview requires at least one changed field")
        spend_affecting = "cpc_bid_micros" in mask or resource.get("status") == "ENABLED"
        if spend_affecting:
            if resource.get("status") == "ENABLED":
                current_group = _ad_group_state(before, ad_group_id)
                _require_paused_parent(before, "ad_group", ad_group_id)
                if "bidMicros" not in after:
                    current_bid = _positive_integer(current_group.get("cpc_bid_micros"))
                    if current_bid is None:
                        raise ValidationError(
                            "Enabling an ad group requires an authoritative positive CPC bid"
                        )
                    after["bidMicros"] = str(current_bid)
            _set_parent_campaign_budget(after, before, "ad_group", ad_group_id)
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="ad_group_update",
            related_key=f"ad-group:{ad_group_id}",
            before=before,
            after=after,
            items=[MutationItem("ad_group_operation", "update", resource, tuple(mask), "ad-group")],
            readback_queries=tuple(
                query for _, query in state_queries("ad_group_update", customer_id, payload)
            ),
            spend_affecting=spend_affecting,
        )

    def _build_keyword_create(self, **values: Any) -> WritePlan:
        return self._keyword(negative=False, values=values)

    def _build_negative_keyword_create(self, **values: Any) -> WritePlan:
        return self._keyword(negative=True, values=values)

    def _keyword(self, *, negative: bool, values: dict[str, Any]) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        text = _name(payload.get("text"), field="text", maximum=80)
        _reject_existing_match(before, "A non-removed keyword with this text")
        match_type = str(payload.get("matchType"))
        if match_type not in _MATCH_TYPES:
            raise ValidationError("matchType is invalid")
        level = str(payload.get("level", "AD_GROUP"))
        kind: WriteKind = "negative_keyword_create" if negative else "keyword_create"
        if not negative and level != "AD_GROUP":
            raise ValidationError("Positive keywords can only be created at AD_GROUP level")
        if level == "AD_GROUP":
            parent_id = _id(payload.get("adGroupId"), field="adGroupId")
            operation_type = "ad_group_criterion_operation"
            parent_field = "ad_group"
            parent_name = _resource_name(customer_id, "adGroups", parent_id)
            related = f"ad-group:{parent_id}:keyword:{text.casefold()}"
        elif level == "CAMPAIGN" and negative:
            parent_id = _id(payload.get("campaignId"), field="campaignId")
            operation_type = "campaign_criterion_operation"
            parent_field = "campaign"
            parent_name = _resource_name(customer_id, "campaigns", parent_id)
            related = f"campaign:{parent_id}:keyword:{text.casefold()}"
        else:
            raise ValidationError("level must be AD_GROUP or CAMPAIGN")
        resource: dict[str, Any] = {
            parent_field: parent_name,
            "negative": negative,
            "keyword": {"text": text, "match_type": match_type},
        }
        if operation_type == "ad_group_criterion_operation":
            resource["status"] = "PAUSED" if not negative else "ENABLED"
        bid = _micros(payload.get("cpcBidMicros"), field="cpcBidMicros", optional=True)
        if bid is not None and not negative:
            resource["cpc_bid_micros"] = bid
        after = {
            "level": level,
            "parentId": parent_id,
            "text": text,
            "matchType": match_type,
            "negative": negative,
        }
        if bid is not None:
            after["bidMicros"] = str(bid)
            _set_parent_campaign_budget(after, before, "ad_group", parent_id)
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind=kind,
            related_key=related,
            before=before,
            after=after,
            items=[MutationItem(operation_type, "create", resource, correlation_id="criterion")],
            readback_queries=tuple(query for _, query in state_queries(kind, customer_id, payload)),
            spend_affecting=bid is not None,
        )

    def _build_criterion_update(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        level = str(payload.get("level"))
        criterion_id = _id(payload.get("criterionId"), field="criterionId")
        if level != "AD_GROUP":
            raise ValidationError("criterion_update_preview supports AD_GROUP keywords only")
        parent_id = _id(payload.get("adGroupId"), field="adGroupId")
        operation_type = "ad_group_criterion_operation"
        resource_name = _resource_name(
            customer_id, "adGroupCriteria", f"{parent_id}~{criterion_id}"
        )
        current_criterion = _positive_keyword_criterion(before, criterion_id)
        resource: dict[str, Any] = {"resource_name": resource_name}
        after: dict[str, Any] = {"level": level, "parentId": parent_id, "criterionId": criterion_id}
        mask: list[str] = []
        if payload.get("status") is not None:
            status = str(payload["status"])
            if status not in _STATUSES:
                raise ValidationError("status must be ENABLED or PAUSED")
            resource["status"] = status
            after["status"] = status
            mask.append("status")
        if payload.get("cpcBidMicros") is not None:
            bid = _micros(payload["cpcBidMicros"], field="cpcBidMicros")
            resource["cpc_bid_micros"] = bid
            after["bidMicros"] = str(bid)
            mask.append("cpc_bid_micros")
        if not mask:
            raise ValidationError("criterion_update_preview requires at least one changed field")
        spend_affecting = "cpc_bid_micros" in mask or resource.get("status") == "ENABLED"
        if spend_affecting:
            if resource.get("status") == "ENABLED":
                _require_paused_parent(before, "ad_group", parent_id)
                if "bidMicros" not in after:
                    current_bid = _positive_integer(
                        current_criterion.get("effective_cpc_bid_micros")
                    )
                    bid_source = str(current_criterion.get("effective_cpc_bid_source"))
                    if current_bid is None or bid_source not in {
                        "AD_GROUP",
                        "AD_GROUP_CRITERION",
                    }:
                        raise ValidationError(
                            "Enabling a keyword requires an authoritative positive "
                            "effective CPC bid"
                        )
                    after["bidMicros"] = str(current_bid)
            _set_parent_campaign_budget(
                after,
                before,
                "ad_group",
                parent_id,
            )
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="criterion_update",
            related_key=f"{level.casefold()}-criterion:{parent_id}:{criterion_id}",
            before=before,
            after=after,
            items=[MutationItem(operation_type, "update", resource, tuple(mask), "criterion")],
            readback_queries=tuple(
                query for _, query in state_queries("criterion_update", customer_id, payload)
            ),
            spend_affecting=spend_affecting,
        )

    def _build_asset_create(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        name = _name(payload.get("name"))
        _reject_existing_match(before, "An asset with this name")
        asset_type = str(payload.get("assetType"))
        resource: dict[str, Any] = {"name": name}
        if asset_type == "TEXT":
            resource["text_asset"] = {"text": _name(payload.get("text"), field="text", maximum=90)}
        elif asset_type == "YOUTUBE_VIDEO":
            video_id = _name(payload.get("youtubeVideoId"), field="youtubeVideoId", maximum=64)
            resource["youtube_video_asset"] = {"youtube_video_id": video_id}
        else:
            raise ValidationError("assetType must be TEXT or YOUTUBE_VIDEO")
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="asset_create",
            related_key=f"asset-name:{name.casefold()}",
            before=before,
            after={"name": name, "assetType": asset_type},
            items=[MutationItem("asset_operation", "create", resource, correlation_id="asset")],
            readback_queries=tuple(
                query for _, query in state_queries("asset_create", customer_id, payload)
            ),
        )

    def _build_asset_link(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        owner_type = str(payload.get("ownerType"))
        owner_id = _id(payload.get("ownerId"), field="ownerId")
        asset = _resource(
            payload.get("assetResourceName"),
            customer_id=customer_id,
            collection="assets",
            field="assetResourceName",
        )
        field_type = _name(payload.get("fieldType"), field="fieldType", maximum=64)
        _reject_existing_match(before, "A non-removed asset link for this owner and asset")
        if owner_type == "CAMPAIGN":
            operation_type = "campaign_asset_operation"
            resource = {
                "campaign": _resource_name(customer_id, "campaigns", owner_id),
                "asset": asset,
                "field_type": field_type,
                "status": "ENABLED",
            }
        elif owner_type == "AD_GROUP":
            operation_type = "ad_group_asset_operation"
            resource = {
                "ad_group": _resource_name(customer_id, "adGroups", owner_id),
                "asset": asset,
                "field_type": field_type,
                "status": "ENABLED",
            }
        elif owner_type == "ASSET_GROUP":
            operation_type = "asset_group_asset_operation"
            resource = {
                "asset_group": _resource_name(customer_id, "assetGroups", owner_id),
                "asset": asset,
                "field_type": field_type,
            }
        else:
            raise ValidationError("ownerType must be CAMPAIGN, AD_GROUP, or ASSET_GROUP")
        after = {
            "ownerType": owner_type,
            "ownerId": owner_id,
            "assetResourceName": asset,
            "fieldType": field_type,
        }
        _set_parent_campaign_budget(
            after,
            before,
            {
                "CAMPAIGN": "campaign",
                "AD_GROUP": "ad_group",
                "ASSET_GROUP": "asset_group",
            }[owner_type],
            owner_id,
        )
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="asset_link",
            related_key=f"{owner_type.casefold()}:{owner_id}:asset:{asset.rsplit('/', 1)[-1]}",
            before=before,
            after=after,
            items=[MutationItem(operation_type, "create", resource, correlation_id="asset-link")],
            readback_queries=tuple(
                query for _, query in state_queries("asset_link", customer_id, payload)
            ),
            spend_affecting=True,
        )

    def _build_asset_group_create(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        campaign_id = _id(payload.get("campaignId"), field="campaignId")
        name = _name(payload.get("name"))
        _reject_existing_match(before, "A non-removed asset group with this name")
        final_url = _url(payload.get("finalUrl"))
        assets = tuple(payload.get("assetResourceNames", ()))
        field_types = tuple(payload.get("fieldTypes", ()))
        if not assets or len(assets) != len(field_types) or len(assets) > 50:
            raise ValidationError(
                "assetResourceNames and fieldTypes must have 1 to 50 matching items"
            )
        group_name = _resource_name(customer_id, "assetGroups", -1)
        items = [
            MutationItem(
                "asset_group_operation",
                "create",
                {
                    "resource_name": group_name,
                    "campaign": _resource_name(customer_id, "campaigns", campaign_id),
                    "name": name,
                    "final_urls": [final_url],
                    "status": "ENABLED",
                },
                correlation_id="asset-group",
            )
        ]
        for index, (asset, field_type) in enumerate(zip(assets, field_types, strict=True), start=1):
            items.append(
                MutationItem(
                    "asset_group_asset_operation",
                    "create",
                    {
                        "asset_group": group_name,
                        "asset": _resource(
                            asset,
                            customer_id=customer_id,
                            collection="assets",
                            field="assetResourceNames",
                        ),
                        "field_type": _name(field_type, field="fieldTypes", maximum=64),
                    },
                    correlation_id=f"asset-link-{index}",
                )
            )
        after = {"campaignId": campaign_id, "name": name, "finalUrl": final_url}
        _set_parent_campaign_budget(after, before, "campaign", campaign_id)
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="asset_group_create",
            related_key=f"campaign:{campaign_id}:asset-group-name:{name.casefold()}",
            before=before,
            after=after,
            items=items,
            readback_queries=tuple(
                query for _, query in state_queries("asset_group_create", customer_id, payload)
            ),
            spend_affecting=True,
        )

    def _build_ad_create(self, **values: Any) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        ad_group_id = _id(payload.get("adGroupId"), field="adGroupId")
        name = _name(payload.get("name"))
        _reject_existing_match(before, "A non-removed ad with this name")
        ad_type = str(payload.get("adType"))
        ad: dict[str, Any] = {"name": name}
        if ad_type == "RESPONSIVE_SEARCH_AD":
            ad["final_urls"] = [_url(payload.get("finalUrl"))]
            ad["responsive_search_ad"] = {
                "headlines": [
                    {"text": text}
                    for text in _texts(
                        payload.get("headlines", ()),
                        field="headlines",
                        minimum=3,
                        maximum=15,
                        item_limit=30,
                    )
                ],
                "descriptions": [
                    {"text": text}
                    for text in _texts(
                        payload.get("descriptions", ()),
                        field="descriptions",
                        minimum=2,
                        maximum=4,
                        item_limit=90,
                    )
                ],
            }
        elif ad_type == "APP_AD":
            ad["app_ad"] = {
                "headlines": [
                    {"text": text}
                    for text in _texts(
                        payload.get("headlines", ()),
                        field="headlines",
                        minimum=2,
                        maximum=5,
                        item_limit=30,
                    )
                ],
                "descriptions": [
                    {"text": text}
                    for text in _texts(
                        payload.get("descriptions", ()),
                        field="descriptions",
                        minimum=1,
                        maximum=5,
                        item_limit=90,
                    )
                ],
                "images": [
                    {
                        "asset": _resource(
                            item,
                            customer_id=customer_id,
                            collection="assets",
                            field="imageAssets",
                        )
                    }
                    for item in payload.get("imageAssets", ())
                ],
                "youtube_videos": [
                    {
                        "asset": _resource(
                            item,
                            customer_id=customer_id,
                            collection="assets",
                            field="youtubeVideoAssets",
                        )
                    }
                    for item in payload.get("youtubeVideoAssets", ())
                ],
            }
        else:
            raise ValidationError("adType must be RESPONSIVE_SEARCH_AD or APP_AD")
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind="ad_create",
            related_key=f"ad-group:{ad_group_id}:ad-name:{name.casefold()}",
            before=before,
            after={"adGroupId": ad_group_id, "name": name, "adType": ad_type, "status": "PAUSED"},
            items=[
                MutationItem(
                    "ad_group_ad_operation",
                    "create",
                    {
                        "ad_group": _resource_name(customer_id, "adGroups", ad_group_id),
                        "status": "PAUSED",
                        "ad": ad,
                    },
                    correlation_id="ad",
                )
            ],
            readback_queries=tuple(
                query for _, query in state_queries("ad_create", customer_id, payload)
            ),
        )

    def _build_recommendation_apply(self, **values: Any) -> WritePlan:
        return self._recommendation(kind="recommendation_apply", values=values)

    def _build_recommendation_dismiss(self, **values: Any) -> WritePlan:
        return self._recommendation(kind="recommendation_dismiss", values=values)

    def _recommendation(self, *, kind: WriteKind, values: dict[str, Any]) -> WritePlan:
        profile, customer_id, policy_id, payload, before = _values(values)
        resource = _resource(
            payload.get("recommendationResourceName"),
            customer_id=customer_id,
            collection="recommendations",
            field="recommendationResourceName",
        )
        return self._plan(
            profile=profile,
            customer_id=customer_id,
            policy_id=policy_id,
            kind=kind,
            related_key=f"recommendation:{resource.rsplit('/', 1)[-1]}",
            before=before,
            after={
                "recommendationResourceName": resource,
                "action": kind.removeprefix("recommendation_"),
            },
            items=[
                MutationItem(
                    "recommendation_operation",
                    "update",
                    {"resource_name": resource},
                    ("resource_name",),
                    "recommendation",
                )
            ],
            readback_queries=tuple(query for _, query in state_queries(kind, customer_id, payload)),
        )


def _values(values: dict[str, Any]) -> tuple[str, str, str, dict[str, Any], dict[str, Any]]:
    return (
        str(values["profile"]),
        str(values["customer_id"]),
        str(values["policy_id"]),
        values["payload"],
        values["before"],
    )


def _reject_existing_match(before: dict[str, Any], description: str) -> None:
    objects = before.get("objects")
    if not isinstance(objects, list) or not objects:
        return
    first = objects[0]
    if not isinstance(first, dict):
        raise ValidationError("Current exact-match state is malformed")
    items = first.get("items")
    if not isinstance(items, list):
        raise ValidationError("Current exact-match state is malformed")
    if items:
        raise ValidationError(f"{description} already exists")


def _find_integer(value: Any, key: str) -> int | None:
    if isinstance(value, dict):
        if key in value:
            try:
                return int(str(value[key]))
            except ValueError:
                return None
        for nested in value.values():
            found = _find_integer(nested, key)
            if found is not None:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _find_integer(nested, key)
            if found is not None:
                return found
    return None


def _positive_integer(value: object) -> int | None:
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _query_items(
    before: dict[str, Any], index: int, *, evidence: str
) -> tuple[dict[str, Any], ...]:
    objects = before.get("objects")
    if not isinstance(objects, list) or index >= len(objects):
        raise ValidationError(f"{evidence} evidence is incomplete")
    current = objects[index]
    if not isinstance(current, dict) or not isinstance(current.get("items"), list):
        raise ValidationError(f"{evidence} evidence is malformed")
    items = current["items"]
    if any(not isinstance(item, dict) for item in items):
        raise ValidationError(f"{evidence} evidence is malformed")
    return tuple(items)


def _campaign_state(
    before: dict[str, Any], campaign_id: str, *, require_channel: bool = False
) -> tuple[dict[str, Any], int]:
    items = _query_items(before, 0, evidence="Campaign state")
    if len(items) != 1:
        raise ValidationError("Campaign state and daily budget must resolve exactly once")
    campaign = items[0].get("campaign")
    budget = items[0].get("campaign_budget")
    if not isinstance(campaign, dict) or not isinstance(budget, dict):
        raise ValidationError("Campaign state and daily budget must resolve exactly once")
    if str(campaign.get("id")) != campaign_id or campaign.get("status") not in _STATUSES:
        raise ValidationError("Campaign state and daily budget must resolve exactly once")
    campaign_budget = campaign.get("campaign_budget")
    budget_resource = budget.get("resource_name")
    if (
        not isinstance(campaign_budget, str)
        or not isinstance(budget_resource, str)
        or campaign_budget != budget_resource
    ):
        raise ValidationError("Campaign state and daily budget must resolve exactly once")
    amount = _positive_integer(budget.get("amount_micros"))
    if amount is None:
        raise ValidationError("Campaign state and daily budget must resolve exactly once")
    if require_channel and not isinstance(campaign.get("advertising_channel_type"), str):
        raise ValidationError("Campaign advertising channel evidence is incomplete")
    return campaign, amount


def _targeting_rows(before: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for row in _query_items(before, 1, evidence="Campaign targeting"):
        criterion = row.get("campaign_criterion")
        if not isinstance(criterion, dict):
            raise ValidationError("Campaign targeting evidence is malformed")
        criterion_id = criterion.get("criterion_id")
        resource_name = criterion.get("resource_name")
        criterion_type = criterion.get("type")
        status = criterion.get("status")
        negative = criterion.get("negative")
        if (
            criterion_id is None
            or not str(criterion_id).isascii()
            or not str(criterion_id).isdigit()
            or not isinstance(resource_name, str)
            or not resource_name
            or criterion_type not in {"LOCATION", "LANGUAGE"}
            or status not in _STATUSES
            or (negative is not None and not isinstance(negative, bool))
        ):
            raise ValidationError("Campaign targeting evidence is malformed")
        location = criterion.get("location")
        language = criterion.get("language")
        location_name = (
            location.get("geo_target_constant") if isinstance(location, dict) else None
        )
        language_name = (
            language.get("language_constant") if isinstance(language, dict) else None
        )
        if criterion_type == "LOCATION":
            if not isinstance(location_name, str) or not location_name or language_name is not None:
                raise ValidationError("Campaign targeting evidence is malformed")
        elif not isinstance(language_name, str) or not language_name or location_name is not None:
            raise ValidationError("Campaign targeting evidence is malformed")
        result.append(criterion)
    return tuple(result)


def _require_geo_constants(before: dict[str, Any], expected_ids: tuple[str, ...]) -> None:
    found: dict[str, dict[str, Any]] = {}
    for row in _walk_dicts(before):
        constant = row.get("geo_target_constant")
        if isinstance(constant, dict) and constant.get("id") is not None:
            found[str(constant["id"])] = constant
    if set(found) != set(expected_ids) or any(
        str(found[identifier].get("status")) != "ENABLED" for identifier in expected_ids
    ):
        raise ValidationError("Every geoTargetId must resolve to an enabled geo target constant")


def _require_language_constants(before: dict[str, Any], expected_ids: tuple[str, ...]) -> None:
    found: dict[str, dict[str, Any]] = {}
    for row in _walk_dicts(before):
        constant = row.get("language_constant")
        if isinstance(constant, dict) and constant.get("id") is not None:
            found[str(constant["id"])] = constant
    if set(found) != set(expected_ids) or any(
        found[identifier].get("targetable") is not True for identifier in expected_ids
    ):
        raise ValidationError("Every languageId must resolve to a targetable language constant")


def _ad_group_state(before: dict[str, Any], ad_group_id: str) -> dict[str, Any]:
    items = _query_items(before, 0, evidence="Ad group state")
    if len(items) != 1 or not isinstance(items[0].get("ad_group"), dict):
        raise ValidationError("Ad group state must resolve exactly once")
    group = items[0]["ad_group"]
    if str(group.get("id")) != ad_group_id or group.get("status") not in _STATUSES:
        raise ValidationError("Ad group state must resolve exactly once")
    raw_bid = group.get("cpc_bid_micros")
    if raw_bid is not None and _positive_integer(raw_bid) is None:
        raise ValidationError("Ad group CPC evidence is malformed")
    return group


def _require_paused_parent(before: dict[str, Any], parent_resource: str, parent_id: str) -> None:
    items = _query_items(before, 1, evidence="Parent campaign")
    if len(items) != 1:
        raise ValidationError("Parent campaign state must resolve exactly once")
    parent = items[0].get(parent_resource)
    campaign = items[0].get("campaign")
    if (
        not isinstance(parent, dict)
        or str(parent.get("id")) != parent_id
        or not isinstance(campaign, dict)
        or campaign.get("id") is None
        or campaign.get("status") != "PAUSED"
        or not isinstance(campaign.get("campaign_budget"), str)
    ):
        raise ValidationError("Child enablement requires an authoritative PAUSED parent campaign")


def _positive_keyword_criterion(before: dict[str, Any], criterion_id: str) -> dict[str, Any]:
    items = _query_items(before, 0, evidence="Keyword criterion")
    if len(items) != 1 or not isinstance(items[0].get("ad_group_criterion"), dict):
        raise ValidationError("Keyword criterion state must resolve exactly once")
    criterion = items[0]["ad_group_criterion"]
    keyword = criterion.get("keyword")
    negative = criterion.get("negative")
    if (
        str(criterion.get("criterion_id")) != criterion_id
        or criterion.get("type") != "KEYWORD"
        or (negative is not None and negative is not False)
        or criterion.get("status") not in _STATUSES
        or not isinstance(keyword, dict)
        or not isinstance(keyword.get("text"), str)
        or not keyword["text"].strip()
        or keyword.get("match_type") not in _MATCH_TYPES
    ):
        raise ValidationError("criterion_update_preview supports positive keywords only")
    return criterion


def _https_urls(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > 10:
        raise ValidationError("An eligible Search ad must expose bounded HTTPS final URLs")
    urls: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.startswith("https://"):
            raise ValidationError("An eligible Search ad must expose bounded HTTPS final URLs")
        urls.append(_url(item, field="finalUrl"))
    if len(set(urls)) != len(urls):
        raise ValidationError("An eligible Search ad must not contain duplicate final URLs")
    return tuple(urls)


def _require_search_enable_readiness(
    before: dict[str, Any], campaign_id: str
) -> tuple[int, int, tuple[str, ...]]:
    campaign, budget = _campaign_state(before, campaign_id, require_channel=True)
    if str(campaign.get("status")) != "PAUSED":
        raise ValidationError("Only a PAUSED campaign can be enabled")
    if str(campaign.get("advertising_channel_type")) != "SEARCH":
        raise NotSupportedError("v0.2 can enable only Search campaigns")
    if str(campaign.get("bidding_strategy_type")) != "MANUAL_CPC":
        raise NotSupportedError("v0.2 can enable only Search campaigns using MANUAL_CPC")
    manual_cpc = campaign.get("manual_cpc")
    if (
        not isinstance(manual_cpc, dict)
        or manual_cpc.get("enhanced_cpc_enabled") is not False
    ):
        raise ValidationError("Campaign enable requires Enhanced CPC to be disabled")
    geo_setting = campaign.get("geo_target_type_setting")
    if not isinstance(geo_setting, dict) or any(
        str(geo_setting.get(field)) != "PRESENCE"
        for field in ("positive_geo_target_type", "negative_geo_target_type")
    ):
        raise ValidationError("Campaign enable requires presence-only geo targeting")

    targeting = _targeting_rows(before)
    positive_locations = [
        criterion
        for criterion in targeting
        if isinstance(criterion.get("location"), dict)
        and criterion["location"].get("geo_target_constant") is not None
        and criterion.get("status") == "ENABLED"
        and criterion.get("negative") in {None, False}
    ]
    positive_languages = [
        criterion
        for criterion in targeting
        if isinstance(criterion.get("language"), dict)
        and criterion["language"].get("language_constant") is not None
        and criterion.get("status") == "ENABLED"
        and criterion.get("negative") in {None, False}
    ]
    if not positive_locations or not positive_languages:
        raise ValidationError("Campaign enable requires enabled location and language targeting")

    enabled_groups: set[str] = set()
    all_groups: set[str] = set()
    for row in _query_items(before, 2, evidence="Search ad group"):
        campaign_row = row.get("campaign")
        group = row.get("ad_group")
        if (
            not isinstance(campaign_row, dict)
            or str(campaign_row.get("id")) != campaign_id
            or not isinstance(group, dict)
            or group.get("id") is None
            or not str(group["id"]).isascii()
            or not str(group["id"]).isdigit()
            or group.get("status") not in _STATUSES
        ):
            raise ValidationError("Search ad group evidence is malformed")
        group_id = str(group["id"])
        if group_id in all_groups:
            raise ValidationError("Search ad group evidence contains duplicates")
        all_groups.add(group_id)
        if group.get("status") == "ENABLED":
            enabled_groups.add(group_id)
    if not enabled_groups:
        raise ValidationError("Campaign enable requires an enabled ad group")

    effective_bids: list[int] = []
    seen_criteria: set[str] = set()
    for row in _query_items(before, 3, evidence="Search keyword"):
        campaign_row = row.get("campaign")
        criterion = row.get("ad_group_criterion")
        group = row.get("ad_group")
        negative = criterion.get("negative") if isinstance(criterion, dict) else None
        if (
            not isinstance(campaign_row, dict)
            or str(campaign_row.get("id")) != campaign_id
            or not isinstance(criterion, dict)
            or criterion.get("criterion_id") is None
            or not str(criterion["criterion_id"]).isascii()
            or not str(criterion["criterion_id"]).isdigit()
            or criterion.get("status") not in _STATUSES
            or (negative is not None and not isinstance(negative, bool))
            or criterion.get("type") != "KEYWORD"
            or not isinstance(criterion.get("keyword"), dict)
            or not isinstance(criterion["keyword"].get("text"), str)
            or not criterion["keyword"]["text"].strip()
            or criterion["keyword"].get("match_type") not in _MATCH_TYPES
            or not isinstance(group, dict)
            or group.get("id") is None
            or str(group.get("id")) not in all_groups
            or group.get("status") not in _STATUSES
        ):
            raise ValidationError("Search keyword evidence is malformed")
        criterion_key = f"{group['id']}~{criterion['criterion_id']}"
        if criterion_key in seen_criteria:
            raise ValidationError("Search keyword evidence contains duplicates")
        seen_criteria.add(criterion_key)
        if (
            criterion.get("status") != "ENABLED"
            or (negative is not None and negative is not False)
            or str(group.get("id")) not in enabled_groups
        ):
            continue
        bid = _positive_integer(criterion.get("effective_cpc_bid_micros"))
        bid_source = str(criterion.get("effective_cpc_bid_source"))
        if bid is None or bid_source not in {"AD_GROUP", "AD_GROUP_CRITERION"}:
            raise ValidationError(
                "Campaign enable requires an authoritative positive effective CPC bid "
                "for every enabled keyword"
            )
        effective_bids.append(bid)
    if not effective_bids:
        raise ValidationError("Campaign enable requires an enabled positive keyword")

    landing_urls: set[str] = set()
    seen_ads: set[str] = set()
    for row in _query_items(before, 4, evidence="Search ad"):
        campaign_row = row.get("campaign")
        group = row.get("ad_group")
        ad_group_ad = row.get("ad_group_ad")
        if (
            not isinstance(campaign_row, dict)
            or str(campaign_row.get("id")) != campaign_id
            or not isinstance(group, dict)
            or group.get("id") is None
            or str(group.get("id")) not in all_groups
            or group.get("status") not in _STATUSES
            or not isinstance(ad_group_ad, dict)
            or ad_group_ad.get("status") not in _STATUSES
            or not isinstance(ad_group_ad.get("ad"), dict)
            or ad_group_ad["ad"].get("id") is None
            or not str(ad_group_ad["ad"]["id"]).isascii()
            or not str(ad_group_ad["ad"]["id"]).isdigit()
            or not isinstance(ad_group_ad["ad"].get("type"), str)
            or not isinstance(ad_group_ad.get("policy_summary"), dict)
            or not isinstance(
                ad_group_ad["policy_summary"].get("approval_status"), str
            )
            or not isinstance(ad_group_ad["policy_summary"].get("review_status"), str)
        ):
            raise ValidationError("Search ad evidence is malformed")
        ad_key = f"{group['id']}~{ad_group_ad['ad']['id']}"
        if ad_key in seen_ads:
            raise ValidationError("Search ad evidence contains duplicates")
        seen_ads.add(ad_key)
        if (
            ad_group_ad.get("status") == "ENABLED"
            and ad_group_ad["ad"].get("type") == "RESPONSIVE_SEARCH_AD"
            and ad_group_ad["policy_summary"].get("approval_status") == "APPROVED"
            and ad_group_ad["policy_summary"].get("review_status") == "REVIEWED"
            and str(group.get("id")) in enabled_groups
        ):
            landing_urls.update(_https_urls(ad_group_ad["ad"].get("final_urls")))
    if not landing_urls:
        raise ValidationError(
            "Campaign enable requires an approved responsive search ad with HTTPS final URLs"
        )
    return budget, max(effective_bids), tuple(sorted(landing_urls))


def _set_parent_campaign_budget(
    after: dict[str, Any], before: dict[str, Any], parent_resource: str, parent_id: str
) -> None:
    budget = _find_parent_campaign_budget(before, parent_resource, parent_id)
    if budget is not None:
        after["currentCampaignDailyBudgetMicros"] = str(budget)


def _find_parent_campaign_budget(
    value: Any, parent_resource: str, parent_id: str
) -> int | None:
    campaign_id: str | None = None
    campaign_budget_resource: str | None = None
    for nested in _walk_dicts(value):
        parent = nested.get(parent_resource)
        budget = nested.get("campaign_budget")
        if (
            isinstance(parent, dict)
            and str(parent.get("id")) == parent_id
            and isinstance(budget, dict)
        ):
            try:
                return int(str(budget["amount_micros"]))
            except (KeyError, ValueError):
                pass
        campaign = nested.get("campaign")
        if (
            isinstance(parent, dict)
            and str(parent.get("id")) == parent_id
            and isinstance(campaign, dict)
            and campaign.get("id") is not None
        ):
            raw_resource = campaign.get("campaign_budget")
            if raw_resource is None:
                continue
            campaign_id = str(campaign["id"])
            campaign_budget_resource = str(raw_resource)
            break
    if campaign_id is None:
        return None
    for nested in _walk_dicts(value):
        campaign = nested.get("campaign")
        budget = nested.get("campaign_budget")
        if (
            not isinstance(campaign, dict)
            or str(campaign.get("id")) != campaign_id
            or not isinstance(budget, dict)
        ):
            continue
        row_budget_resource = campaign.get("campaign_budget")
        budget_resource = budget.get("resource_name")
        if (
            str(row_budget_resource) != campaign_budget_resource
            or str(budget_resource) != campaign_budget_resource
        ):
            continue
        try:
            return int(str(budget["amount_micros"]))
        except (KeyError, ValueError):
            continue
    return None


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_dicts(nested)
