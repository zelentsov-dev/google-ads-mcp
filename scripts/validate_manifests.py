from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def main() -> None:
    plugin = cast(
        dict[str, Any],
        json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")),
    )
    required = {"name", "version", "description", "author", "interface", "mcpServers"}
    missing = sorted(required - set(plugin))
    if missing:
        raise SystemExit(f"Plugin manifest missing field: {missing[0]}")
    if plugin["name"] != "google-ads-mcp" or not SEMVER.fullmatch(str(plugin["version"])):
        raise SystemExit("Plugin name/version is invalid")
    if plugin["mcpServers"] != "./.mcp.json":
        raise SystemExit("Plugin must reference the root .mcp.json")
    mcp = cast(dict[str, Any], json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8")))
    server = cast(dict[str, Any], mcp["mcpServers"]["google-ads"])
    if server.get("command") != "./bin/google-ads-mcp-launcher":
        raise SystemExit("MCP manifest must use the credential-free launcher")
    if server.get("args") != []:
        raise SystemExit("MCP launcher does not accept runtime arguments")
    launcher = (ROOT / "bin" / "google-ads-mcp-launcher").read_text(encoding="utf-8")
    if "google-ads-mcp serve --stdio" not in launcher or '"$@"' in launcher:
        raise SystemExit("Launcher must hard-code the frozen stdio server command")
    skill = (ROOT / "skills" / "google-ads-operator" / "SKILL.md").read_text(encoding="utf-8")
    if "name: google-ads-operator" not in skill or "[TODO" in skill:
        raise SystemExit("Operator skill frontmatter is invalid")
    agent = cast(
        dict[str, Any],
        yaml.safe_load(
            (ROOT / "skills" / "google-ads-operator" / "agents" / "openai.yaml").read_text(
                encoding="utf-8"
            )
        ),
    )
    if "$google-ads-operator" not in agent["interface"]["default_prompt"]:
        raise SystemExit("Skill default prompt must mention $google-ads-operator")
    if agent["policy"]["allow_implicit_invocation"] is not True:
        raise SystemExit("Operator skill must allow implicit invocation")
    print("plugin and operator skill manifests valid")


if __name__ == "__main__":
    main()
