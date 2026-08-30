from __future__ import annotations

import http.client
import json
import os
import urllib.parse
from collections.abc import Callable

REQUIRED_WORKFLOWS = ("ci.yml", "security.yml", "codeql.yml")


def _successful_exact_main_run(payload: object, release_sha: str) -> bool:
    if not isinstance(payload, dict):
        return False
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        return False
    return any(
        isinstance(run, dict)
        and run.get("head_sha") == release_sha
        and run.get("head_branch") == "main"
        and run.get("event") == "push"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        for run in runs
    )


def verify_workflows(
    repository: str,
    release_sha: str,
    token: str,
    *,
    request_json: Callable[[str, str], object] | None = None,
) -> None:
    requester = request_json or _request_json
    missing: list[str] = []
    for workflow in REQUIRED_WORKFLOWS:
        encoded = urllib.parse.quote(workflow, safe="")
        query = urllib.parse.urlencode(
            {
                "branch": "main",
                "event": "push",
                "head_sha": release_sha,
                "status": "completed",
                "per_page": "100",
            }
        )
        url = (
            f"https://api.github.com/repos/{repository}/actions/workflows/{encoded}/runs?{query}"
        )
        if not _successful_exact_main_run(requester(url, token), release_sha):
            missing.append(workflow)
    if missing:
        raise SystemExit(
            "Release blocked: exact main SHA has no successful completed push run for "
            + ", ".join(missing)
        )


def _request_json(url: str, token: str) -> object:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "api.github.com":
        raise SystemExit("Release preflight API URL is invalid")
    connection = http.client.HTTPSConnection("api.github.com", timeout=30)
    try:
        connection.request(
            "GET",
            urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, "")),
            headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "google-ads-mcp-release-preflight",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise SystemExit(f"GitHub Actions API returned HTTP {response.status}")
        return json.loads(response.read())
    finally:
        connection.close()


def main() -> None:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    release_sha = os.environ.get("RELEASE_SHA", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repository or "/" not in repository:
        raise SystemExit("GITHUB_REPOSITORY is required")
    if len(release_sha) != 40 or any(
        character not in "0123456789abcdef" for character in release_sha
    ):
        raise SystemExit("RELEASE_SHA must be a lowercase 40-character commit SHA")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")
    verify_workflows(repository, release_sha, token)
    print(f"release preflight: PASS — exact main SHA {release_sha}")


if __name__ == "__main__":
    main()
