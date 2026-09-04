"""Incremental Knowledge Graph updater for new commits."""

import os
import uuid
from pathlib import Path
from typing import List, Optional
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import GraphEdge, GraphNode, GraphVersion, RepositorySnapshot
from backend.languages.interface import LanguageAnalyzer
from backend.languages.python_analyzer import PythonAnalyzer
from backend.observability.logging import get_logger
from backend.repository.git import GitOps

logger = get_logger(__name__)


class GraphUpdater:
    """Incrementally updates Knowledge Graph nodes/edges for modified files."""

    def __init__(self, analyzers: Optional[List[LanguageAnalyzer]] = None):
        self.analyzers = analyzers or [PythonAnalyzer()]

    def _get_analyzer_for_file(self, file_path: str) -> Optional[LanguageAnalyzer]:
        for analyzer in self.analyzers:
            if analyzer.supports(file_path):
                return analyzer
        return None

    async def update_incremental(
        self,
        session: AsyncSession,
        base_graph_version: GraphVersion,
        new_snapshot: RepositorySnapshot,
        changed_files: List[str],
    ) -> GraphVersion:
        """Create a new graph version with incrementally updated nodes for changed files."""
        logger.info(
            "Incrementally updating knowledge graph",
            base_graph_id=base_graph_version.id,
            new_commit=new_snapshot.head_sha,
            changed_files_count=len(changed_files),
        )

        new_graph_version = GraphVersion(
            repository_id=new_snapshot.repository_id,
            snapshot_id=new_snapshot.id,
            commit_sha=new_snapshot.head_sha,
            is_incremental=True,
        )
        session.add(new_graph_version)
        await session.flush()

        repo_root = Path(new_snapshot.clone_path)

        # 1. Copy unaffected nodes from base graph
        stmt = (
            select(GraphNode)
            .where(GraphNode.graph_version_id == base_graph_version.id)
            .where(~GraphNode.file_path.in_(changed_files))
        )
        res = await session.execute(stmt)
        unaffected_nodes = res.scalars().all()

        id_mapping = {}
        for old_node in unaffected_nodes:
            new_id = str(uuid.uuid4())
            id_mapping[old_node.id] = new_id
            copied_node = GraphNode(
                id=new_id,
                graph_version_id=new_graph_version.id,
                repository_id=new_snapshot.repository_id,
                node_type=old_node.node_type,
                name=old_node.name,
                qualified_name=old_node.qualified_name,
                file_path=old_node.file_path,
                start_line=old_node.start_line,
                end_line=old_node.end_line,
                source_commit=old_node.source_commit,
                parser_version=old_node.parser_version,
                confidence=old_node.confidence,
                properties=old_node.properties,
            )
            session.add(copied_node)

        # 2. Parse changed files
        new_nodes: List[GraphNode] = []
        name_to_node = {}

        for file_rel in changed_files:
            file_path = repo_root / file_rel
            if not file_path.exists():
                continue  # File was deleted

            analyzer = self._get_analyzer_for_file(file_rel)
            if not analyzer:
                continue

            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception as e:
                logger.warning("Could not read changed file", file=file_rel, error=str(e))
                continue

            symbols, relations = analyzer.parse_file(file_rel, content)
            for sym in symbols:
                node_id = str(uuid.uuid4())
                node = GraphNode(
                    id=node_id,
                    graph_version_id=new_graph_version.id,
                    repository_id=new_snapshot.repository_id,
                    node_type=sym.symbol_type,
                    name=sym.name,
                    qualified_name=sym.qualified_name,
                    file_path=sym.file_path,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    source_commit=new_snapshot.head_sha,
                    parser_version="python_ast",
                    confidence=1.0,
                    properties=sym.properties,
                )
                session.add(node)
                new_nodes.append(node)
                name_to_node[sym.qualified_name] = node

        await session.commit()
        logger.info(
            "Incremental graph update complete",
            new_graph_id=new_graph_version.id,
            reused_nodes=len(unaffected_nodes),
            new_nodes=len(new_nodes),
        )
        return new_graph_version
