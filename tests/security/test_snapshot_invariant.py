"""Security tests for snapshot immutability and commit drift checkpoints."""

import uuid
import pytest

from backend.database.models import Job, Repository
from backend.database.sessions import async_session_factory
from backend.review.exceptions import StaleSnapshotError
from backend.review.snapshot_service import assert_head_sha_current, mark_superseded


def test_assert_head_sha_current_detects_drift():
    job = Job(id="job-1", head_sha="1111111111111111111111111111111111111111")

    # Match passes
    assert_head_sha_current(job, "1111111111111111111111111111111111111111")

    # Drift raises StaleSnapshotError
    with pytest.raises(StaleSnapshotError) as exc:
        assert_head_sha_current(job, "2222222222222222222222222222222222222222", checkpoint_name="pre_publication")
    assert "Commit drift detected at pre_publication" in str(exc.value)


@pytest.mark.asyncio
async def test_mark_superseded_creates_immutable_new_job():
    async with async_session_factory() as session:
        repo = Repository(
            id=str(uuid.uuid4()),
            github_id=98765,
            full_name=f"test-org/repo-{uuid.uuid4().hex[:6]}",
            owner="test-org",
            name="repo",
        )
        session.add(repo)
        await session.flush()

        initial_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        drifted_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        job = Job(
            id=str(uuid.uuid4()),
            repository_id=repo.id,
            pr_number=101,
            pr_title="Fix bug",
            base_sha="0000000000000000000000000000000000000000",
            head_sha=initial_sha,
            status="REVIEWING",
            version=1,
        )
        session.add(job)
        await session.commit()

        # Supersede job due to new commit push
        new_job = await mark_superseded(session, job, new_head_sha=drifted_sha)

        # Invariant checks
        assert new_job.id != job.id
        assert new_job.head_sha == drifted_sha
        assert new_job.status == "RECEIVED"

        # Old job must remain an immutable record of the old SHA
        assert job.head_sha == initial_sha
        assert job.status == "STALE_SNAPSHOT"
        assert job.superseded_by_job_id == new_job.id
