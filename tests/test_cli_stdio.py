from __future__ import annotations

import json
import os
import queue
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, TextIO

import pytest

from google_ads_mcp.cli import main

_SAFE_ENVIRONMENT_KEYS = (
    "HOME",
    "PATH",
    "PYTHONPATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
)


def _stdio_environment(config: Path) -> dict[str, str]:
    environment = {key: os.environ[key] for key in _SAFE_ENVIRONMENT_KEYS if key in os.environ}
    environment["GOOGLE_ADS_MCP_CONFIG"] = str(config)
    return environment


def _read_stdout(stream: TextIO, output: queue.Queue[str | None], lines: list[str]) -> None:
    try:
        for line in stream:
            lines.append(line)
            output.put(line)
    finally:
        output.put(None)


def _run_stdio_until(
    config: Path, messages: list[object], expected_ids: set[int]
) -> tuple[list[dict[str, Any]], str, str]:
    process = subprocess.Popen(
        [sys.executable, "-m", "google_ads_mcp", "serve", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_stdio_environment(config),
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    output: queue.Queue[str | None] = queue.Queue()
    raw_lines: list[str] = []
    reader = threading.Thread(
        target=_read_stdout,
        args=(process.stdout, output, raw_lines),
        daemon=True,
    )
    reader.start()
    responses: list[dict[str, Any]] = []
    try:
        payload = "\n".join(
            message if isinstance(message, str) else json.dumps(message) for message in messages
        )
        process.stdin.write(payload + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 10
        while not expected_ids <= {item.get("id") for item in responses}:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                line = output.get(timeout=remaining)
            except queue.Empty:
                break
            if line is None:
                break
            responses.append(json.loads(line))
    finally:
        process.stdin.close()
        process.stdin = None
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        reader.join(timeout=5)
    stderr = process.stderr.read()
    if not expected_ids <= {item.get("id") for item in responses}:
        pytest.fail("MCP process ended before all expected response IDs were received")
    return [json.loads(line) for line in raw_lines], "".join(raw_lines), stderr


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    main(["version"])
    assert "0.1.0" in capsys.readouterr().out


def test_cli_config_init_validate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "accounts.json"
    main(["--config", str(config), "config", "init"])
    if os.name != "nt":
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
    main(["--config", str(config), "config", "validate"])
    output = capsys.readouterr().out
    assert '"valid": true' in output


def test_cli_failure_is_stderr_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--config", str(tmp_path / "missing.json"), "config", "validate"])
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "security_policy_violation" in captured.err


def test_black_box_stdio_initialize_and_tools_list(tmp_path: Path) -> None:
    config = tmp_path / "accounts.json"
    config.write_text('{"profiles": []}\n')
    config.chmod(0o600)
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "black-box-test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    lines, stdout, stderr = _run_stdio_until(config, messages, {1, 2})
    assert {line.get("id") for line in lines} == {1, 2}
    tools = next(line["result"]["tools"] for line in lines if line.get("id") == 2)
    assert len(tools) == 13
    assert all(line.startswith("{") for line in stdout.splitlines())
    assert "starting Google Ads MCP" in stderr


def test_black_box_malformed_cancel_and_bounded_auth_failure(tmp_path: Path) -> None:
    config = tmp_path / "accounts.json"
    config.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "name": "test-read-only",
                        "defaultCustomerId": "2222222222",
                        "auth": {"type": "adc", "developerTokenEnv": "ABSENT_TEST_TOKEN"},
                        "allowWrites": False,
                    }
                ]
            }
        )
    )
    config.chmod(0o600)
    messages = [
        "not-json",
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "black-box-test", "version": "1"},
                },
            }
        ),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 999, "reason": "synthetic"},
            }
        ),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "auth_check",
                    "arguments": {
                        "profile": "test-read-only",
                        "customerId": "2222222222",
                    },
                },
            }
        ),
    ]
    lines, stdout, stderr = _run_stdio_until(config, messages, {1, 2})
    result = next(line["result"] for line in lines if line.get("id") == 2)
    assert result["structuredContent"]["error"]["code"] == "developer_token_missing"
    assert any(line.get("method") == "notifications/message" for line in lines)
    assert all(line.startswith("{") for line in stdout.splitlines())
    assert "not-json" not in stderr


def test_black_box_errors_do_not_echo_credential_shaped_identifiers(tmp_path: Path) -> None:
    config = tmp_path / "accounts.json"
    config.write_text('{"profiles": []}\n')
    config.chmod(0o600)
    raw_profile = "synthetic_SECRET_PROFILE_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    raw_customer = "synthetic_SECRET_CUSTOMER_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "black-box-test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "auth_check",
                "arguments": {"profile": raw_profile, "customerId": raw_customer},
            },
        },
    ]
    _, stdout, stderr = _run_stdio_until(config, messages, {1, 2})
    assert raw_profile not in stdout
    assert raw_profile not in stderr
    assert raw_customer not in stdout
    assert raw_customer not in stderr


def test_black_box_schema_errors_do_not_echo_credential_shaped_input(tmp_path: Path) -> None:
    config = tmp_path / "accounts.json"
    config.write_text('{"profiles": []}\n')
    config.chmod(0o600)
    raw_profile = "GOC" + "SPX-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    raw_customer = "sk_" + "live_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "black-box-test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "auth_check",
                "arguments": {"profile": [raw_profile], "customerId": [raw_customer]},
            },
        },
    ]
    lines, stdout, stderr = _run_stdio_until(config, messages, {1, 2})
    assert raw_profile not in stdout
    assert raw_profile not in stderr
    assert raw_customer not in stdout
    assert raw_customer not in stderr
    result = next(line["result"] for line in lines if line.get("id") == 2)
    assert result["structuredContent"]["error"]["code"] == "invalid_request"


def test_black_box_active_cancellation_stops_adapter(tmp_path: Path) -> None:
    marker = tmp_path / "operation"
    environment = _stdio_environment(tmp_path / "unused.json")
    environment["GOOGLE_ADS_MCP_CANCELLATION_MARKER"] = str(marker)
    process = subprocess.Popen(
        [sys.executable, "tests/stdio_cancellation_server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    assert process.stdin is not None
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "black-box-test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "customers_list",
                "arguments": {"profile": "test-read-only"},
            },
        },
    ]
    try:
        process.stdin.write("\n".join(json.dumps(message) for message in messages) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 5
        while not marker.with_suffix(".started").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.with_suffix(".started").exists()
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": 2, "reason": "synthetic"},
                }
            )
            + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + 5
        while not marker.with_suffix(".cancelled").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.with_suffix(".cancelled").exists()
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    lines = [json.loads(line) for line in stdout.splitlines() if line]
    assert not any(
        line.get("id") == 2
        and line.get("result", {}).get("structuredContent", {}).get("status") == "ok"
        for line in lines
    )
    assert all(line.startswith("{") for line in stdout.splitlines())
    assert "Traceback" not in stderr
