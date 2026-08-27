from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from google_ads_mcp.errors import SecurityError

_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:developer_?token|refresh_?token|client_?secret|private_?key|"
    r"service_?account|access_?token|password|credential)(?:$|_)",
    re.IGNORECASE,
)
_ALLOWED_SECRET_REFERENCE_KEYS = {"developerTokenEnv"}
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+\-/]+=*")
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


def reject_embedded_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            if key_text not in _ALLOWED_SECRET_REFERENCE_KEYS and _SENSITIVE_KEY.search(key_text):
                raise SecurityError(f"Secret-bearing config key is forbidden at {path}.{key_text}")
            reject_embedded_secrets(nested, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            reject_embedded_secrets(nested, f"{path}[{index}]")


def _validate_file_stat(file_stat: os.stat_result, *, label: str) -> list[str]:
    if not stat.S_ISREG(file_stat.st_mode):
        raise SecurityError(f"{label} must be a regular file")
    if os.name == "nt":
        return [f"Unable to verify Windows ACL for {label}; confirm owner-only access"]
    if file_stat.st_uid != os.getuid():
        raise SecurityError(f"{label} must be owned by the current user")
    if stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise SecurityError(f"{label} permissions must be 0600 or stricter")
    return []


@contextmanager
def _owner_only_descriptor(path: Path, *, label: str) -> Generator[tuple[int, list[str]]]:
    if not path.is_absolute():
        raise SecurityError(f"{label} path must be absolute")
    try:
        path_stat = os.lstat(path)
    except FileNotFoundError as exc:
        raise SecurityError(f"{label} file does not exist") from exc
    except OSError as exc:
        raise SecurityError(f"{label} could not be inspected securely") from exc
    if stat.S_ISLNK(path_stat.st_mode):
        raise SecurityError(f"{label} must not be a symbolic link")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SecurityError(f"{label} could not be opened securely") from exc
    try:
        descriptor_stat = os.fstat(descriptor)
        if (path_stat.st_dev, path_stat.st_ino) != (
            descriptor_stat.st_dev,
            descriptor_stat.st_ino,
        ):
            raise SecurityError(f"{label} changed while it was being opened")
        yield descriptor, _validate_file_stat(descriptor_stat, label=label)
    finally:
        os.close(descriptor)


def check_owner_only_file(path: Path, *, label: str) -> list[str]:
    with _owner_only_descriptor(path, label=label) as (_, warnings):
        return warnings


def read_owner_only_text(
    path: Path, *, label: str, max_bytes: int = 1_048_576
) -> tuple[str, list[str]]:
    with _owner_only_descriptor(path, label=label) as (descriptor, warnings):
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise SecurityError(f"{label} exceeds the maximum allowed size")
    try:
        return b"".join(chunks).decode("utf-8"), warnings
    except UnicodeDecodeError as exc:
        raise SecurityError(f"{label} must be UTF-8 text") from exc


def sanitize_text(value: object, *, redact_email: bool = True) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")[:500]
    text = _BEARER.sub("[REDACTED]", text)
    text = _LONG_TOKEN.sub("[REDACTED]", text)
    if redact_email:
        text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    return text


def redact_untrusted(value: Any, *, redact_email: bool = True) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            if _SENSITIVE_KEY.search(str(key)) or (redact_email and "email" in str(key).lower()):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = redact_untrusted(nested, redact_email=redact_email)
        return result
    if isinstance(value, list):
        return [redact_untrusted(item, redact_email=redact_email) for item in value]
    if isinstance(value, str):
        return sanitize_text(value, redact_email=redact_email)
    return value


def stable_fingerprint(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
