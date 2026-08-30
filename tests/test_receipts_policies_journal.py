from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from google_ads_mcp import journal as journal_module
from google_ads_mcp import policies as policies_module
from google_ads_mcp import receipts as receipts_module
from google_ads_mcp.errors import ConfigError, SecurityError, ValidationError
from google_ads_mcp.journal import OperationJournal, resolve_state_dir
from google_ads_mcp.policies import (
    PolicyApproval,
    approve_policy,
    initialize_policies,
    load_policies,
    parse_policies,
    policy_document_fingerprint,
    resolve_policy_path,
    revoke_policy,
)
from google_ads_mcp.receipts import (
    ReceiptPayload,
    ReceiptSigner,
    canonical_json,
    load_or_create_installation_key,
    receipt_hash,
    value_fingerprint,
)
from google_ads_mcp.write_models import MutationItem, WritePlan

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def _policy_document(status: str = "pending") -> dict[str, Any]:
    document: dict[str, Any] = {
        "schemaVersion": 1,
        "policies": [
            {
                "policyId": "safe",
                "profile": "operator",
                "customerId": "1234567890",
                "currencyCode": "usd",
                "permissions": ["campaign_budget", "campaign_status"],
                "allowCampaignEnable": False,
                "campaignIds": [],
                "limits": {
                    "maxAggregateDailyBudgetMicros": "10000000",
                    "maxCampaignDailyBudgetMicros": "5000000",
                    "maxSpendChangeMicrosPerOperation": "1000000",
                    "maxBidMicros": "500000",
                },
                "approval": {
                    "status": status,
                    "approvedAt": None,
                    "expiresAt": None,
                    "policyFingerprint": None,
                },
            }
        ],
    }
    return document


def _write_policy(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document))
    path.chmod(0o600)


def _plan(related_key: str = "campaign:1") -> WritePlan:
    return WritePlan(
        profile="operator",
        customer_id="1234567890",
        policy_id="safe",
        kind="campaign_status",
        related_key=related_key,
        before={"campaign": {"status": "ENABLED"}},
        after={"campaignId": "1", "status": "PAUSED"},
        items=(
            MutationItem(
                "campaign_operation",
                "update",
                {"resource_name": "customers/1234567890/campaigns/1", "status": "PAUSED"},
                ("status",),
            ),
        ),
        readback_queries=("SELECT campaign.id FROM campaign LIMIT 1",),
        monetary_delta={"spendMicros": "0"},
        risk={"spendAffecting": False},
    )


def test_receipt_sign_verify_tamper_and_strict_payload() -> None:
    signer = ReceiptSigner(b"k" * 32)
    payload = ReceiptPayload(
        operation_id="operation",
        profile="operator",
        customer_id="1234567890",
        policy_id="safe",
        kind="campaign_status",
        current_fingerprint="f" * 64,
        plan_digest="a" * 64,
        validation_digest="b" * 64,
        policy_fingerprint="c" * 64,
        issued_at=1,
        expires_at=2,
        nonce="nonce",
    )
    receipt = signer.sign(payload)
    assert signer.verify(receipt) == payload
    assert len(receipt_hash(receipt)) == 64
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert value_fingerprint({"a": 1}) == value_fingerprint({"a": 1})
    with pytest.raises(ValidationError, match="signature"):
        signer.verify(receipt[:-1] + ("A" if receipt[-1] != "A" else "B"))
    with pytest.raises(ValidationError, match="malformed"):
        signer.verify("not-a-receipt")
    with pytest.raises(SecurityError):
        ReceiptSigner(b"short")
    malformed = ReceiptPayload.from_dict
    with pytest.raises(ValidationError, match="scope"):
        malformed({"operationId": "x"})
    invalid = payload.as_dict()
    invalid["issuedAt"] = "invalid"
    with pytest.raises(ValidationError, match="values"):
        malformed(invalid)


def _signed_raw_receipt(value: bytes, key: bytes = b"k" * 32) -> str:
    encoded = base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
    signature = hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest()
    rendered_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"v1.{encoded}.{rendered_signature}"


def test_receipt_rejects_invalid_base64_json_and_root_type() -> None:
    signer = ReceiptSigner(b"k" * 32)
    with pytest.raises(ValidationError, match="malformed"):
        signer.verify("v1.e30.☃")
    with pytest.raises(ValidationError, match="malformed"):
        signer.verify("v1.e30.a")
    with pytest.raises(ValidationError, match="malformed"):
        signer.verify(_signed_raw_receipt(b"{"))
    with pytest.raises(ValidationError, match="malformed"):
        signer.verify(_signed_raw_receipt(b"[]"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_installation_key_is_owner_only_and_stable(tmp_path: Path) -> None:
    path = tmp_path / "state" / "installation.key"
    first = load_or_create_installation_key(path)
    second = load_or_create_installation_key(path)
    assert first == second
    assert len(first) == 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_bytes(b"bad")
    path.chmod(0o600)
    with pytest.raises(SecurityError, match="invalid"):
        load_or_create_installation_key(path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_installation_key_permission_and_type_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    insecure_parent = tmp_path / "insecure"
    insecure_parent.mkdir(mode=0o700)
    insecure_parent.chmod(0o755)
    with pytest.raises(SecurityError, match="state directory"):
        load_or_create_installation_key(insecure_parent / "key")

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    directory_key = state / "directory-key"
    directory_key.mkdir()
    with pytest.raises(SecurityError, match="regular"):
        load_or_create_installation_key(directory_key)

    insecure_key = state / "insecure-key"
    insecure_key.write_bytes(b"k" * 32)
    insecure_key.chmod(0o644)
    with pytest.raises(SecurityError, match="owner-only"):
        load_or_create_installation_key(insecure_key)

    linked_target = tmp_path / "linked-target"
    linked_target.mkdir(mode=0o700)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(linked_target, target_is_directory=True)
    with pytest.raises(SecurityError, match="symbolic"):
        load_or_create_installation_key(linked_parent / "key")

    key_target = tmp_path / "key-target"
    key_target.write_bytes(b"k" * 32)
    key_target.chmod(0o600)
    linked_key = state / "linked-key"
    linked_key.symlink_to(key_target)
    with pytest.raises(SecurityError, match="opened securely"):
        load_or_create_installation_key(linked_key)

    monkeypatch.setattr(receipts_module.os, "name", "nt")
    with pytest.raises(SecurityError, match="Windows ACL"):
        load_or_create_installation_key(state / "windows-key")


@pytest.mark.skipif(os.name == "nt", reason="POSIX secure-open contract")
def test_installation_key_secure_open_failure_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(receipts_module.os, "O_NOFOLLOW", 0)
        with pytest.raises(SecurityError, match="opening is unavailable"):
            load_or_create_installation_key(tmp_path / "nofollow" / "key")

    state = tmp_path / "denied"
    state.mkdir(mode=0o700)
    with monkeypatch.context() as patch:
        patch.setattr(
            receipts_module.os,
            "open",
            lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(SecurityError, match="created securely"):
            load_or_create_installation_key(state / "key")


@pytest.mark.skipif(
    os.name == "nt", reason="policy writes fail closed without Windows ACL verification"
)
def test_policy_parse_approval_revocation_and_limits(tmp_path: Path) -> None:
    path = tmp_path / "policies.json"
    _write_policy(path, _policy_document())
    policy_file = load_policies(str(path))
    policy = policy_file.policy("safe")
    assert policy.currency_code == "USD"
    assert policy.fingerprint
    approved = approve_policy(policy_file, "safe", now=NOW)
    assert approved.approval.status == "approved"
    assert approved.approval.expires_at == NOW + timedelta(days=30)
    reloaded = load_policies(str(path))
    reloaded.policy("safe").require_active(
        profile="operator",
        customer_id="1234567890",
        currency_code="USD",
        kind="campaign_budget",
        now=NOW,
    )
    reloaded.policy("safe").enforce_money(
        {"spendMicros": "100"}, {"dailyBudgetMicros": "200", "bidMicros": "100"}
    )
    revoked = revoke_policy(reloaded, "safe")
    assert revoked.approval.status == "revoked"
    with pytest.raises(ValidationError, match="approved"):
        revoked.require_active(
            profile="operator",
            customer_id="1234567890",
            currency_code="USD",
            kind="campaign_budget",
            now=NOW,
        )
    assert policy_document_fingerprint(load_policies(str(path)))


def test_policy_missing_and_pending_timestamp_serialization(tmp_path: Path) -> None:
    policy_file = parse_policies(_policy_document(), path=tmp_path / "p.json")
    with pytest.raises(ValidationError, match="does not exist"):
        policy_file.policy("missing")
    pending = PolicyApproval(
        status="pending", approved_at=None, expires_at=None, policy_fingerprint=None
    )
    assert pending.as_dict()["approvedAt"] is None


def test_policy_accepts_campaign_targeting_permission(tmp_path: Path) -> None:
    document = _policy_document()
    document["policies"][0]["permissions"].append("campaign_targeting")

    policy = parse_policies(document, path=tmp_path / "p.json").policy("safe")

    assert "campaign_targeting" in policy.permissions


def test_campaign_enable_policy_requires_fingerprint_bound_campaign_allowlist(
    tmp_path: Path,
) -> None:
    document = _policy_document()
    item = document["policies"][0]
    item["allowCampaignEnable"] = True
    with pytest.raises(ConfigError, match="campaignIds"):
        parse_policies(document, path=tmp_path / "missing.json")

    item["campaignIds"] = ["24192332798"]
    policy = parse_policies(document, path=tmp_path / "valid.json").policy("safe")

    assert policy.campaign_ids == frozenset({"24192332798"})
    assert policy.unsigned_dict()["campaignIds"] == ["24192332798"]


@pytest.mark.parametrize(
    "campaign_ids",
    [["bad"], [1], ["1", "1"], ["1" * 21], [str(index) for index in range(101)]],
)
def test_policy_campaign_allowlist_rejects_invalid_ids(
    campaign_ids: list[object], tmp_path: Path
) -> None:
    document = _policy_document()
    document["policies"][0]["campaignIds"] = campaign_ids

    with pytest.raises(ConfigError, match="campaignIds"):
        parse_policies(document, path=tmp_path / "invalid.json")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(schemaVersion=2), "schemaVersion"),
        (lambda value: value.update(extra=True), "Unknown"),
        (lambda value: value.update(policies="bad"), "array"),
        (lambda value: value["policies"][0].update(policyId=""), "policyId"),
        (lambda value: value["policies"][0].update(currencyCode="US"), "currencyCode"),
        (lambda value: value["policies"][0].update(permissions=[]), "permissions"),
        (
            lambda value: value["policies"][0].update(permissions=["billing"]),
            "permission",
        ),
        (lambda value: value["policies"][0].update(limits="bad"), "limits"),
        (
            lambda value: value["policies"][0]["limits"].update(maxBidMicros="bad"),
            "maxBidMicros",
        ),
        (
            lambda value: value["policies"][0]["limits"].update(
                maxCampaignDailyBudgetMicros="20000000"
            ),
            "Campaign budget",
        ),
        (lambda value: value["policies"][0].update(approval="bad"), "approval"),
        (
            lambda value: value["policies"][0]["approval"].update(status="bad"),
            "status",
        ),
    ],
)
def test_invalid_policy_documents_fail_closed(mutation: Any, message: str, tmp_path: Path) -> None:
    document = _policy_document()
    mutation(document)
    with pytest.raises(ConfigError, match=message):
        parse_policies(document, path=tmp_path / "policies.json")


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "root"),
        ({"schemaVersion": 1, "policies": ["bad"]}, "object"),
    ],
)
def test_invalid_policy_root_and_item(document: object, message: str, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_policies(document, path=tmp_path / "policies.json")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("profile", "", "profile"),
        ("maxBidMicros", "0", "greater than zero"),
        ("approvedAt", 1, "RFC3339"),
        ("approvedAt", "invalid", "RFC3339"),
        ("approvedAt", "2026-08-28T12:00:00", "time zone"),
    ],
)
def test_additional_policy_guards(
    field: str, value: object, message: str, tmp_path: Path
) -> None:
    document = _policy_document()
    if field == "profile":
        document["policies"][0][field] = value
    elif field == "maxBidMicros":
        document["policies"][0]["limits"][field] = value
    else:
        document["policies"][0]["approval"][field] = value
    with pytest.raises(ConfigError, match=message):
        parse_policies(document, path=tmp_path / "policies.json")


def test_approved_policy_structure_and_fingerprint_guards(tmp_path: Path) -> None:
    incomplete = _policy_document(status="approved")
    with pytest.raises(ConfigError, match="valid approval"):
        parse_policies(incomplete, path=tmp_path / "incomplete.json")

    mismatched = _policy_document(status="approved")
    approval = mismatched["policies"][0]["approval"]
    approval.update(
        approvedAt="2026-08-28T12:00:00Z",
        expiresAt="2026-08-29T12:00:00Z",
        policyFingerprint="0" * 64,
    )
    with pytest.raises(ConfigError, match="fingerprint"):
        parse_policies(mismatched, path=tmp_path / "mismatched.json")


def test_policy_invalid_json_and_windows_write_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "policies.json"
    path.write_text("{")
    path.chmod(0o600)
    with pytest.raises(ConfigError, match="valid JSON"):
        load_policies(str(path))
    windows_path = tmp_path / "windows.json"
    monkeypatch.setattr(policies_module.os, "name", "nt")
    with pytest.raises(SecurityError, match="Windows ACL"):
        policies_module._write_owner_only(
            windows_path,
            {"schemaVersion": 1, "policies": []},
            replace_existing=False,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_policy_directory_security_guards(tmp_path: Path) -> None:
    insecure = tmp_path / "insecure-policy"
    insecure.mkdir(mode=0o700)
    insecure.chmod(0o755)
    with pytest.raises(SecurityError, match="owner-only"):
        initialize_policies(str(insecure / "policies.json"))

    target = tmp_path / "policy-target"
    target.mkdir(mode=0o700)
    linked = tmp_path / "policy-link"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(SecurityError, match="symbolic"):
        initialize_policies(str(linked / "policies.json"))


def test_policy_scope_permissions_expiry_and_money_fail_closed(tmp_path: Path) -> None:
    policy = parse_policies(_policy_document(), path=tmp_path / "p.json").policy("safe")
    approval = PolicyApproval(
        status="approved",
        approved_at=NOW,
        expires_at=NOW + timedelta(days=1),
        policy_fingerprint=policy.fingerprint,
    )
    policy = replace(policy, approval=approval)
    for arguments in [
        {"profile": "other", "customer_id": "1234567890", "currency_code": "USD"},
        {"profile": "operator", "customer_id": "0000000000", "currency_code": "USD"},
        {"profile": "operator", "customer_id": "1234567890", "currency_code": "EUR"},
    ]:
        with pytest.raises(ValidationError):
            policy.require_active(
                **arguments,
                kind="campaign_budget",
                now=NOW,
            )
    with pytest.raises(ValidationError, match="permit"):
        policy.require_active(
            profile="operator",
            customer_id="1234567890",
            currency_code="USD",
            kind="asset_create",
            now=NOW,
        )
    with pytest.raises(ValidationError, match="approved"):
        policy.require_active(
            profile="operator",
            customer_id="1234567890",
            currency_code="USD",
            kind="campaign_budget",
            now=NOW + timedelta(days=2),
        )
    for delta, after in [
        ({"spendMicros": "1000001"}, {}),
        ({"spendMicros": "0"}, {"dailyBudgetMicros": "5000001"}),
        ({"spendMicros": "0"}, {"targetCpaMicros": "500001"}),
    ]:
        with pytest.raises(ValidationError, match="limit"):
            policy.enforce_money(delta, after)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_policy_init_paths_and_duplicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "policies.json"
    assert initialize_policies(str(path)) == path
    assert load_policies(str(path)).policies == ()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ConfigError, match="exists"):
        initialize_policies(str(path))
    duplicate = _policy_document()
    duplicate["policies"].append(dict(duplicate["policies"][0]))
    with pytest.raises(ConfigError, match="unique"):
        parse_policies(duplicate, path=path)
    monkeypatch.setenv("GOOGLE_ADS_MCP_POLICIES", str(path))
    assert resolve_policy_path() == path
    relative = resolve_policy_path("relative.json")
    assert relative.is_absolute()


@pytest.mark.skipif(os.name == "nt", reason="POSIX SQLite contract")
def test_journal_state_machine_replay_expiry_and_unresolved(tmp_path: Path) -> None:
    journal = OperationJournal(tmp_path / "state")
    plan = _plan()
    fingerprint = value_fingerprint(plan.before)
    preview = journal.create_preview(
        operation_id="operation-1",
        receipt_hash_value="h1",
        plan=plan,
        current_fingerprint=fingerprint,
        plan_digest=value_fingerprint(plan.as_dict()),
        validation_digest=value_fingerprint({"valid": True}),
        policy_fingerprint="p" * 64,
        validation={"valid": True},
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    assert preview.state == "previewed"
    assert stat.S_IMODE(journal.path.stat().st_mode) == 0o600
    assert journal.by_receipt_hash("h1").operation_id == "operation-1"
    applying = journal.begin_apply("operation-1", "h1", NOW + timedelta(minutes=1))
    assert applying.state == "applying"
    with pytest.raises(ValidationError, match="used"):
        journal.begin_apply("operation-1", "h1", NOW + timedelta(minutes=2))
    unresolved = journal.transition(
        "operation-1",
        "committed_unverified",
        verification={"state": "inconclusive"},
        result={},
        now=NOW + timedelta(minutes=2),
    )
    assert unresolved.state == "committed_unverified"
    with pytest.raises(ValidationError, match="requires"):
        journal.create_preview(
            operation_id="operation-2",
            receipt_hash_value="h2",
            plan=plan,
            current_fingerprint=fingerprint,
            plan_digest=value_fingerprint(plan.as_dict()),
            validation_digest=value_fingerprint({"valid": True}),
            policy_fingerprint="p" * 64,
            validation={"valid": True},
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
        )
    resolved = journal.transition(
        "operation-1",
        "applied",
        verification={"state": "matched_after"},
        result={"state": "applied"},
        now=NOW + timedelta(minutes=3),
    )
    assert resolved.state == "applied"
    assert journal.list(limit=1)[0].operation_id == "operation-1"
    assert journal.list(limit=5, profile="operator")
    with pytest.raises(ValidationError, match="between"):
        journal.list(limit=0)
    with pytest.raises(ValidationError, match="does not exist"):
        journal.inspect("missing")
    with pytest.raises(ValidationError, match="unknown"):
        journal.by_receipt_hash("missing")


@pytest.mark.skipif(os.name == "nt", reason="POSIX SQLite contract")
def test_journal_expiry_invalidation_and_transition_guards(tmp_path: Path) -> None:
    journal = OperationJournal(tmp_path / "state")
    plan = _plan("campaign:2")
    fingerprint = value_fingerprint(plan.before)
    journal.create_preview(
        operation_id="expired",
        receipt_hash_value="expired-hash",
        plan=plan,
        current_fingerprint=fingerprint,
        plan_digest=value_fingerprint(plan.as_dict()),
        validation_digest=value_fingerprint({"valid": True}),
        policy_fingerprint="p" * 64,
        validation={"valid": True},
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValidationError, match="expired"):
        journal.begin_apply("expired", "expired-hash", NOW + timedelta(seconds=2))
    assert journal.inspect("expired").state == "expired"
    other = _plan("campaign:3")
    journal.create_preview(
        operation_id="drift",
        receipt_hash_value="drift-hash",
        plan=other,
        current_fingerprint=value_fingerprint(other.before),
        plan_digest=value_fingerprint(other.as_dict()),
        validation_digest=value_fingerprint({"valid": True}),
        policy_fingerprint="p" * 64,
        validation={"valid": True},
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    invalidated = journal.invalidate("drift", "drift", NOW)
    assert invalidated.state == "expired"
    with pytest.raises(ValidationError, match="transition"):
        journal.transition(
            "drift",
            "applied",
            verification={},
            result={},
            now=NOW,
        )
    with pytest.raises(ValidationError, match="unknown"):
        journal.begin_apply("missing", "wrong", NOW)


def test_state_path_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_ADS_MCP_STATE_DIR", str(tmp_path))
    assert resolve_state_dir() == tmp_path
    assert resolve_state_dir("relative-state").is_absolute()


def test_journal_windows_write_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "windows-state"
    monkeypatch.setattr(journal_module.os, "name", "nt")
    with pytest.raises(SecurityError, match="Windows ACL"):
        OperationJournal(state)


@pytest.mark.skipif(os.name == "nt", reason="POSIX SQLite contract")
def test_journal_path_security_guards(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first = OperationJournal(state)
    assert OperationJournal(state).path == first.path

    target = tmp_path / "target-state"
    target.mkdir(mode=0o700)
    linked_state = tmp_path / "linked-state"
    linked_state.symlink_to(target, target_is_directory=True)
    with pytest.raises(SecurityError, match="symbolic"):
        OperationJournal(linked_state)

    journal_target = tmp_path / "journal-target"
    journal_target.write_bytes(b"not sqlite")
    journal_target.chmod(0o600)
    journal_link_state = tmp_path / "journal-link-state"
    journal_link_state.mkdir(mode=0o700)
    (journal_link_state / "operations.sqlite3").symlink_to(journal_target)
    with pytest.raises(SecurityError, match="opened securely"):
        OperationJournal(journal_link_state)

    replacement_state = tmp_path / "replacement-state"
    journal = OperationJournal(replacement_state)
    original = replacement_state / "operations-original.sqlite3"
    journal.path.replace(original)
    journal.path.write_bytes(b"replacement")
    journal.path.chmod(0o600)
    with pytest.raises(SecurityError, match="changed after initialization"):
        journal.list(profile=None, limit=10)


@pytest.mark.skipif(os.name == "nt", reason="POSIX SQLite contract")
def test_journal_additional_integrity_and_path_guards(tmp_path: Path) -> None:
    with pytest.raises(SecurityError, match="absolute"):
        OperationJournal(Path("relative-state"))
    with pytest.raises(SecurityError, match="structured data"):
        journal_module._decode("[]")

    insecure_state = tmp_path / "insecure-state"
    insecure_state.mkdir(mode=0o700)
    insecure_state.chmod(0o755)
    with pytest.raises(SecurityError, match="owner-only"):
        OperationJournal(insecure_state)

    insecure_file_state = tmp_path / "insecure-file-state"
    insecure_file_state.mkdir(mode=0o700)
    insecure_file = insecure_file_state / "operations.sqlite3"
    insecure_file.write_bytes(b"")
    insecure_file.chmod(0o644)
    with pytest.raises(SecurityError, match="regular file"):
        OperationJournal(insecure_file_state)


@pytest.mark.skipif(os.name == "nt", reason="POSIX secure-open contract")
def test_journal_secure_open_identity_failure_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "operations.sqlite3"
    path.write_bytes(b"")
    path.chmod(0o600)

    with monkeypatch.context() as patch:
        patch.setattr(journal_module.os, "name", "nt")
        with pytest.raises(SecurityError, match="Windows ACL"):
            journal_module._open_journal(path, create=False)

    with monkeypatch.context() as patch:
        patch.setattr(journal_module.os, "O_NOFOLLOW", 0)
        with pytest.raises(SecurityError, match="opening is unavailable"):
            journal_module._open_journal(path, create=False)

    real_stat = os.stat
    with monkeypatch.context() as patch:
        patch.setattr(
            journal_module.os,
            "stat",
            lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("gone")),
        )
        with pytest.raises(SecurityError, match="identity could not be verified"):
            journal_module._open_journal(path, create=False)

    with monkeypatch.context() as patch:
        def mismatched_stat(*args: Any, **kwargs: Any) -> os.stat_result:
            values = list(real_stat(*args, **kwargs))
            values[1] = int(values[1]) + 1
            return os.stat_result(values)

        patch.setattr(journal_module.os, "stat", mismatched_stat)
        with pytest.raises(SecurityError, match="changed while it was opened"):
            journal_module._open_journal(path, create=False)

    journal = OperationJournal(tmp_path / "state")
    calls = 0

    def disappearing_stat(*args: Any, **kwargs: Any) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise FileNotFoundError("gone")
        return real_stat(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(journal_module.os, "stat", disappearing_stat)
        with pytest.raises(SecurityError, match="identity could not be verified"):
            journal.list(profile=None, limit=10)


@pytest.mark.skipif(os.name == "nt", reason="POSIX SQLite contract")
def test_journal_migrates_integrity_columns_and_blocks_cross_key_apply(tmp_path: Path) -> None:
    state = tmp_path / "state"
    journal = OperationJournal(state)
    with sqlite3.connect(journal.path) as connection:
        connection.execute("ALTER TABLE operations DROP COLUMN plan_digest")
        connection.execute("ALTER TABLE operations DROP COLUMN validation_digest")
        connection.execute("ALTER TABLE operations DROP COLUMN policy_fingerprint")
    migrated = OperationJournal(state)
    with sqlite3.connect(migrated.path) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(operations)")
        }
    assert {"plan_digest", "validation_digest", "policy_fingerprint"} <= columns

    first = _plan("campaign:1")
    second = _plan("campaign:2")
    for operation_id, receipt, plan in (
        ("first", "first-hash", first),
        ("second", "second-hash", second),
    ):
        migrated.create_preview(
            operation_id=operation_id,
            receipt_hash_value=receipt,
            plan=plan,
            current_fingerprint=value_fingerprint(plan.before),
            plan_digest=value_fingerprint(plan.as_dict()),
            validation_digest=value_fingerprint({"valid": True}),
            policy_fingerprint="p" * 64,
            validation={"valid": True},
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
        )
    migrated.begin_apply("first", "first-hash", NOW)
    with pytest.raises(ValidationError, match="unresolved customer write"):
        migrated.begin_apply("second", "second-hash", NOW)
