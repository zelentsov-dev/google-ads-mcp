from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from google_ads_mcp import config as config_module
from google_ads_mcp.config import (
    ADCAuth,
    GoogleAdsYamlAuth,
    initialize_config,
    load_config,
    normalize_customer_id,
    parse_config,
    resolve_config_path,
)
from google_ads_mcp.errors import ConfigError, SecurityError


def _profile(**overrides: object) -> dict[str, object]:
    profile: dict[str, object] = {
        "name": "production-read-only",
        "loginCustomerId": "123-456-7890",
        "defaultCustomerId": "0987654321",
        "auth": {"type": "adc", "developerTokenEnv": "GOOGLE_ADS_DEVELOPER_TOKEN"},
        "allowWrites": False,
    }
    profile.update(overrides)
    return profile


def test_parse_adc_config() -> None:
    config = parse_config({"profiles": [_profile()]}, path=Path("/config.json"))
    profile = config.profile("production-read-only")
    assert profile.login_customer_id == "1234567890"
    assert isinstance(profile.auth, ADCAuth)
    assert profile.allow_writes is False


def test_profile_lookup_walks_multiple_profiles() -> None:
    second = _profile(name="second", defaultCustomerId=None, loginCustomerId=None)
    config = parse_config({"profiles": [_profile(), second]}, path=Path("/config.json"))
    assert config.profile("second").name == "second"
    assert normalize_customer_id(None, field="optional") is None


def test_parse_yaml_config_requires_secure_absolute_file(tmp_path: Path) -> None:
    yaml = tmp_path / "google-ads.yaml"
    yaml.write_text("synthetic: true\n")
    yaml.chmod(0o600)
    config = parse_config(
        {"profiles": [_profile(auth={"type": "googleAdsYaml", "path": str(yaml)})]},
        path=tmp_path / "accounts.json",
    )
    assert isinstance(config.profiles[0].auth, GoogleAdsYamlAuth)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"profiles": "bad"},
        {"profiles": ["bad"]},
        {"profiles": [_profile(name="bad space")]},
        {"profiles": [_profile(allowWrites=True)]},
        {"profiles": [_profile(extra="no")]},
        {"profiles": [_profile(auth={"type": "unknown"})]},
        {"profiles": [_profile(auth="not-an-object")]},
        {"profiles": [_profile(auth={"type": "adc"})]},
        {"profiles": [_profile(auth={"type": "adc", "developerTokenEnv": "BAD-NAME"})]},
        {
            "profiles": [
                _profile(auth={"type": "adc", "developerTokenEnv": "rawTokenValue123456789"})
            ]
        },
        {"profiles": [_profile(auth={"type": "adc", "developerToken": "raw-secret"})]},
        {"profiles": [_profile(auth={"type": "adc", "refreshToken": "raw-secret"})]},
        {"profiles": [_profile(auth={"type": "googleAdsYaml", "path": ""})]},
    ],
)
def test_invalid_configs_are_rejected(data: object) -> None:
    with pytest.raises((ConfigError, SecurityError)):
        parse_config(data, path=Path("/config.json"))


def test_duplicate_profiles_rejected() -> None:
    with pytest.raises(ConfigError, match="Duplicate"):
        parse_config({"profiles": [_profile(), _profile()]}, path=Path("/config.json"))


def test_relative_or_unsafe_yaml_rejected(tmp_path: Path) -> None:
    with pytest.raises(SecurityError, match="absolute"):
        parse_config(
            {"profiles": [_profile(auth={"type": "googleAdsYaml", "path": "google-ads.yaml"})]},
            path=tmp_path / "accounts.json",
        )
    yaml = tmp_path / "google-ads.yaml"
    yaml.write_text("synthetic: true\n")
    yaml.chmod(0o644)
    document = {"profiles": [_profile(auth={"type": "googleAdsYaml", "path": str(yaml)})]}
    if os.name == "nt":
        assert parse_config(document, path=tmp_path / "accounts.json").warnings
    else:
        with pytest.raises(SecurityError, match="0600"):
            parse_config(document, path=tmp_path / "accounts.json")


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink enforcement")
def test_symlink_yaml_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("synthetic: true\n")
    target.chmod(0o600)
    link = tmp_path / "link.yaml"
    link.symlink_to(target)
    with pytest.raises(SecurityError, match="symbolic"):
        parse_config(
            {"profiles": [_profile(auth={"type": "googleAdsYaml", "path": str(link)})]},
            path=tmp_path / "accounts.json",
        )


def test_init_and_load_owner_only_config(tmp_path: Path) -> None:
    path = tmp_path / "config" / "accounts.json"
    created = initialize_config(str(path))
    assert created == path
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    config = load_config(str(path))
    assert config.profiles[0].name == "production-read-only"
    with pytest.raises(ConfigError, match="already exists"):
        initialize_config(str(path))


def test_init_cleans_temporary_file_on_atomic_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "accounts.json"

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("synthetic atomic failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic"):
        initialize_config(str(target))
    assert list(tmp_path.iterdir()) == []


def test_init_windows_mode_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "accounts.json"
    monkeypatch.setattr(config_module, "_is_windows", lambda: True)
    assert initialize_config(str(target)) == target


def test_load_rejects_invalid_json_permissions_and_encoding(tmp_path: Path) -> None:
    path = tmp_path / "accounts.json"
    path.write_text("{")
    path.chmod(0o600)
    with pytest.raises(ConfigError, match="line"):
        load_config(str(path))
    path.write_bytes(b"\xff")
    with pytest.raises(SecurityError, match="UTF-8"):
        load_config(str(path))
    path.write_text(json.dumps({"profiles": []}))
    path.chmod(0o644)
    if os.name == "nt":
        assert load_config(str(path)).warnings
    else:
        with pytest.raises(SecurityError, match="0600"):
            load_config(str(path))


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink enforcement")
def test_load_rejects_config_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text('{"profiles": []}')
    target.chmod(0o600)
    link = tmp_path / "accounts.json"
    link.symlink_to(target)
    with pytest.raises(SecurityError, match="symbolic"):
        load_config(str(link))


def test_config_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / "env.json"
    monkeypatch.setenv("GOOGLE_ADS_MCP_CONFIG", str(env_path))
    assert resolve_config_path() == env_path
    cli_path = tmp_path / "cli.json"
    assert resolve_config_path(str(cli_path)) == cli_path
    monkeypatch.delenv("GOOGLE_ADS_MCP_CONFIG")
    assert str(resolve_config_path()).endswith("/.config/google-ads-mcp/accounts.json")


@given(st.integers(min_value=0, max_value=9_999_999_999))
def test_customer_id_round_trip(value: int) -> None:
    rendered = f"{value:010d}"
    assert normalize_customer_id(rendered, field="id", required=True) == rendered


@pytest.mark.parametrize(
    "value", [None, "", "123", "abcdefghij", "１２３４５６７８９０", "12345678901"]
)
def test_invalid_customer_ids(value: object) -> None:
    with pytest.raises(ConfigError):
        normalize_customer_id(value, field="id", required=True)


def test_missing_profile() -> None:
    config = parse_config({"profiles": []}, path=Path("/config.json"))
    with pytest.raises(ConfigError, match="requested profile"):
        config.profile("missing")
