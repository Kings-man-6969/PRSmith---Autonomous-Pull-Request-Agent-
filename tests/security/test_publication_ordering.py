"""Security test ensuring publication prerequisites are checked BEFORE claiming ownership."""

from datetime import timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Job, PublishedReview, Repository, ReviewRun, utcnow
from backend.review.exceptions import PublicationPrerequisiteError
from backend.review.publisher import claim_publication, publish_review


class MockGitHubClient:
    def __init__(self):
        self.post_calls = []

    async def find_comment_by_marker(self, repo, pr_number, marker):
        return None

    async def post_review_comment(self, repo, pr_number, body):
        self.post_calls.append({"repo": repo, "pr_number": pr_number, "body": body})

        class MockComment:
            id = "mock-comment-id-123"
            created_at = utcnow()
        return MockComment()


@pytest.mark.asyncio
async def test_prerequisites_checked_before_claim(db_session: AsyncSession):
    """If prerequisites are NOT satisfied, publication fails BEFORE claiming or posting."""
    repo = Repository(
        github_id=9001,
        full_name="test-org/pub-order-repo",
        owner="test-org",
        name="pub-order-repo",
    )
    db_session.add(repo)
    await db_session.flush()

    job = Job(
        repository_id=repo.id,
        pr_number=50,
        base_sha="base50",
        head_sha="head50",
        status="VALIDATING",
    )
    db_session.add(job)
    await db_session.commit()

    # Create ReviewRun that has evidence_validation_passed=False (prerequisite FAILS)
    review_run = ReviewRun(
        job_id=job.id,
        head_sha=job.head_sha,
        model_name="gpt-4o",
        evidence_validation_passed=False,
    )
    db_session.add(review_run)
    await db_session.commit()

    client = MockGitHubClient()

    # Attempt publication: MUST raise PublicationPrerequisiteError
    with pytest.raises(PublicationPrerequisiteError):
        await publish_review(
            session=db_session,
            job=job,
            repo_full_name=repo.full_name,
            pr_number=job.pr_number,
            review_body="## Review findings",
            github_client=client,
            worker_id="worker-1",
            patch_artifact_hash=None,
        )

    # Invariant: NO PublishedReview claim record was created
    claim_count = await db_session.scalar(
        select(PublishedReview).where(PublishedReview.job_id == job.id)
    )
    assert claim_count is None

    # Invariant: NO GitHub API call was made
    assert len(client.post_calls) == 0


@pytest.mark.asyncio
async def test_claim_prevents_concurrent_posts(db_session: AsyncSession):
    """Once a claim is held, another worker cannot claim the same publication key."""
    repo = Repository(
        github_id=9002,
        full_name="test-org/concurrent-claim-repo",
        owner="test-org",
        name="concurrent-claim-repo",
    )
    db_session.add(repo)
    await db_session.flush()

    job = Job(
        repository_id=repo.id,
        pr_number=51,
        base_sha="base51",
        head_sha="head51",
        status="VALIDATING",
    )
    db_session.add(job)
    await db_session.commit()

    idempotency_key = f"pub:{repo.id}:51:some_chain_hash_abc"

    # Worker 1 claims publication
    claim1 = await claim_publication(
        session=db_session,
        idempotency_key=idempotency_key,
        chain_hash="dummy_chain_hash",
        job=job,
        worker_id="worker-A",
    )
    assert claim1 is not None
    assert claim1.worker_id == "worker-A"

    # Worker 2 attempts to claim the same idempotency key concurrently
    claim2 = await claim_publication(
        session=db_session,
        idempotency_key=idempotency_key,
        chain_hash="dummy_chain_hash",
        job=job,
        worker_id="worker-B",
    )
    # Invariant: claim2 MUST be None because worker-A already holds it
    assert claim2 is None
