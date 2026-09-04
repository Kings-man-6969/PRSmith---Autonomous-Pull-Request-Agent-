"""FK-enforced Knowledge Graph Garbage Collection safely pruning unreferenced graphs."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import GraphVersion, Job
from backend.observability.logging import get_logger

logger = get_logger(__name__)

ACTIVE_JOB_STATUSES = [
    "PENDING",
    "CLONING",
    "ANALYZING",
    "REVIEWING",
    "REPAIRING",
    "VALIDATING",
    "PUBLISHING",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GraphGarbageCollector:
    """Safely collects unreferenced or stale GraphVersion rows without breaking active pipelines."""

    @staticmethod
    async def get_prunable_graph_versions(
        session: AsyncSession,
        repository_id: Optional[str] = None,
        retention_hours: int = 24,
    ) -> List[GraphVersion]:
        """Find graph versions that have NO active jobs referencing them and exceed retention."""
        cutoff = utcnow() - timedelta(hours=retention_hours)

        # Subquery: check if any active job holds this graph_version_id
        active_job_ref = select(1).where(
            Job.graph_version_id == GraphVersion.id,
            Job.status.in_(ACTIVE_JOB_STATUSES),
        )

        query = (
            select(GraphVersion)
            .where(
                ~exists(active_job_ref),
                GraphVersion.created_at < cutoff,
            )
        )
        if repository_id:
            query = query.where(GraphVersion.repository_id == repository_id)

        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def prune_stale_graphs(
        session: AsyncSession,
        repository_id: Optional[str] = None,
        retention_hours: int = 24,
    ) -> int:
        """Prune unreferenced graph versions, cascading deletes to nodes and edges.

        Returns number of pruned GraphVersion records.
        """
        prunable = await GraphGarbageCollector.get_prunable_graph_versions(
            session=session,
            repository_id=repository_id,
            retention_hours=retention_hours,
        )

        if not prunable:
            return 0

        prunable_ids = [gv.id for gv in prunable]
        stmt = delete(GraphVersion).where(GraphVersion.id.in_(prunable_ids))
        res = await session.execute(stmt)
        await session.commit()

        pruned_count = res.rowcount or len(prunable_ids)
        logger.info(
            "Pruned unreferenced graph versions",
            pruned_count=pruned_count,
            repository_id=repository_id,
        )
        return pruned_count
