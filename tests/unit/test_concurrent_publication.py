"""Failure injection test: Concurrent publication race condition suppression."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Job, PublishedReview, Repository, ReviewRun, utcnow
from backend.review.publisher import publish_review


class ConcurrentMockGitHubClient:
    def __init__(self):
        self.posted_comments = []

    async def find_comment_by_marker(self, repo, pr_number, marker, **kwargs):
        for c in self.posted_comments:
            if marker in c["body"]:
                return c
        return None

    async def post_review_comment(self, repo, pr_number, body, **kwargs):
        comment = {
            "id": f"gh-comment-{len(self.posted_comments) + 1}",
            "repo": repo,
            "pr_number": pr_number,
            "body": body,
            "created_at": utcnow(),
        }
        self.posted_comments.append(comment)

        class CommentObj:
            id = comment["id"]
            created_at = comment["created_at"]

        return CommentObj()


@pytest.mark.asyncio
async def test_concurrent_publication_single_post_invariant(db_session: AsyncSession):
    """When two workers invoke publication simultaneously, exactly one GitHub comment is posted."""
    import random
    unique_gh_id = random.randint(1000000, 9999999)
    repo = Repository(
        github_id=unique_gh_id,
        full_name=f"acme/race-condition-repo-{unique_gh_id}",
        owner="acme",
        name=f"race-condition-repo-{unique_gh_id}",
    )
    db_session.add(repo)
    await db_session.flush()

    job = Job(
        repository_id=repo.id,
        pr_number=77,
        base_sha="base77",
        head_sha="head77",
        status="VALIDATING",
    )
    db_session.add(job)
    await db_session.flush()

    # Create satisfied ReviewRun prerequisite
    review_run = ReviewRun(
        job_id=job.id,
        head_sha=job.head_sha,
        model_name="gpt-4o",
        evidence_validation_passed=True,
    )
    db_session.add(review_run)
    await db_session.commit()

    github_client = ConcurrentMockGitHubClient()
    job_id = job.id
    pr_num = job.pr_number
    repo_name = repo.full_name

    # Worker 1 runs publication
    pub1 = await publish_review(
        session=db_session,
        job=job,
        repo_full_name=repo_name,
        pr_number=pr_num,
        review_body="Detailed review findings by Worker 1",
        github_client=github_client,
        worker_id="worker-node-1",
        patch_artifact_hash=None,
    )
    assert pub1 is not None
    assert pub1.publication_status == "POSTED"

    # Worker 2 runs publication concurrently with the same pipeline output
    pub2 = await publish_review(
        session=db_session,
        job=job,
        repo_full_name=repo_name,
        pr_number=pr_num,
        review_body="Detailed review findings by Worker 2",
        github_client=github_client,
        worker_id="worker-node-2",
        patch_artifact_hash=None,
    )
    assert pub2 is None

    # Invariants:
    # 1. Exactly ONE GitHub comment was created
    assert len(github_client.posted_comments) == 1

    # 2. Exactly ONE PublishedReview record in the database
    all_pubs = (
        await db_session.scalars(
            select(PublishedReview).where(PublishedReview.job_id == job_id)
        )
    ).all()
    assert len(all_pubs) == 1
    assert all_pubs[0].worker_id == "worker-node-1"
    assert all_pubs[0].publication_status == "POSTED"
