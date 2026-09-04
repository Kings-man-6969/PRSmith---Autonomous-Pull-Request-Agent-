"""Security tests for publication prerequisites, atomic claims, and marker reconciliation."""

import uuid
from unittest.mock import AsyncMock
import pytest

from backend.database.models import Job, PublishedReview, Repository, ReviewRun, ValidationRun
from backend.database.sessions import async_session_factory
from backend.review.exceptions import PublicationPrerequisiteError
from backend.review.publisher import (
    assert_publication_prerequisites,
    claim_publication,
    publish_review_comment,
)


@pytest.mark.asyncio
async def test_publication_prerequisites_repair_path():
    async with async_session_factory() as session:
        repo = Repository(
            id=str(uuid.uuid4()),
            github_id=9871,
            full_name="org/repo-pub",
            owner="org",
            name="repo-pub",
        )
        session.add(repo)
        await session.flush()

        sha = "1234567890123456789012345678901234567890"
        job = Job(
            id=str(uuid.uuid4()),
            repository_id=repo.id,
            pr_number=5,
            base_sha="0000000000000000000000000000000000000000",
            head_sha=sha,
            status="VALIDATING",
        )
        session.add(job)
        await session.flush()

        # 1. No validation run exists -> raises PublicationPrerequisiteError
        with pytest.raises(PublicationPrerequisiteError):
            await assert_publication_prerequisites(session, job, patch_artifact_hash="patch_hash_x")

        # 2. Add failed validation run -> still raises
        val_failed = ValidationRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            stage="FINAL",
            passed=False,
            head_sha=sha,
            patch_artifact_hash="patch_hash_x",
        )
        session.add(val_failed)
        await session.commit()

        with pytest.raises(PublicationPrerequisiteError):
            await assert_publication_prerequisites(session, job, patch_artifact_hash="patch_hash_x")

        # 3. Add passing validation run with WRONG head_sha -> still raises (commit drift check)
        val_wrong_sha = ValidationRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            stage="FINAL",
            passed=True,
            head_sha="different_sha_12345678901234567890123456",
            patch_artifact_hash="patch_hash_x",
        )
        session.add(val_wrong_sha)
        await session.commit()

        with pytest.raises(PublicationPrerequisiteError):
            await assert_publication_prerequisites(session, job, patch_artifact_hash="patch_hash_x")

        # 4. Add passing validation run with exact head_sha and exact patch -> succeeds
        val_success = ValidationRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            stage="FINAL",
            passed=True,
            head_sha=sha,
            patch_artifact_hash="patch_hash_x",
        )
        session.add(val_success)
        await session.commit()

        result = await assert_publication_prerequisites(session, job, patch_artifact_hash="patch_hash_x")
        assert result.id == val_success.id


@pytest.mark.asyncio
async def test_publication_prerequisites_review_only_path():
    async with async_session_factory() as session:
        repo = Repository(
            id=str(uuid.uuid4()),
            github_id=9872,
            full_name="org/repo-rev",
            owner="org",
            name="repo-rev",
        )
        session.add(repo)
        await session.flush()

        sha = "9999999999999999999999999999999999999999"
        job = Job(
            id=str(uuid.uuid4()),
            repository_id=repo.id,
            pr_number=6,
            base_sha="0000000000000000000000000000000000000000",
            head_sha=sha,
            status="REVIEWING",
        )
        session.add(job)
        await session.flush()

        # Review-only (patch_artifact_hash=None) without evidence check passed -> raises
        with pytest.raises(PublicationPrerequisiteError):
            await assert_publication_prerequisites(session, job, patch_artifact_hash=None)

        # Passing evidence check -> succeeds
        review_run = ReviewRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            head_sha=sha,
            evidence_validation_passed=True,
            model_name="test-llm",
        )
        session.add(review_run)
        await session.commit()

        res = await assert_publication_prerequisites(session, job, patch_artifact_hash=None)
        assert res.id == review_run.id


@pytest.mark.asyncio
async def test_publication_claim_and_marker_reconciliation():
    async with async_session_factory() as session:
        job = Job(
            id=str(uuid.uuid4()),
            repository_id=str(uuid.uuid4()),
            pr_number=10,
            base_sha="base",
            head_sha="head",
            status="PUBLISHING",
        )
        session.add(job)
        await session.commit()

        key = f"pub:repo1:10:head:{uuid.uuid4().hex}"
        chain_hash = "chain_hash_abc123"

        # Worker 1 claims publication
        pub1 = await claim_publication(
            session=session,
            idempotency_key=key,
            chain_hash=chain_hash,
            job=job,
            worker_id="worker-1",
        )
        assert pub1 is not None
        assert pub1.publication_status == "CLAIMED"

        # Worker 2 attempts same publication concurrently -> blocked
        pub2 = await claim_publication(
            session=session,
            idempotency_key=key,
            chain_hash=chain_hash,
            job=job,
            worker_id="worker-2",
        )
        assert pub2 is None

        # Post with marker reconciliation: simulate crash after GitHub comment was posted
        mock_github = AsyncMock()
        mock_github.find_comment_by_marker.return_value = {"id": 123456, "body": f"<!-- prsmith:{key} -->"}

        published = await publish_review_comment(
            session=session,
            pub=pub1,
            repo_full_name="owner/repo",
            pr_number=10,
            review_body="Review summary",
            github_client=mock_github,
        )

        assert published.publication_status == "POSTED"
        assert published.github_comment_id == "123456"
        # Since existing marker was found, post_review_comment was NOT called again (idempotent!)
        mock_github.post_review_comment.assert_not_called()
