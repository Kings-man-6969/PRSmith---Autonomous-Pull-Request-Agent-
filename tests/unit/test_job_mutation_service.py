"""Unit tests for JobMutationService concurrency control, leases, and optimistic versioning."""

from datetime import timedelta
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Job, Repository, utcnow
from backend.orchestration.job_service import JobMutationService
from backend.orchestration.leases import recover_stale_job_leases
from backend.orchestration.state_machine import (
    InvalidStateTransitionError,
    LeaseConflictError,
    StaleJobError,
)


@pytest.mark.asyncio
async def test_job_transition_advances_version_and_status(db_session: AsyncSession):
    """Transitioning status updates status and increments job version atomically."""
    repo = Repository(
        github_id=8881,
        full_name="test-org/job-test-repo",
        owner="test-org",
        name="job-test-repo",
    )
    db_session.add(repo)
    await db_session.flush()

    job = Job(
        repository_id=repo.id,
        pr_number=10,
        base_sha="base10",
        head_sha="head10",
        status="PENDING",
        version=1,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    assert job.version == 1
    assert job.status == "PENDING"

    # Legal transition: PENDING -> CLONING
    updated_job = await JobMutationService.transition(
        session=db_session,
        job=job,
        new_status="CLONING",
    )
    await db_session.commit()

    assert updated_job.status == "CLONING"
    assert updated_job.version == 2


@pytest.mark.asyncio
async def test_job_optimistic_concurrency_conflict_raises_stale_job_error(db_session: AsyncSession):
    """If another transaction modifies version concurrently, transition raises StaleJobError."""
    repo = Repository(
        github_id=8882,
        full_name="test-org/job-conflict-repo",
        owner="test-org",
        name="job-conflict-repo",
    )
    db_session.add(repo)
    await db_session.flush()

    job = Job(
        repository_id=repo.id,
        pr_number=11,
        base_sha="base11",
        head_sha="head11",
        status="PENDING",
        version=1,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Artificially modify job version in DB to simulate concurrent mutation
    job.version = 999  # In-memory version is out of sync with actual DB row

    with pytest.raises(StaleJobError):
        await JobMutationService.transition(
            session=db_session,
            job=job,
            new_status="CLONING",
        )


@pytest.mark.asyncio
async def test_worker_lease_lifecycle_and_conflict(db_session: AsyncSession):
    """Test acquiring, renewing, conflicting, and releasing worker execution leases."""
    repo = Repository(
        github_id=8883,
        full_name="test-org/job-lease-repo",
        owner="test-org",
        name="job-lease-repo",
    )
    db_session.add(repo)
    await db_session.flush()

    job = Job(
        repository_id=repo.id,
        pr_number=12,
        base_sha="base12",
        head_sha="head12",
        status="PENDING",
        version=1,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # Worker A acquires lease
    await JobMutationService.acquire_lease(
        session=db_session,
        job=job,
        worker_id="worker-A",
        duration_s=300,
    )
    await db_session.commit()
    assert job.worker_id == "worker-A"
    assert job.lease_until is not None

    # Worker B tries to acquire active lease -> raises LeaseConflictError
    with pytest.raises(LeaseConflictError):
        await JobMutationService.acquire_lease(
            session=db_session,
            job=job,
            worker_id="worker-B",
        )

    # Worker B tries to transition job -> raises LeaseConflictError
    with pytest.raises(LeaseConflictError):
        await JobMutationService.transition(
            session=db_session,
            job=job,
            new_status="CLONING",
            worker_id="worker-B",
        )

    # Worker A successfully transitions job
    await JobMutationService.transition(
        session=db_session,
        job=job,
        new_status="CLONING",
        worker_id="worker-A",
    )
    await db_session.commit()
    assert job.status == "CLONING"

    # Worker A renews lease
    renewed = await JobMutationService.renew_lease(
        session=db_session,
        job=job,
        worker_id="worker-A",
        extension_s=600,
    )
    assert renewed is True

    # Worker A releases lease
    await JobMutationService.release_lease(
        session=db_session,
        job=job,
        worker_id="worker-A",
    )
    await db_session.commit()
    assert job.worker_id is None
    assert job.lease_until is None


@pytest.mark.asyncio
async def test_stale_lease_recovery(db_session: AsyncSession):
    """Expired worker leases are detected and marked FAILED with explanatory error."""
    repo = Repository(
        github_id=8884,
        full_name="test-org/job-stale-repo",
        owner="test-org",
        name="job-stale-repo",
    )
    db_session.add(repo)
    await db_session.flush()

    past_time = utcnow() - timedelta(minutes=10)
    job = Job(
        repository_id=repo.id,
        pr_number=13,
        base_sha="base13",
        head_sha="head13",
        status="CLONING",
        worker_id="crashed-worker-99",
        lease_until=past_time,
        version=2,
    )
    db_session.add(job)
    await db_session.commit()

    recovered = await recover_stale_job_leases(session=db_session, mark_as_failed=True)
    assert len(recovered) == 1
    assert recovered[0].id == job.id
    assert recovered[0].status == "FAILED"
    assert "Execution abandoned" in recovered[0].error_message
