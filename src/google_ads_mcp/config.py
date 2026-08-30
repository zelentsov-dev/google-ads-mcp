from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from google_ads_mcp.constants import DEFAULT_CONFIG_ENV, DEFAULT_CONFIG_PATH
from google_ads_mcp.errors import ConfigError, SecurityError
from google_ads_mcp.security import (
    check_owner_only_file,
    read_owner_only_text,
    reject_embedded_secrets,
)

_PROFILE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


def _is_windows() -> bool:
    return os.name == "nt"


@dataclass(frozen=True, slots=True)
class ADCAuth:
    type: Literal["adc"]
    developer_token_env: str | None
    developer_token_keyring: bool = False


@dataclass(frozen=True, slots=True)
class GoogleAdsYamlAuth:
    type: Literal["googleAdsYaml"]
    path: Path
    developer_token_keyring: bool = False


Auth = ADCAuth | GoogleAdsYamlAuth


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    login_customer_id: str | None
    default_customer_id: str | None
    auth: Auth
    allow_writes: bool


@dataclass(frozen=True, slots=True)
class AccountsConfig:
    path: Path
    profiles: tuple[Profile, ...]
    warnings: tuple[str, ...] = ()

    def profile(self, name: str) -> Profile:
        for profile in self.profiles:
            if profile.name == name:
                return profile
        raise ConfigError("The requested profile is not configured", code="profile_not_found")


def resolve_config_path(cli_path: str | None = None) -> Path:
    raw = cli_path or os.environ.get(DEFAULT_CONFIG_ENV) or DEFAULT_CONFIG_PATH
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path).absolute()


def normalize_customer_id(value: object, *, field: str, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise ConfigError(f"{field} is required")
        return None
    normalized = str(value).replace("-", "")
    if not normalized.isascii() or not normalized.isdigit() or len(normalized) != 10:
        raise ConfigError(f"{field} must contain exactly 10 digits")
    return normalized


def _strict_keys(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"Unknown config key at {path}: {unknown[0]}")


def _parse_auth(raw: object, profile_name: str) -> tuple[Auth, list[str]]:
    if not isinstance(raw, dict):
        raise ConfigError(f"Profile {profile_name} auth must be an object")
    auth = cast(dict[str, Any], raw)
    auth_type = auth.get("type")
    if auth_type == "adc":
        _strict_keys(
            auth,
            {"type", "developerTokenEnv", "developerTokenKeyring"},
            f"profiles.{profile_name}.auth",
        )
        env_name = auth.get("developerTokenEnv")
        keyring = auth.get("developerTokenKeyring") is True
        if env_name is not None and (
            not isinstance(env_name, str) or not _ENV_NAME.fullmatch(env_name)
        ):
            raise ConfigError(f"Profile {profile_name} developerTokenEnv is invalid")
        if keyring == (env_name is not None):
            raise ConfigError(
                f"Profile {profile_name} must configure exactly one developer token source"
            )
        return ADCAuth(
            type="adc",
            developer_token_env=env_name,
            developer_token_keyring=keyring,
        ), []
    if auth_type == "googleAdsYaml":
        _strict_keys(
            auth,
            {"type", "path", "developerTokenKeyring"},
            f"profiles.{profile_name}.auth",
        )
        raw_path = auth.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ConfigError(f"Profile {profile_name} YAML path is required")
        path = Path(raw_path)
        if not path.is_absolute():
            raise SecurityError(f"Profile {profile_name} YAML path must be absolute")
        warnings = check_owner_only_file(path, label=f"Profile {profile_name} Google Ads YAML")
        return GoogleAdsYamlAuth(
            type="googleAdsYaml",
            path=path,
            developer_token_keyring=auth.get("developerTokenKeyring") is True,
        ), warnings
    raise ConfigError(f"Profile {profile_name} auth.type must be adc or googleAdsYaml")


def parse_config(
    data: object, *, path: Path, file_warnings: list[str] | None = None
) -> AccountsConfig:
    if not isinstance(data, dict):
        raise ConfigError("Configuration root must be an object")
    root = cast(dict[str, Any], data)
    reject_embedded_secrets(root)
    _strict_keys(root, {"profiles"}, "$")
    raw_profiles = root.get("profiles")
    if not isinstance(raw_profiles, list):
        raise ConfigError("profiles must be an array")
    profiles: list[Profile] = []
    warnings = list(file_warnings or [])
    seen: set[str] = set()
    for index, raw_profile in enumerate(raw_profiles):
        if not isinstance(raw_profile, dict):
            raise ConfigError(f"profiles[{index}] must be an object")
        item = cast(dict[str, Any], raw_profile)
        _strict_keys(
            item,
            {"name", "loginCustomerId", "defaultCustomerId", "auth", "allowWrites"},
            f"profiles[{index}]",
        )
        name = item.get("name")
        if not isinstance(name, str) or not _PROFILE_NAME.fullmatch(name):
            raise ConfigError(f"profiles[{index}].name is invalid")
        if name in seen:
            raise ConfigError(f"Duplicate profile name: {name}")
        seen.add(name)
        if not isinstance(item.get("allowWrites"), bool):
            raise ConfigError(f"Profile {name} allowWrites must be a boolean")
        auth, auth_warnings = _parse_auth(item.get("auth"), name)
        warnings.extend(auth_warnings)
        profiles.append(
            Profile(
                name=name,
                login_customer_id=normalize_customer_id(
                    item.get("loginCustomerId"), field=f"Profile {name} loginCustomerId"
                ),
                default_customer_id=normalize_customer_id(
                    item.get("defaultCustomerId"), field=f"Profile {name} defaultCustomerId"
                ),
                auth=auth,
                allow_writes=bool(item["allowWrites"]),
            )
        )
    return AccountsConfig(path=path, profiles=tuple(profiles), warnings=tuple(warnings))


def load_config(cli_path: str | None = None) -> AccountsConfig:
    path = resolve_config_path(cli_path)
    try:
        text, warnings = read_owner_only_text(path, label="Google Ads MCP config")
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Configuration is not valid JSON at line {exc.lineno}") from exc
    return parse_config(raw, path=path, file_warnings=warnings)


def initialize_config(cli_path: str | None = None) -> Path:
    path = resolve_config_path(cli_path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ConfigError("Configuration already exists", code="config_exists")
    document = {
        "profiles": [
            {
                "name": "production-read-only",
                "loginCustomerId": "1234567890",
                "defaultCustomerId": "0987654321",
                "auth": {
                    "type": "adc",
                    "developerTokenKeyring": True,
                },
                "allowWrites": False,
            }
        ]
    }
    payload = json.dumps(document, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".accounts.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if not _is_windows():
            fchmod = getattr(os, "fchmod", None)
            if fchmod is None:
                raise SecurityError("Unable to secure the temporary configuration file")
            fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if not _is_windows():
            path.chmod(0o600)
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return path
