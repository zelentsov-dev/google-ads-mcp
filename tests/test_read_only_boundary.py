from __future__ import annotations

import subprocess
import sys


def test_static_safe_operator_boundary() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/validate_read_only.py"],
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert "45 tools" in completed.stdout
    assert "11 operation types" in completed.stdout
    assert "13 forbidden services" in completed.stdout
