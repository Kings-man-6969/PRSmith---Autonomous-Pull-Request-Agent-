"""CI entrypoint for PRSmith autonomous review workflow."""

import argparse
import asyncio
import os
import sys
from typing import Any, Optional, Sequence

import httpx


GITHUB_API = "https://api.github.com"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PRSmith CI review preflight for a pull request.")
    parser.add_argument("--repo", required=True, help="Repository in owner/name format.")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number.")
    parser.add_argument("--head-sha", required=True, help="Expected PR head SHA for this workflow run.")
    return parser.parse_args(argv)


def _github_headers(token: Optional[str]) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


async def fetch_pull_request(repo: str, pr_number: int, token: Optional[str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}",
            headers=_github_headers(token),
        )
        response.raise_for_status()
        return response.json()


async def run_ci_review(repo: str, pr_number: int, expected_head_sha: str, token: Optional[str]) -> int:
    pull_request = await fetch_pull_request(repo, pr_number, token)
    actual_head_sha = str(pull_request.get("head", {}).get("sha", ""))
    if not actual_head_sha:
        raise RuntimeError("GitHub API response did not include pull request head SHA.")
    if actual_head_sha != expected_head_sha:
        raise RuntimeError(
            f"Workflow head SHA ({expected_head_sha}) does not match current PR head SHA ({actual_head_sha})."
        )
    print(f"PRSmith CI preflight passed for {repo} PR #{pr_number} at {actual_head_sha}.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    token = os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN")
    try:
        return asyncio.run(run_ci_review(args.repo, args.pr, args.head_sha, token))
    except Exception as exc:
        print(f"PRSmith CI review failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
