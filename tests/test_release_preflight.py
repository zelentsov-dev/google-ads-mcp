from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "google_ads_mcp_verify_release_source",
    Path(__file__).resolve().parents[1] / "scripts" / "verify_release_source.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
verify_release_source = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = verify_release_source
_SPEC.loader.exec_module(verify_release_source)

REQUIRED_WORKFLOWS = verify_release_source.REQUIRED_WORKFLOWS
verify_workflows = verify_release_source.verify_workflows

SHA = "a" * 40


def _run(**changes: object) -> dict[str, object]:
    run: dict[str, object] = {
        "head_sha": SHA,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }
    run.update(changes)
    return run


def test_release_source_requires_successful_push_run_for_every_exact_sha_workflow() -> None:
    requested: list[str] = []

    def request_json(url: str, token: str) -> object:
        assert token == "token"
        requested.append(url)
        return {"workflow_runs": [_run()]}

    verify_workflows("owner/repository", SHA, "token", request_json=request_json)

    assert len(requested) == len(REQUIRED_WORKFLOWS)
    assert all(f"head_sha={SHA}" in url for url in requested)
    assert all("branch=main" in url and "event=push" in url for url in requested)


@pytest.mark.parametrize(
    "change",
    [
        {"head_sha": "b" * 40},
        {"head_branch": "feature"},
        {"event": "pull_request"},
        {"status": "in_progress"},
        {"conclusion": "failure"},
    ],
)
def test_release_source_fails_closed_for_non_matching_workflow_run(
    change: dict[str, object],
) -> None:
    with pytest.raises(SystemExit, match="Release blocked"):
        verify_workflows(
            "owner/repository",
            SHA,
            "token",
            request_json=lambda _url, _token: {"workflow_runs": [_run(**change)]},
        )


def test_release_source_fails_closed_when_one_workflow_is_missing() -> None:
    calls = 0

    def request_json(_url: str, _token: str) -> object:
        nonlocal calls
        calls += 1
        return {"workflow_runs": [] if calls == 2 else [_run()]}

    with pytest.raises(SystemExit, match=r"security\.yml"):
        verify_workflows("owner/repository", SHA, "token", request_json=request_json)
