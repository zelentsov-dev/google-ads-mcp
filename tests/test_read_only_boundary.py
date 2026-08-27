from __future__ import annotations

import subprocess
import sys


def test_static_read_only_boundary() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/validate_read_only.py"],
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert "13 tools" in completed.stdout
