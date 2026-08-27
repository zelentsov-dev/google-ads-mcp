from __future__ import annotations

import os
from pathlib import Path

import pytest

from google_ads_mcp.errors import SecurityError
from google_ads_mcp.security import (
    check_owner_only_file,
    read_owner_only_text,
    redact_untrusted,
    reject_embedded_secrets,
    sanitize_text,
    stable_fingerprint,
)


def test_reject_embedded_secrets_recursively() -> None:
    reject_embedded_secrets({"auth": {"developerTokenEnv": "TOKEN_NAME"}})
    with pytest.raises(SecurityError, match="refreshToken"):
        reject_embedded_secrets({"profiles": [{"refreshToken": "secret"}]})


def test_owner_only_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "secure.json"
    path.write_text("{}")
    path.chmod(0o600)
    warnings = check_owner_only_file(path, label="test")
    assert (bool(warnings), os.name) in {(False, "posix"), (True, "nt")}
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(SecurityError, match="regular"):
        check_owner_only_file(directory, label="test")
    with pytest.raises(SecurityError, match="exist"):
        check_owner_only_file(tmp_path / "missing", label="test")
    with pytest.raises(SecurityError, match="absolute"):
        check_owner_only_file(Path("relative"), label="test")


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor race regression")
def test_secure_read_uses_the_validated_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "secure.json"
    path.write_text("trusted")
    path.chmod(0o600)
    replacement = tmp_path / "replacement.json"
    replacement.write_text("replaced")
    replacement.chmod(0o600)
    original_read = os.read
    swapped = False

    def swap_then_read(descriptor: int, size: int) -> bytes:
        nonlocal swapped
        if not swapped:
            os.replace(replacement, path)
            swapped = True
        return original_read(descriptor, size)

    monkeypatch.setattr(os, "read", swap_then_read)
    text, _ = read_owner_only_text(path, label="test")
    assert text == "trusted"
    assert path.read_text() == "replaced"


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor flag regression")
def test_secure_open_fallback_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "secure.json"
    path.write_text("{}")
    path.chmod(0o600)
    monkeypatch.delattr(os, "O_CLOEXEC")
    monkeypatch.delattr(os, "O_NOFOLLOW")
    assert check_owner_only_file(path, label="test") == []
    monkeypatch.setattr(os, "open", lambda *_: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(SecurityError, match="opened securely"):
        check_owner_only_file(path, label="test")


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor identity regression")
def test_secure_open_rejects_path_swap_and_inspection_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "secure.json"
    path.write_text("trusted")
    path.chmod(0o600)
    replacement = tmp_path / "replacement.json"
    replacement.write_text("replaced")
    replacement.chmod(0o600)
    original_open = os.open

    def swap_then_open(target: Path, flags: int) -> int:
        os.replace(replacement, path)
        return original_open(target, flags)

    monkeypatch.setattr(os, "open", swap_then_open)
    with pytest.raises(SecurityError, match="changed"):
        check_owner_only_file(path, label="test")
    monkeypatch.undo()
    monkeypatch.setattr(os, "lstat", lambda *_: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(SecurityError, match="inspected securely"):
        check_owner_only_file(path, label="test")


def test_secure_read_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "secure.json"
    path.write_text("too large")
    path.chmod(0o600)
    with pytest.raises(SecurityError, match="maximum"):
        read_owner_only_text(path, label="test", max_bytes=2)


def test_sanitize_and_recursive_redaction() -> None:
    raw = "Bearer abcdefghijklmnopqrstuvwxyz0123456789 person@example.com\nnext"
    sanitized = sanitize_text(raw)
    assert "Bearer" not in sanitized
    assert "person@example.com" not in sanitized
    assert "\n" not in sanitized
    value = redact_untrusted(
        {"user_email": "person@example.com", "nested": ["person@example.com"], "refresh_token": "x"}
    )
    assert value["user_email"] == "[REDACTED]"
    assert value["refresh_token"] == "[REDACTED]"
    assert value["nested"] == ["[REDACTED_EMAIL]"]
    assert sanitize_text("person@example.com", redact_email=False) == "person@example.com"
    assert redact_untrusted(42) == 42


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner enforcement")
def test_owner_and_windows_acl_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "secure.json"
    path.write_text("{}")
    path.chmod(0o600)
    monkeypatch.setattr(os, "name", "nt")
    assert "Windows ACL" in check_owner_only_file(path, label="test")[0]
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(SecurityError, match="owned"):
        check_owner_only_file(path, label="test")


def test_stable_fingerprint_is_bound_and_deterministic() -> None:
    assert stable_fingerprint("a", "b") == stable_fingerprint("a", "b")
    assert stable_fingerprint("a", "b") != stable_fingerprint("ab")


@pytest.mark.skipif(os.name != "nt", reason="Windows-only warning path")
def test_windows_acl_warning(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{}")
    assert check_owner_only_file(path, label="test")
