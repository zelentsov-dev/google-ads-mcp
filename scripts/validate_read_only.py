from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "google_ads_mcp"
ALLOWED_SERVICES = {"CustomerService", "GoogleAdsFieldService", "GoogleAdsService"}
EXPECTED_TOOLS = set(json.loads((ROOT / "api-contract" / "tools-v0.1.json").read_text())["tools"])
FORBIDDEN_TOOL_PARTS = {
    "apply",
    "create",
    "delete",
    "mutate",
    "pause",
    "resume",
    "update",
    "upload",
}


def validate() -> None:
    registered_tools: set[str] = set()
    violations: list[str] = []
    for path in SOURCE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
                for decorator in node.decorator_list
            ):
                registered_tools.add(node.name)
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr.lower().startswith(("mutate", "upload")):
                violations.append(f"{path.name}:{node.lineno}: forbidden call {node.func.attr}")
            if node.func.attr != "get_service" or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                violations.append(f"{path.name}:{node.lineno}: dynamic service name")
            elif first.value not in ALLOWED_SERVICES:
                violations.append(f"{path.name}:{node.lineno}: forbidden service {first.value}")
    if registered_tools != EXPECTED_TOOLS:
        violations.append(
            f"tool contract mismatch: expected={sorted(EXPECTED_TOOLS)} "
            f"actual={sorted(registered_tools)}"
        )
    for tool in registered_tools:
        words = set(tool.lower().split("_"))
        if words & FORBIDDEN_TOOL_PARTS:
            violations.append(f"mutation-like tool name: {tool}")
    if violations:
        raise SystemExit("\n".join(violations))
    print(
        f"read-only boundary valid: {len(registered_tools)} tools, {len(ALLOWED_SERVICES)} services"
    )


if __name__ == "__main__":
    validate()
