"""End-to-End Safety Invariant Suite.

Core property: No stale, unvalidated, or policy-escalated patch can reach GitHub.

Scenarios tested:
1. Invalid evidence → RepairPreconditionError → no publication
2. Valid finding + stale snapshot → STALE_SNAPSHOT → no publication
3. Valid + snapshot OK + validation fails → ESCALATED / blocked publication
4. Denylist path → DenylistViolation → escalation, no retry
5. AST pattern → ESCALATED, not rejected
6. Review-only, no evidence validation → PublicationPrerequisiteError
7. Fully valid pipeline → exactly one comment with marker
8. Old validation hash doesn't satisfy new patch hash publication
"""

import uuid
from unittest.mock import AsyncMock
import pytest

from backend.database.models import Job, Repository, ReviewRun, ValidationRun
from backend.database.sessions import async_session_factory
from backend.orchestration.hashing import compute_chain_hash
from backend.repair.patch_policy import (
    CATEGORY_CI_WORKFLOWS,
    PatchPolicyEnforcer,
)
from backend.review.evidence import EvidenceValidator
from backend.review.exceptions import (
    DenylistViolation,
    PublicationPrerequisiteError,
    RepairPreconditionError,
    StaleSnapshotError,
)
from backend.review.publisher import (
    assert_publication_prerequisites,
    publish_review,
)
from backend.review.schemas import Evidence, ReviewFinding
from backend.review.snapshot_service import assert_head_sha_current


@pytest.mark.asyncio
async def test_scenario_1_invalid_evidence_blocks_repair_preconditions():
    """Scenario 1: Invalid evidence claims raise RepairPreconditionError, preventing repair & publication."""
    async with async_session_factory() as session:
        finding = ReviewFinding(
            id="f-1",
            file="service.py",
            symbol="missing_target",
            category="BUG",
            severity="HIGH",
            description="Calls nonexistent method",
            repairability="LOW",
            evidence=[
                Evidence(
                    entity_type="Function",
                    entity_name="missing_func",
                    file_path="service.py",
                    relationship="calls",
                )
            ],
        )

        with pytest.raises(RepairPreconditionError) as exc_info:
            await EvidenceValidator.assert_evidence_supported(
                session=session,
                graph_version_id="gv-nonexistent",
                finding=finding,
            )
        assert "failed evidence verification" in str(exc_info.value)


def test_scenario_2_stale_snapshot_blocks_publication():
    """Scenario 2: Valid finding with drifted commit SHA raises StaleSnapshotError, blocking publication."""
    job = Job(head_sha="sha_original_11111111111111111111111111111111")
    with pytest.raises(StaleSnapshotError):
        assert_head_sha_current(
            job,
            current_head_sha="sha_drifted_22222222222222222222222222222222",
            checkpoint_name="pre-publication",
        )


@pytest.mark.asyncio
async def test_scenario_3_failed_validation_blocks_publication():
    """Scenario 3: Validation failure prevents patch publication."""
    async with async_session_factory() as session:
        job = Job(
            id=str(uuid.uuid4()),
            repository_id=str(uuid.uuid4()),
            base_sha="0000000000000000000000000000000000000000",
            head_sha="sha_abc123",
            pr_number=1,
            status="VALIDATING",
        )
        session.add(job)

        val_run = ValidationRun(
            job_id=job.id,
            stage="FINAL",
            head_sha=job.head_sha,
            patch_artifact_hash="patch_hash_1",
            result_hash="res_hash_failed",
            passed=False,  # Tests failed!
        )
        session.add(val_run)
        await session.commit()

        # Attempting publication prerequisites on failed validation raises PublicationPrerequisiteError
        with pytest.raises(PublicationPrerequisiteError):
            await assert_publication_prerequisites(
                session, job, patch_artifact_hash="patch_hash_1"
            )


def test_scenario_4_denylist_path_raises_denylist_violation():
    """Scenario 4: Modifying CI workflow or forbidden files raises DenylistViolation, requiring escalation."""
    sample_patch = """--- a/.github/workflows/deploy.yml
+++ b/.github/workflows/deploy.yml
@@ -1,3 +1,4 @@
 def example():
+    pass
"""
    res = PatchPolicyEnforcer.evaluate_patch(sample_patch)
    assert res.action == "REJECT"
    assert CATEGORY_CI_WORKFLOWS in res.category_violations
    with pytest.raises(DenylistViolation):
        PatchPolicyEnforcer.assert_patch_allowed(res)


def test_scenario_5_ast_sensitive_pattern_triggers_escalation_not_rejection():
    """Scenario 5: Dangerous AST calls (e.g. eval, subprocess) trigger human escalation, not silent rejection."""
    diff_system = """--- a/src/utils.py
+++ b/src/utils.py
@@ -1,3 +1,4 @@
 def example():
+    os.system('cleanup.sh')
"""
    res = PatchPolicyEnforcer.evaluate_patch(diff_system)
    assert res.action == "ESCALATE"
    assert res.is_allowed is False
    assert any("os.system" in reason for reason in res.escalation_reasons)
    # Rejection reasons must be empty (it is escalated for human review, not rejected)
    assert len(res.reasons) == 0


@pytest.mark.asyncio
async def test_scenario_6_review_only_without_evidence_check_blocks_publication():
    """Scenario 6: Review-only publication fails if ReviewRun did not pass evidence validation."""
    async with async_session_factory() as session:
        job = Job(
            id=str(uuid.uuid4()),
            repository_id=str(uuid.uuid4()),
            base_sha="0000000000000000000000000000000000000000",
            head_sha="sha_rev_only_123",
            pr_number=2,
            status="REVIEWING",
        )
        session.add(job)

        review_run = ReviewRun(
            job_id=job.id,
            head_sha=job.head_sha,
            model_name="gpt-4o",
            evidence_validation_passed=False,  # Failed evidence gate
        )
        session.add(review_run)
        await session.commit()

        with pytest.raises(PublicationPrerequisiteError):
            await assert_publication_prerequisites(session, job, patch_artifact_hash=None)


@pytest.mark.asyncio
async def test_scenario_7_fully_valid_pipeline_posts_exactly_one_comment():
    """Scenario 7: Fully valid pipeline completes with exactly one comment containing the idempotency marker."""
    async with async_session_factory() as session:
        unique_suffix = uuid.uuid4().hex[:8]
        repo = Repository(
            id=str(uuid.uuid4()),
            github_id=uuid.uuid4().int % 1000000,
            full_name=f"acme/safety-pipeline-{unique_suffix}",
            owner="acme",
            name=f"safety-pipeline-{unique_suffix}",
        )
        session.add(repo)
        await session.flush()

        head_sha = "deadbeef12345678901234567890123456789012"
        job = Job(
            id=str(uuid.uuid4()),
            repository_id=repo.id,
            pr_number=77,
            base_sha="0000000000000000000000000000000000000000",
            head_sha=head_sha,
            status="VALIDATING",
        )
        session.add(job)

        val_run = ValidationRun(
            job_id=job.id,
            stage="FINAL",
            head_sha=head_sha,
            patch_artifact_hash="valid_patch_hash_77",
            result_hash="res_hash_ok",
            passed=True,
        )
        session.add(val_run)
        await session.commit()

        # Mock GitHub client
        mock_github = AsyncMock()
        mock_github.find_comment_by_marker.return_value = None
        mock_comment = AsyncMock()
        mock_comment.id = "gh-posted-comment-77"
        mock_github.post_review_comment.return_value = mock_comment

        pub = await publish_review(
            session=session,
            job=job,
            repo_full_name=repo.full_name,
            pr_number=job.pr_number,
            review_body="Automated Repair Validation Passed",
            github_client=mock_github,
            worker_id="worker-safety-1",
            patch_artifact_hash="valid_patch_hash_77",
        )

        assert pub is not None
        assert pub.publication_status == "POSTED"
        assert pub.github_comment_id == "gh-posted-comment-77"
        assert mock_github.post_review_comment.call_count == 1

        # Marker was included in the posted body
        called_args, called_kwargs = mock_github.post_review_comment.call_args
        posted_body = called_kwargs.get("body", "")
        assert "<!-- prsmith:review:" in posted_body


@pytest.mark.asyncio
async def test_scenario_8_old_validation_hash_does_not_satisfy_new_patch_publication():
    """Scenario 8: Passing ValidationRun for an old patch does not satisfy prerequisites for a new patch."""
    async with async_session_factory() as session:
        job = Job(
            id=str(uuid.uuid4()),
            repository_id=str(uuid.uuid4()),
            base_sha="0000000000000000000000000000000000000000",
            head_sha="sha_fixed_123",
            pr_number=8,
            status="VALIDATING",
        )
        session.add(job)

        # Validation passed for patch A
        val_run_a = ValidationRun(
            job_id=job.id,
            stage="FINAL",
            head_sha=job.head_sha,
            patch_artifact_hash="patch_A_hash",
            result_hash="res_hash_a",
            passed=True,
        )
        session.add(val_run_a)
        await session.commit()

        # Worker attempts to publish patch B -> fails prerequisite check
        with pytest.raises(PublicationPrerequisiteError):
            await assert_publication_prerequisites(
                session, job, patch_artifact_hash="patch_B_hash"
            )
