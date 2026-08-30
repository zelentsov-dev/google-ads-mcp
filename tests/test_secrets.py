from __future__ import annotations

import builtins
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from google_ads_mcp.errors import SecurityError
from google_ads_mcp.secrets import SystemSecretStore


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.values[(service, username)] = value


def test_system_secret_store_never_returns_values_on_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeKeyring()
    monkeypatch.setattr(SystemSecretStore, "_keyring", staticmethod(lambda: backend))
    store = SystemSecretStore()
    assert store.get_developer_token("operator") is None
    assert store.set_developer_token("operator", "synthetic-token") is None
    assert store.get_developer_token("operator") == "synthetic-token"


@pytest.mark.parametrize("value", ["short", "contains space", "x" * 257])
def test_secret_format_fails_closed(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeKeyring()
    monkeypatch.setattr(SystemSecretStore, "_keyring", staticmethod(lambda: backend))
    with pytest.raises(SecurityError, match="format"):
        SystemSecretStore().set_developer_token("operator", value)


def test_secret_backend_failures_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def get_password(self, *_: Any) -> str | None:
            raise RuntimeError("private backend details")

        def set_password(self, *_: Any) -> None:
            raise RuntimeError("private backend details")

    monkeypatch.setattr(SystemSecretStore, "_keyring", staticmethod(lambda: Broken()))
    store = SystemSecretStore()
    with pytest.raises(SecurityError, match="could not read") as read:
        store.get_developer_token("operator")
    with pytest.raises(SecurityError, match="could not save") as write:
        store.set_developer_token("operator", "synthetic-token")
    assert "private backend" not in read.value.message
    assert "private backend" not in write.value.message


def test_invalid_stored_secret_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Invalid:
        @staticmethod
        def get_password(*_: Any) -> str:
            return ""

    monkeypatch.setattr(SystemSecretStore, "_keyring", staticmethod(lambda: Invalid()))
    with pytest.raises(SecurityError, match="invalid"):
        SystemSecretStore().get_developer_token("operator")


def test_keyring_backend_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = ModuleType("keyring.errors")

    class KeyringError(Exception):
        pass

    class NoKeyringError(KeyringError):
        pass

    errors.KeyringError = KeyringError
    errors.NoKeyringError = NoKeyringError
    keyring = ModuleType("keyring")
    keyring.get_keyring = lambda: SimpleNamespace(priority=1)
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setitem(sys.modules, "keyring.errors", errors)
    assert SystemSecretStore._keyring() is keyring
    keyring.get_keyring = lambda: SimpleNamespace(priority=0)
    with pytest.raises(SecurityError, match="verified"):
        SystemSecretStore._keyring()

    def fail_backend() -> object:
        raise KeyringError("private")

    keyring.get_keyring = fail_backend
    with pytest.raises(SecurityError, match="verified"):
        SystemSecretStore._keyring()


def test_missing_keyring_dependency_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    original = builtins.__import__

    def missing(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring":
            raise ImportError("private")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(SecurityError, match="unavailable") as error:
        SystemSecretStore._keyring()
    assert "private" not in error.value.message
