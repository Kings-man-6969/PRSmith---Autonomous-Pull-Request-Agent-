"""Hallucination gate validating reviewer claims against the Knowledge Graph."""

from typing import List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from backend.graph.queries import GraphQueries
from backend.observability.logging import get_logger
from backend.review.exceptions import RepairPreconditionError, StaleFindingError
from backend.review.schemas import ReviewFinding

logger = get_logger(__name__)


class EvidenceValidator:
    """Validates claims in reviewer findings using the Knowledge Graph."""

    @staticmethod
    async def validate_finding_evidence(
        session: AsyncSession, graph_version_id: str, finding: ReviewFinding
    ) -> Tuple[bool, ReviewFinding]:
        """Verify each piece of evidence in the finding against the knowledge graph."""
        validated_evidence = []
        has_unsupported_claim = False

        for ev in finding.evidence:
            if ev.relationship in ["calls", "reads_db", "writes_db", "tests"]:
                is_supported = await GraphQueries.check_claim_support(
                    session,
                    graph_version_id,
                    finding.symbol or finding.file,
                    ev.entity_name,
                    ev.relationship,
                )
                ev.verified_in_graph = is_supported
                if not is_supported:
                    has_unsupported_claim = True
                    logger.warning(
                        "Reviewer claim not supported in Knowledge Graph",
                        symbol=finding.symbol,
                        claimed_target=ev.entity_name,
                        claimed_rel=ev.relationship,
                    )
            validated_evidence.append(ev)

        finding.evidence = validated_evidence

        # If key claims are ungrounded, reduce confidence or mark low repairability
        if has_unsupported_claim and finding.evidence:
            finding.confidence = max(0.2, finding.confidence * 0.5)
            if finding.confidence < 0.5:
                finding.repairability = "LOW"

        return not has_unsupported_claim, finding

    @staticmethod
    async def assert_evidence_supported(
        session: AsyncSession, graph_version_id: str, finding: ReviewFinding
    ) -> None:
        """Enforce that finding claims are strictly backed by the knowledge graph.

        Raises:
            StaleFindingError: If graph_version_id is missing or obsolete.
            RepairPreconditionError: If critical evidence claims fail graph verification.
        """
        if not graph_version_id:
            raise StaleFindingError("Finding cannot be verified: missing or null graph_version_id")

        is_valid, _ = await EvidenceValidator.validate_finding_evidence(session, graph_version_id, finding)
        if not is_valid:
            raise RepairPreconditionError(
                f"Finding for symbol '{finding.symbol or finding.file}' failed evidence verification against graph"
            )

