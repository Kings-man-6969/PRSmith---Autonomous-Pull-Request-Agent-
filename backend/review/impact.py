"""Impact analysis linking reviewer findings to affected repository entities."""

from typing import List
from sqlalchemy.ext.asyncio import AsyncSession

from backend.graph.queries import GraphQueries
from backend.review.schemas import ReviewFinding


class ImpactAnalyzer:
    """Computes full downstream impact for detected issues."""

    @staticmethod
    async def enrich_finding_impact(
        session: AsyncSession, graph_version_id: str, finding: ReviewFinding
    ) -> ReviewFinding:
        """Populate affected_entities on the finding via Knowledge Graph queries."""
        if not finding.symbol:
            return finding

        impact = await GraphQueries.get_impact(session, graph_version_id, finding.symbol)
        affected = []

        for caller in impact.callers:
            affected.append(f"caller:{caller.qualified_name}")
        for test in impact.tests:
            affected.append(f"test:{test.qualified_name}")
        for api in impact.api_endpoints:
            affected.append(f"api:{api.qualified_name}")

        finding.affected_entities = list(set(affected))
        return finding
