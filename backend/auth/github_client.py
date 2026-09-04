"""GitHub REST client for fetching PR metadata and posting structured reviews."""

from typing import Any, Dict, List, Optional
import httpx

from backend.auth.github_app import GitHubAppAuth
from backend.observability.logging import get_logger, log_event

logger = get_logger(__name__)

_GH_API = "https://api.github.com"


class GitHubClient:
    """Provides GitHub API integration for Pull Requests and repository discovery."""

    def __init__(self, auth: Optional[GitHubAppAuth] = None):
        self.auth = auth or GitHubAppAuth()

    # ── Installation-token methods (webhook-triggered flows) ─────────────────

    async def get_pull_request(
        self, installation_id: int, owner: str, repo: str, pr_number: int
    ) -> Dict[str, Any]:
        token = await self.auth.get_installation_token(installation_id)
        url = f"{_GH_API}/repos/{owner}/{repo}/pulls/{pr_number}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            res.raise_for_status()
            return res.json()

    async def get_pull_request_diff(
        self, installation_id: int, owner: str, repo: str, pr_number: int
    ) -> str:
        token = await self.auth.get_installation_token(installation_id)
        url = f"{_GH_API}/repos/{owner}/{repo}/pulls/{pr_number}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3.diff",
        }
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            res.raise_for_status()
            return res.text

    async def post_review_comment(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
    ) -> None:
        token = await self.auth.get_installation_token(installation_id)
        url = f"{_GH_API}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers, json={"body": body})
            if res.status_code == 201:
                log_event(
                    "review.published",
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                )
            else:
                logger.error(
                    "Failed to post PR review comment",
                    status=res.status_code,
                    body=res.text,
                )

    # ── PAT-based discovery methods (dashboard / repo-hub flows) ─────────────

    @staticmethod
    def _pat_headers(pat: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def list_user_repos(self, pat: str) -> List[Dict[str, Any]]:
        """List all repositories accessible to the PAT owner, newest-updated first."""
        results: List[Dict[str, Any]] = []
        page = 1
        headers = self._pat_headers(pat)

        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                res = await client.get(
                    f"{_GH_API}/user/repos",
                    headers=headers,
                    params={"per_page": 100, "sort": "updated", "page": page},
                )
                res.raise_for_status()
                batch = res.json()
                if not batch:
                    break
                results.extend(batch)
                if len(batch) < 100:
                    break
                page += 1

        return [
            {
                "id": r["id"],
                "full_name": r["full_name"],
                "owner": r["owner"]["login"],
                "name": r["name"],
                "private": r["private"],
                "default_branch": r.get("default_branch", "main"),
                "updated_at": r.get("updated_at", ""),
                "open_issues_count": r.get("open_issues_count", 0),
            }
            for r in results
        ]

    async def list_repo_branches(self, pat: str, owner: str, repo: str) -> List[str]:
        """List branch names for a repository."""
        headers = self._pat_headers(pat)
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                f"{_GH_API}/repos/{owner}/{repo}/branches",
                headers=headers,
                params={"per_page": 100},
            )
            res.raise_for_status()
            return [b["name"] for b in res.json()]

    async def list_open_prs(self, pat: str, owner: str, repo: str) -> List[Dict[str, Any]]:
        """List open pull requests for a repository."""
        headers = self._pat_headers(pat)
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                f"{_GH_API}/repos/{owner}/{repo}/pulls",
                headers=headers,
                params={
                    "state": "open",
                    "per_page": 50,
                    "sort": "created",
                    "direction": "desc",
                },
            )
            res.raise_for_status()
            return [
                {
                    "number": p["number"],
                    "title": p["title"],
                    "head_branch": p["head"]["ref"],
                    "created_at": p["created_at"],
                    "user": p["user"]["login"],
                }
                for p in res.json()
            ]
