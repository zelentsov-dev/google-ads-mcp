from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", default="checksums.txt")
    args = parser.parse_args()
    directory = args.directory.resolve()
    output = directory / args.output
    lines: list[str] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path == output:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
