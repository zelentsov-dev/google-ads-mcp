from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

CRITICAL_FILES = (
    "src/google_ads_mcp/config.py",
    "src/google_ads_mcp/gaql.py",
    "src/google_ads_mcp/security.py",
    "src/google_ads_mcp/server.py",
)


def main() -> None:
    report = cast(dict[str, Any], json.loads(Path("coverage.json").read_text(encoding="utf-8")))
    files = cast(dict[str, Any], report["files"])
    failed: list[str] = []
    for name in CRITICAL_FILES:
        summary = cast(dict[str, Any], files[name]["summary"])
        covered = float(summary["percent_covered"])
        if covered != 100.0:
            failed.append(f"{name}: {covered:.2f}%")
    if failed:
        raise SystemExit("Safety-critical branch coverage must be 100%:\n" + "\n".join(failed))
    print(f"safety-critical branch coverage valid: {len(CRITICAL_FILES)} files at 100%")


if __name__ == "__main__":
    main()
