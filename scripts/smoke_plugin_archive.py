from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, cast

_SAFE_ENVIRONMENT_KEYS = (
    "HOME",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "UV_CACHE_DIR",
    "UV_PYTHON_INSTALL_DIR",
    "WINDIR",
    "XDG_CACHE_HOME",
)


def _extract(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise SystemExit(f"Unsafe plugin archive member: {member.filename}")
        archive.extractall(destination)


def smoke(archive_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="google-ads-mcp-plugin-smoke-") as temporary:
        root = Path(temporary)
        _extract(archive_path, root)
        plugin_root = root / "google-ads-mcp"
        launcher = plugin_root / "bin" / "google-ads-mcp-launcher"
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
        config = root / "accounts.json"
        config.write_text('{"profiles": []}', encoding="utf-8")
        if os.name != "nt":
            config.chmod(0o600)

        messages = (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "archive-smoke", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        environment = {key: os.environ[key] for key in _SAFE_ENVIRONMENT_KEYS if key in os.environ}
        environment["GOOGLE_ADS_MCP_CONFIG"] = str(config)
        completed = subprocess.run(
            [str(launcher)],
            input="\n".join(json.dumps(message) for message in messages) + "\n",
            text=True,
            capture_output=True,
            env=environment,
            timeout=60,
            check=True,
        )
        try:
            responses = [
                cast(dict[str, Any], json.loads(line))
                for line in completed.stdout.splitlines()
                if line
            ]
        except json.JSONDecodeError as exc:
            raise SystemExit("Plugin stdout contained non-JSON content") from exc
        result = next(
            cast(dict[str, Any], response["result"])
            for response in responses
            if response.get("id") == 2
        )
        tools = cast(list[dict[str, Any]], result["tools"])
        expected = json.loads(
            (plugin_root / "api-contract" / "tools-v0.2.json").read_text(encoding="utf-8")
        )["tools"]
        actual = sorted(str(tool["name"]) for tool in tools)
        if actual != expected:
            raise SystemExit("Plugin archive tools/list does not match the public contract")
        if any(response.get("jsonrpc") != "2.0" for response in responses):
            raise SystemExit("Plugin archive emitted an invalid JSON-RPC response")
        print(f"plugin archive stdio smoke valid: {len(tools)} tools; stdout JSON-only")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    smoke(args.archive.resolve())


if __name__ == "__main__":
    main()
