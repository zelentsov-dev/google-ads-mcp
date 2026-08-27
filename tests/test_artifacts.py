from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


def test_plugin_archive_is_allowlisted_and_executable(tmp_path: Path) -> None:
    archive = tmp_path / "plugin.zip"
    subprocess.run(
        [sys.executable, "scripts/package_plugin.py", str(archive)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [sys.executable, "scripts/scan_artifacts.py", str(archive)],
        check=True,
        capture_output=True,
        text=True,
    )
    with zipfile.ZipFile(archive) as contents:
        names = contents.namelist()
        assert "google-ads-mcp/.codex-plugin/plugin.json" in names
        assert "google-ads-mcp/bin/google-ads-mcp-launcher" in names
        assert not any(".env" in name or "__pycache__" in name for name in names)
        launcher = contents.getinfo("google-ads-mcp/bin/google-ads-mcp-launcher")
        assert (launcher.external_attr >> 16) & 0o111
        launcher_text = contents.read(launcher).decode()
        assert "uv run --frozen --no-dev" in launcher_text
        assert "serve --stdio" in launcher_text


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("google-ads.yaml", b"safe-looking-placeholder"),
        ("nested/.env.production", b"SAFE=placeholder"),
        ("data.txt", b"-----BEGIN PRIVATE KEY-----\nnot-a-real-key"),
        ("data.json", b'{"refresh_token":"synthetic-value-long-enough"}'),
        (
            "data.json",
            b'{"api_' + b'key":"synthetic_' + b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef" + b'"}',
        ),
        ("data.txt", b"Authorization: Bearer SyntheticToken01234567890123456789"),
        ("data.txt", b"AI" + b"za0123456789abcdefghijklmnopqrstuvwxy"),
        ("data.txt", b"GOC" + b"SPX-" + b"A1b2C3d4E5f6G7h8I9j0K1l2M3n4"),
        (
            "data.json",
            b'{"value":"SyntheticBare_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef"}',
        ),
        (
            "data.txt",
            b"eyJhbGciOiJIUzI1NiJ9"
            + b".eyJzdWIiOiJzeW50aGV0aWMifQ"
            + b".abcdefghijklmnopqrstuv",
        ),
    ],
)
def test_artifact_scan_rejects_credentials(tmp_path: Path, name: str, content: bytes) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(name, content)
    completed = subprocess.run(
        [sys.executable, "scripts/scan_artifacts.py", str(archive)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0


def test_manifest_validator() -> None:
    subprocess.run(
        [sys.executable, "scripts/validate_manifests.py"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_release_version_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REF_NAME", "v0.1.0")
    valid = subprocess.run(
        [sys.executable, "scripts/verify_release_version.py"],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert valid.returncode == 0
    environment = os.environ.copy()
    environment["GITHUB_REF_NAME"] = "v0.1.1"
    invalid = subprocess.run(
        [sys.executable, "scripts/verify_release_version.py"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode != 0
