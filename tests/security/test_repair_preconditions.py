"""Security tests verifying explicit domain exceptions and repair preconditions."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.review.evidence import EvidenceValidator
from backend.review.exceptions import (
    DenylistViolation,
    PublicationPrerequisiteError,
    RepairPreconditionError,
    ReviewError,
    StaleFindingError,
    StaleSnapshotError,
)
from backend.review.schemas import Evidence, ReviewFinding


def test_exception_inheritance_hierarchy():
    """All domain review and repair exceptions inherit from ReviewError."""
    assert issubclass(RepairPreconditionError, ReviewError)
    assert issubclass(StaleFindingError, RepairPreconditionError)
    assert issubclass(StaleSnapshotError, RepairPreconditionError)
    assert issubclass(DenylistViolation, RepairPreconditionError)
    assert issubclass(PublicationPrerequisiteError, ReviewError)


@pytest.mark.asyncio
async def test_evidence_validator_rejects_missing_graph_version(db_session: AsyncSession):
    """Calling assert_evidence_supported without graph_version_id raises StaleFindingError."""
    finding = ReviewFinding(
        id="find-1",
        file="src/auth.py",
        symbol="verify_token",
        category="SECURITY",
        severity="HIGH",
        description="Missing signature check",
        repairability="MEDIUM",
        evidence=[
            Evidence(
                entity_type="Function",
                entity_name="jwt.decode",
                file_path="src/jwt.py",
                relationship="calls",
            )
        ],
    )

    with pytest.raises(StaleFindingError) as exc:
        await EvidenceValidator.assert_evidence_supported(
            session=db_session,
            graph_version_id="",  # Missing / empty
            finding=finding,
        )
    assert "missing or null graph_version_id" in str(exc.value)


@pytest.mark.asyncio
async def test_evidence_validator_rejects_unsupported_claim(db_session: AsyncSession):
    """Unsupported graph claim raises RepairPreconditionError."""
    finding = ReviewFinding(
        id="find-2",
        file="src/billing.py",
        symbol="charge_card",
        category="BUG",
        severity="MEDIUM",
        description="Wrong call",
        repairability="MEDIUM",
        evidence=[
            Evidence(
                entity_type="Function",
                entity_name="nonexistent_payment_gateway",
                file_path="src/gateway.py",
                relationship="calls",
            )
        ],
    )

    with pytest.raises(RepairPreconditionError) as exc:
        await EvidenceValidator.assert_evidence_supported(
            session=db_session,
            graph_version_id="graph-ver-nonexistent",
            finding=finding,
        )
    assert "failed evidence verification" in str(exc.value)
