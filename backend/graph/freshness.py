"""Freshness and drift verification for the Repository Knowledge Graph."""

from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import GraphNode, GraphVersion
from backend.observability.logging import get_logger

logger = get_logger(__name__)


class GraphFreshnessChecker:
    """Checks if a graph version is stale or drifted compared to current commit."""

    @staticmethod
    async def is_stale(
        session: AsyncSession, graph_version_id: str, current_head_sha: str, max_age_hours: int = 24
    ) -> bool:
        stmt = select(GraphVersion).where(GraphVersion.id == graph_version_id)
        res = await session.execute(stmt)
        gv = res.scalars().first()
        if not gv:
            return True

        if gv.commit_sha != current_head_sha:
            return True

        age = datetime.utcnow() - gv.last_verified
        if age > timedelta(hours=max_age_hours):
            return True

        return False

    @staticmethod
    async def needs_full_rebuild(
        session: AsyncSession, repository_id: str, threshold_incremental_generations: int = 10
    ) -> bool:
        """Determines if incremental updates have accumulated enough drift to require a full rebuild."""
        stmt = (
            select(func.count(GraphVersion.id))
            .where(GraphVersion.repository_id == repository_id)
            .where(GraphVersion.is_incremental == True)
        )
        res = await session.execute(stmt)
        incremental_count = res.scalar() or 0
        return incremental_count >= threshold_incremental_generations
