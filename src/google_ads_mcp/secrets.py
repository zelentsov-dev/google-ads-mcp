from __future__ import annotations

from typing import Any, Protocol

from google_ads_mcp.errors import SecurityError

_SERVICE = "google-ads-mcp"


class SecretStore(Protocol):
    def get_developer_token(self, profile: str) -> str | None: ...

    def set_developer_token(self, profile: str, token: str) -> None: ...


class SystemSecretStore:
    @staticmethod
    def _username(profile: str) -> str:
        return f"developer-token:{profile}"

    @staticmethod
    def _keyring() -> Any:
        try:
            import keyring
            from keyring.errors import KeyringError, NoKeyringError
        except ImportError as exc:
            raise SecurityError(
                "System secure storage support is unavailable",
                code="secure_storage_unavailable",
            ) from exc
        try:
            backend = keyring.get_keyring()
            if backend.priority <= 0:
                raise NoKeyringError("No recommended keyring backend")
        except (KeyringError, NoKeyringError) as exc:
            raise SecurityError(
                "System secure storage could not be verified",
                code="secure_storage_unavailable",
            ) from exc
        return keyring

    def get_developer_token(self, profile: str) -> str | None:
        keyring = self._keyring()
        try:
            value = keyring.get_password(_SERVICE, self._username(profile))
        except Exception as exc:
            raise SecurityError(
                "System secure storage could not read the developer token",
                code="secure_storage_unavailable",
            ) from exc
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise SecurityError("The stored developer token is invalid")
        return value

    def set_developer_token(self, profile: str, token: str) -> None:
        value = token.strip()
        if len(value) < 8 or len(value) > 256 or any(character.isspace() for character in value):
            raise SecurityError("The developer token format is invalid")
        keyring = self._keyring()
        try:
            keyring.set_password(_SERVICE, self._username(profile), value)
        except Exception as exc:
            raise SecurityError(
                "System secure storage could not save the developer token",
                code="secure_storage_unavailable",
            ) from exc
