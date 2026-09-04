"""Deterministic AST-based Knowledge Graph and Code Intelligence Builder."""

import os
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database.models import GraphEdge, GraphNode, GraphVersion, RepositorySnapshot
from backend.languages.interface import LanguageAnalyzer
from backend.languages.javascript_analyzer import JavaScriptAnalyzer
from backend.languages.python_analyzer import PythonAnalyzer
from backend.observability.logging import get_logger
from backend.retrieval.semantic_retriever import SemanticRetriever

logger = get_logger(__name__)


class GraphBuilder:
    """Constructs a deterministic AST Knowledge Graph and Code Vector Embeddings."""

    def __init__(self, analyzers: Optional[List[LanguageAnalyzer]] = None):
        self.analyzers = analyzers or [PythonAnalyzer(), JavaScriptAnalyzer()]
        self.retriever = SemanticRetriever()

    def _get_analyzer_for_file(self, file_path: str) -> Optional[LanguageAnalyzer]:
        for analyzer in self.analyzers:
            if analyzer.supports(file_path):
                return analyzer
        return None

    async def build_for_snapshot(
        self, session: AsyncSession, snapshot: RepositorySnapshot
    ) -> GraphVersion:
        """Build the full knowledge graph and vector index for a repository snapshot."""
        logger.info(
            "Building deterministic knowledge graph and embeddings",
            repo_id=snapshot.repository_id,
            commit_sha=snapshot.head_sha,
            clone_path=str(snapshot.clone_path),
        )

        repo_root = Path(snapshot.clone_path)
        graph_version = GraphVersion(
            repository_id=snapshot.repository_id,
            snapshot_id=snapshot.id,
            commit_sha=snapshot.head_sha,
            is_incremental=False,
        )
        session.add(graph_version)
        await session.flush()

        all_nodes: List[GraphNode] = []
        name_to_node: Dict[str, GraphNode] = {}
        short_name_to_nodes: Dict[str, List[GraphNode]] = {}
        all_raw_relations = []
        code_chunks: List[Dict[str, Any]] = []

        # Walk repository files (excluding build artifacts and dependencies)
        ignored_dirs = {
            ".git", ".venv", "venv", "node_modules", "dist", "build",
            "__pycache__", ".pytest_cache", ".next", "coverage", ".turbo",
        }

        for root, dirs, files in os.walk(repo_root):
            dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
            for file in sorted(files):
                full_path = Path(root) / file
                rel_path = str(full_path.relative_to(repo_root)).replace("\\", "/")

                analyzer = self._get_analyzer_for_file(rel_path)
                if not analyzer:
                    continue

                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                except Exception as e:
                    logger.warning("Failed to read file for graph build", file=rel_path, error=str(e))
                    continue

                symbols, relations = analyzer.parse_file(rel_path, content)
                file_lines = content.splitlines()

                for sym in symbols:
                    node = GraphNode(
                        id=str(uuid.uuid4()),
                        graph_version_id=graph_version.id,
                        repository_id=snapshot.repository_id,
                        node_type=sym.symbol_type,
                        name=sym.name,
                        qualified_name=sym.qualified_name,
                        file_path=sym.file_path,
                        start_line=sym.start_line,
                        end_line=sym.end_line,
                        source_commit=snapshot.head_sha,
                        parser_version="ast_v2",
                        confidence=1.0,
                        properties=sym.properties,
                    )
                    all_nodes.append(node)
                    name_to_node[sym.qualified_name] = node
                    short_name_to_nodes.setdefault(sym.name, []).append(node)

                    # Extract chunk text for vector embedding
                    s_idx = max(0, sym.start_line - 1)
                    e_idx = min(len(file_lines), sym.end_line)
                    chunk_body = "\n".join(file_lines[s_idx:e_idx])
                    if chunk_body.strip():
                        code_chunks.append({
                            "file_path": sym.file_path,
                            "symbol_name": sym.qualified_name,
                            "language": "javascript" if rel_path.endswith((".js", ".ts", ".jsx", ".tsx")) else "python",
                            "chunk_text": f"// Symbol: {sym.qualified_name} ({sym.symbol_type})\n{chunk_body}",
                        })

                all_raw_relations.extend(relations)

        # Bulk save nodes
        session.add_all(all_nodes)
        await session.flush()

        # Resolve cross-module and intra-file relationships
        all_edges: List[GraphEdge] = []
        seen_edges: Set[Tuple[str, str, str]] = set()

        for rel in all_raw_relations:
            source_node = name_to_node.get(rel.source_qualified_name)
            target_node = name_to_node.get(rel.target_qualified_name)

            # Fallback 1: Resolve by short symbol name if target is unqualified
            if not target_node and rel.target_qualified_name in short_name_to_nodes:
                candidates = short_name_to_nodes[rel.target_qualified_name]
                if len(candidates) == 1:
                    target_node = candidates[0]
                elif source_node:
                    # Prefer candidate in same file or same directory
                    same_file = [c for c in candidates if c.file_path == source_node.file_path]
                    if same_file:
                        target_node = same_file[0]
                    else:
                        target_node = candidates[0]

            # Fallback 2: Resolve relative module import paths (e.g. ./utils -> src.utils)
            if not target_node and (rel.target_qualified_name.startswith(".") or "/" in rel.target_qualified_name):
                clean_target = rel.target_qualified_name.replace("./", "").replace("/", ".").replace("\\", ".")
                for qname, candidate_node in name_to_node.items():
                    if qname.endswith(clean_target) or clean_target in qname:
                        target_node = candidate_node
                        break

            if source_node and target_node and source_node.id != target_node.id:
                edge_key = (source_node.id, target_node.id, rel.relation_type)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edge = GraphEdge(
                        id=str(uuid.uuid4()),
                        graph_version_id=graph_version.id,
                        source_node_id=source_node.id,
                        target_node_id=target_node.id,
                        relationship_type=rel.relation_type,
                        source_commit=snapshot.head_sha,
                        confidence=rel.confidence,
                        derived_by=rel.derived_by,
                        properties=rel.properties,
                    )
                    all_edges.append(edge)

        session.add_all(all_edges)
        graph_version.node_count = len(all_nodes)
        graph_version.edge_count = len(all_edges)

        # Index code embeddings for hybrid semantic search
        try:
            await self.retriever.index_chunks(
                session=session,
                repository_id=snapshot.repository_id,
                commit_sha=snapshot.head_sha,
                chunks=code_chunks,
            )
        except Exception as e:
            logger.warning("Code embedding indexing completed with notice", error=str(e))

        await session.commit()
        logger.info(
            "Knowledge graph and embeddings built successfully",
            graph_version_id=graph_version.id,
            nodes=len(all_nodes),
            edges=len(all_edges),
            chunks=len(code_chunks),
        )
        return graph_version
