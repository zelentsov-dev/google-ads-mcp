from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google_ads_mcp.errors import SecurityError, ValidationError


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def value_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def receipt_hash(receipt: str) -> str:
    return hashlib.sha256(receipt.encode("ascii")).hexdigest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    alphanumeric = value.replace("-", "").replace("_", "")
    if not value or not value.isascii() or not alphanumeric.isalnum():
        raise ValidationError("The operation receipt is malformed", code="invalid_receipt")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ValidationError("The operation receipt is malformed", code="invalid_receipt") from exc


@dataclass(frozen=True, slots=True)
class ReceiptPayload:
    operation_id: str
    profile: str
    customer_id: str
    policy_id: str
    kind: str
    current_fingerprint: str
    plan_digest: str
    validation_digest: str
    policy_fingerprint: str
    issued_at: int
    expires_at: int
    nonce: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "operationId": self.operation_id,
            "profile": self.profile,
            "customerId": self.customer_id,
            "policyId": self.policy_id,
            "kind": self.kind,
            "currentFingerprint": self.current_fingerprint,
            "planDigest": self.plan_digest,
            "validationDigest": self.validation_digest,
            "policyFingerprint": self.policy_fingerprint,
            "issuedAt": self.issued_at,
            "expiresAt": self.expires_at,
            "nonce": self.nonce,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ReceiptPayload:
        required = {
            "operationId",
            "profile",
            "customerId",
            "policyId",
            "kind",
            "currentFingerprint",
            "planDigest",
            "validationDigest",
            "policyFingerprint",
            "issuedAt",
            "expiresAt",
            "nonce",
        }
        if set(value) != required:
            raise ValidationError(
                "The operation receipt has an invalid scope", code="invalid_receipt"
            )
        try:
            return cls(
                operation_id=str(value["operationId"]),
                profile=str(value["profile"]),
                customer_id=str(value["customerId"]),
                policy_id=str(value["policyId"]),
                kind=str(value["kind"]),
                current_fingerprint=str(value["currentFingerprint"]),
                plan_digest=str(value["planDigest"]),
                validation_digest=str(value["validationDigest"]),
                policy_fingerprint=str(value["policyFingerprint"]),
                issued_at=int(value["issuedAt"]),
                expires_at=int(value["expiresAt"]),
                nonce=str(value["nonce"]),
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "The operation receipt has invalid values", code="invalid_receipt"
            ) from exc


class ReceiptSigner:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise SecurityError("The installation signing key is invalid")
        self._key = key

    def sign(self, payload: ReceiptPayload) -> str:
        encoded = _encode(canonical_json(payload.as_dict()).encode("utf-8"))
        signature = hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest()
        return f"v1.{encoded}.{_encode(signature)}"

    def verify(self, receipt: str) -> ReceiptPayload:
        parts = receipt.split(".")
        if len(parts) != 3 or parts[0] != "v1":
            raise ValidationError("The operation receipt is malformed", code="invalid_receipt")
        expected = hmac.new(self._key, parts[1].encode("ascii"), hashlib.sha256).digest()
        provided = _decode(parts[2])
        if not hmac.compare_digest(expected, provided):
            raise ValidationError(
                "The operation receipt signature is invalid", code="invalid_receipt"
            )
        try:
            decoded = json.loads(_decode(parts[1]))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValidationError(
                "The operation receipt is malformed", code="invalid_receipt"
            ) from exc
        if not isinstance(decoded, dict):
            raise ValidationError("The operation receipt is malformed", code="invalid_receipt")
        return ReceiptPayload.from_dict(decoded)


def load_or_create_installation_key(path: Path) -> bytes:
    if os.name == "nt":
        raise SecurityError(
            "Windows ACL verification is unavailable; write operations are disabled",
            code="writes_not_secure",
        )
    if path.parent.is_symlink():
        raise SecurityError("The operation state directory must not be a symbolic link")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_stat = path.parent.stat()
    if parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) & 0o077:
        raise SecurityError("The operation state directory must be owner-only")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise SecurityError("Secure installation-key opening is unavailable")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | nofollow
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        try:
            descriptor = os.open(
                path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow
            )
        except OSError as exc:
            raise SecurityError("The installation key could not be opened securely") from exc
    except OSError as exc:
        raise SecurityError("The installation key could not be created securely") from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SecurityError("The installation key must be a regular file")
        if file_stat.st_uid != os.getuid() or stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise SecurityError("The installation key must be owner-only")
        data = os.read(descriptor, 33)
        if not data:
            data = os.urandom(32)
            os.write(descriptor, data)
            os.fsync(descriptor)
        if len(data) != 32:
            raise SecurityError("The installation key is invalid")
        return data
    finally:
        os.close(descriptor)
