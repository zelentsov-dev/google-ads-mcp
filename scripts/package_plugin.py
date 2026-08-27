from __future__ import annotations

import argparse
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE_FILES = {
    ".mcp.json",
    "LICENSE",
    "PRIVACY.md",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "uv.lock",
}
INCLUDE_ROOTS = {".codex-plugin", "api-contract", "bin", "docs", "skills", "src"}
DENIED_NAMES = {
    ".env",
    "accounts.json",
    "application_default_credentials.json",
    "google-ads.yaml",
}
DENIED_SUFFIXES = {".key", ".p12", ".pem", ".pfx", ".pyc"}


def included_files() -> list[Path]:
    result: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {".git", ".venv", "dist"} or "__pycache__" in relative.parts:
            continue
        if relative.as_posix() in INCLUDE_FILES or relative.parts[0] in INCLUDE_ROOTS:
            lowered = path.name.lower()
            if lowered in DENIED_NAMES or path.suffix.lower() in DENIED_SUFFIXES:
                raise SystemExit(f"Refusing to package denied file: {relative}")
            result.append(path)
    return sorted(result)


def build(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in included_files():
            relative = path.relative_to(ROOT)
            info = zipfile.ZipInfo(f"google-ads-mcp/{relative.as_posix()}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            mode = 0o755 if relative.as_posix() == "bin/google-ads-mcp-launcher" else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
