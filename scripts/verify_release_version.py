from __future__ import annotations

import json
import os
from pathlib import Path

from google_ads_mcp.constants import VERSION


def main() -> None:
    tag = os.environ.get("GITHUB_REF_NAME")
    if tag != f"v{VERSION}":
        raise SystemExit(f"Release tag must be v{VERSION}, got {tag or '<missing>'}")
    plugin = json.loads(Path(".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    baseline = json.loads(Path("api-contract/upstream-baseline.json").read_text(encoding="utf-8"))
    if plugin["version"] != VERSION:
        raise SystemExit("Plugin and Python versions do not match")
    if baseline["googleAdsPython"] != "31.2.0" or baseline["googleAdsApi"] != "v25":
        raise SystemExit("Release upstream baseline changed without review")
    print(f"release version valid: {tag}")


if __name__ == "__main__":
    main()
