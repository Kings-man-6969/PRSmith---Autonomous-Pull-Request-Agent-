"""Failure injection test: PR Head SHA changes mid-pipeline.

Verifies:
1. assert_head_sha_current detects drift and raises StaleSnapshotError.
2. mark_superseded creates a new replacement job and links superseded_by_job_id.
3. Concurrent mark_superseded calls produce exactly one active replacement job.
"""

import uuid
import pytest
from sqlalchemy import select

from backend.database.models import Job, Repository
from backend.database.sessions import async_session_factory
from backend.review.exceptions import StaleSnapshotError
from backend.review.snapshot_service import assert_head_sha_current, mark_superseded


@pytest.mark.asyncio
async def test_pr_sha_change_detection_and_superseded():
    async with async_session_factory() as session:
        unique_suffix = uuid.uuid4().hex[:8]
        repo = Repository(
            id=str(uuid.uuid4()),
            github_id=int(uuid.uuid4().int % 1000000),
            full_name=f"acme/sha-drift-repo-{unique_suffix}",
            owner="acme",
            name=f"sha-drift-repo-{unique_suffix}",
        )
        session.add(repo)
        await session.flush()

        initial_sha = "1111111111111111111111111111111111111111"
        job = Job(
            id=str(uuid.uuid4()),
            repository_id=repo.id,
            pr_number=99,
            base_sha="0000000000000000000000000000000000000000",
            head_sha=initial_sha,
            status="REVIEWING",
        )
        session.add(job)
        await session.commit()

        # Checkpoint 1: Commit matches -> no error
        assert_head_sha_current(job, current_head_sha=initial_sha, checkpoint_name="pre-review")

        # Developer pushes new commit while review is in progress
        new_commit_sha = "2222222222222222222222222222222222222222"

        # Checkpoint 2: Commit drifted -> raises StaleSnapshotError
        with pytest.raises(StaleSnapshotError) as exc_info:
            assert_head_sha_current(job, current_head_sha=new_commit_sha, checkpoint_name="pre-repair")
        assert "Commit drift detected" in str(exc_info.value)

        # Worker 1 transitions job to superseded
        replacement_job_1 = await mark_superseded(session, job, new_head_sha=new_commit_sha)

        assert replacement_job_1.head_sha == new_commit_sha
        assert replacement_job_1.status == "RECEIVED"
        assert job.status == "STALE_SNAPSHOT"
        assert job.superseded_by_job_id == replacement_job_1.id

        # Worker 2 concurrently attempts mark_superseded with same new SHA
        replacement_job_2 = await mark_superseded(session, job, new_head_sha=new_commit_sha)

        # Invariant: exactly one active replacement job created
        assert replacement_job_2.id == replacement_job_1.id
