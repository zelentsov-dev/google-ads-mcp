from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google_ads_mcp.models import Status


@dataclass(slots=True)
class PublicError(Exception):
    code: str
    message: str
    status: Status = "unavailable"
    request_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "error": {"code": self.code, "message": self.message},
        }
        if self.request_id:
            result["error"]["requestId"] = self.request_id
        return result


class ConfigError(PublicError):
    def __init__(self, message: str, code: str = "invalid_config") -> None:
        super().__init__(code=code, message=message)


class SecurityError(PublicError):
    def __init__(self, message: str, code: str = "security_policy_violation") -> None:
        super().__init__(code=code, message=message)


class ValidationError(PublicError):
    def __init__(self, message: str, code: str = "invalid_request") -> None:
        super().__init__(code=code, message=message)


class BlockedError(PublicError):
    def __init__(self, message: str, code: str = "write_blocked") -> None:
        super().__init__(code=code, message=message, status="blocked")


class NotSupportedError(PublicError):
    def __init__(self, message: str, code: str = "not_supported") -> None:
        super().__init__(code=code, message=message, status="not_supported")


class CursorError(PublicError):
    def __init__(self, message: str, code: str = "invalid_cursor") -> None:
        super().__init__(code=code, message=message)


class AdapterError(PublicError):
    transient: bool

    def __init__(
        self,
        message: str,
        *,
        code: str = "google_ads_unavailable",
        status: Status = "unavailable",
        request_id: str | None = None,
        transient: bool = False,
    ) -> None:
        super().__init__(code=code, message=message, status=status, request_id=request_id)
        self.transient = transient
