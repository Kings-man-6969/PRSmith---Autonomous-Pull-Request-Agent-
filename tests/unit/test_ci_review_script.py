import pytest

from scripts import ci_review


def test_main_invokes_runner_with_cli_args(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_run(repo: str, pr_number: int, expected_head_sha: str, token: str) -> int:
        captured["repo"] = repo
        captured["pr_number"] = pr_number
        captured["expected_head_sha"] = expected_head_sha
        captured["token"] = token
        return 0

    monkeypatch.setattr(ci_review, "run_ci_review", fake_run)
    monkeypatch.setenv("GITHUB_PAT", "ghp_test_token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    exit_code = ci_review.main(
        ["--repo", "octocat/hello-world", "--pr", "42", "--head-sha", "abc123"]
    )

    assert exit_code == 0
    assert captured == {
        "repo": "octocat/hello-world",
        "pr_number": 42,
        "expected_head_sha": "abc123",
        "token": "ghp_test_token",
    }


@pytest.mark.asyncio
async def test_run_ci_review_fails_on_head_sha_mismatch(monkeypatch):
    async def fake_fetch(repo: str, pr_number: int, token: str) -> dict[str, object]:
        return {"head": {"sha": "different-sha"}}

    monkeypatch.setattr(ci_review, "fetch_pull_request", fake_fetch)

    with pytest.raises(RuntimeError, match="does not match current PR head SHA"):
        await ci_review.run_ci_review("octocat/hello-world", 42, "expected-sha", "ghp_test_token")
