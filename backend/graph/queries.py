"""Query API for traversing and interrogating the Knowledge Graph."""

from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.database.models import GraphEdge, GraphNode, GraphVersion
from backend.graph.models import ImpactSet, NodeModel


class GraphQueries:
    """Provides high-level graph query and traversal methods."""

    @staticmethod
    def _to_model(node: GraphNode) -> NodeModel:
        return NodeModel(
            id=node.id,
            repository_id=node.repository_id,
            node_type=node.node_type,
            name=node.name,
            qualified_name=node.qualified_name,
            file_path=node.file_path,
            start_line=node.start_line,
            end_line=node.end_line,
            source_commit=node.source_commit,
            parser_version=node.parser_version,
            confidence=node.confidence,
            properties=node.properties or {},
            created_at=node.created_at,
        )

    @classmethod
    async def get_node_by_symbol(
        cls, session: AsyncSession, graph_version_id: str, symbol_name: str
    ) -> Optional[NodeModel]:
        """Find a node by exact name or qualified name."""
        stmt = (
            select(GraphNode)
            .where(GraphNode.graph_version_id == graph_version_id)
            .where(
                (GraphNode.qualified_name == symbol_name)
                | (GraphNode.name == symbol_name)
            )
        )
        res = await session.execute(stmt)
        node = res.scalars().first()
        return cls._to_model(node) if node else None

    @classmethod
    async def get_callers(
        cls, session: AsyncSession, graph_version_id: str, node_id: str
    ) -> List[NodeModel]:
        """Find all functions/methods that call the target node."""
        stmt = (
            select(GraphNode)
            .join(GraphEdge, GraphEdge.source_node_id == GraphNode.id)
            .where(GraphEdge.graph_version_id == graph_version_id)
            .where(GraphEdge.target_node_id == node_id)
            .where(GraphEdge.relationship_type.in_(["calls", "reads_db", "writes_db"]))
        )
        res = await session.execute(stmt)
        return [cls._to_model(n) for n in res.scalars().all()]

    @classmethod
    async def get_callees(
        cls, session: AsyncSession, graph_version_id: str, node_id: str
    ) -> List[NodeModel]:
        """Find all functions/methods called by the target node."""
        stmt = (
            select(GraphNode)
            .join(GraphEdge, GraphEdge.target_node_id == GraphNode.id)
            .where(GraphEdge.graph_version_id == graph_version_id)
            .where(GraphEdge.source_node_id == node_id)
            .where(GraphEdge.relationship_type.in_(["calls", "reads_db", "writes_db"]))
        )
        res = await session.execute(stmt)
        return [cls._to_model(n) for n in res.scalars().all()]

    @classmethod
    async def get_tests(
        cls, session: AsyncSession, graph_version_id: str, node_id: str
    ) -> List[NodeModel]:
        """Find test nodes covering or referencing the target node."""
        stmt = (
            select(GraphNode)
            .join(GraphEdge, GraphEdge.source_node_id == GraphNode.id)
            .where(GraphEdge.graph_version_id == graph_version_id)
            .where(GraphEdge.target_node_id == node_id)
            .where(GraphEdge.relationship_type == "tests")
        )
        res = await session.execute(stmt)
        return [cls._to_model(n) for n in res.scalars().all()]

    @classmethod
    async def get_impact(
        cls, session: AsyncSession, graph_version_id: str, symbol_name: str
    ) -> ImpactSet:
        """Compute complete impact subgraph for a changed symbol."""
        node = await cls.get_node_by_symbol(session, graph_version_id, symbol_name)
        if not node:
            return ImpactSet(target_symbol=symbol_name)

        callers = await cls.get_callers(session, graph_version_id, node.id)
        callees = await cls.get_callees(session, graph_version_id, node.id)
        tests = await cls.get_tests(session, graph_version_id, node.id)

        # Also get tests covering callers
        for caller in callers:
            caller_tests = await cls.get_tests(session, graph_version_id, caller.id)
            for ct in caller_tests:
                if ct.id not in [t.id for t in tests]:
                    tests.append(ct)

        api_endpoints = [n for n in callers if n.node_type == "APIEndpoint"]
        db_nodes = [n for n in callees if "db" in n.node_type.lower() or "repository" in n.file_path.lower()]

        return ImpactSet(
            target_symbol=symbol_name,
            callers=callers,
            callees=callees,
            tests=tests,
            api_endpoints=api_endpoints,
            db_interactions=db_nodes,
        )

    @classmethod
    async def check_claim_support(
        cls,
        session: AsyncSession,
        graph_version_id: str,
        source_symbol: str,
        target_symbol: str,
        expected_relationship: str = "calls",
    ) -> bool:
        """Hallucination gate: verify if a claimed relationship exists in the knowledge graph."""
        stmt = (
            select(GraphEdge)
            .join(GraphNode, GraphEdge.source_node_id == GraphNode.id)
            .where(GraphEdge.graph_version_id == graph_version_id)
            .where(
                (GraphNode.name == source_symbol)
                | (GraphNode.qualified_name == source_symbol)
            )
            .where(GraphEdge.relationship_type == expected_relationship)
        )
        res = await session.execute(stmt)
        edges = res.scalars().all()
        return len(edges) > 0
