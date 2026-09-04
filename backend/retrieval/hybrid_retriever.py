"""Hybrid retrieval combining Knowledge Graph traversal and semantic search."""

from pathlib import Path
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import GraphVersion, RepositorySnapshot
from backend.observability.logging import get_logger
from backend.retrieval.graph_retriever import GraphRetriever
from backend.retrieval.ranker import ContextRanker
from backend.retrieval.sanitized_projections import get_sanitized_projection
from backend.retrieval.semantic_retriever import SemanticRetriever

logger = get_logger(__name__)


class HybridRetriever:
    """Orchestrates hybrid retrieval for review and repair agents.

    Security: all file reads go through SanitizedProjection to enforce the
    mandatory secret-redaction boundary before content reaches LLM prompts.
    """

    def __init__(self, semantic_retriever: Optional[SemanticRetriever] = None):
        self.semantic = semantic_retriever or SemanticRetriever()
        self.graph = GraphRetriever()
        self.ranker = ContextRanker()
        self._projection = get_sanitized_projection()

    async def retrieve_context(
        self,
        session: AsyncSession,
        snapshot: RepositorySnapshot,
        graph_version_id: str,
        changed_symbols: List[str],
        query: str,
        pr_diff: str,
    ) -> List[Dict[str, Any]]:
        """Retrieve priority-ranked context combining diff, graph relationships, and semantic search."""
        candidates: List[Dict[str, Any]] = []

        # 1. Add PR Diff (Highest Priority)
        candidates.append({
            "category": "pr_diff",
            "file_path": "PR_DIFF",
            "content": pr_diff,
        })

        # 2. Add Knowledge Graph Context
        graph_items = await self.graph.retrieve_for_symbols(
            session, graph_version_id, changed_symbols
        )
        repo_root = Path(snapshot.clone_path)

        for item in graph_items:
            file_rel = item.get("file_path", "")
            lines = item.get("lines")

            # SECURITY: use SanitizedProjection — never raw open()
            if file_rel and lines and lines[0] and lines[1]:
                file_content = self._projection.read_lines_sanitized(
                    repo_root, file_rel, lines[0], lines[1]
                )
            else:
                file_content = f"# Symbol: {item.get('symbol_name')} in {file_rel}"

            candidates.append({
                "category": item.get("relationship", "caller"),
                "file_path": file_rel,
                "symbol_name": item.get("symbol_name"),
                "content": file_content,
            })

        # 3. Add Semantic Vector Search Context
        if query:
            semantic_items = await self.semantic.search(
                session, snapshot.repository_id, snapshot.head_sha, query, top_k=5
            )
            for s_item in semantic_items:
                candidates.append({
                    "category": "semantic",
                    "file_path": s_item["file_path"],
                    "symbol_name": s_item.get("symbol_name"),
                    "content": s_item["chunk_text"],
                })

        # Rank and pack within token budget
        return self.ranker.rank_and_truncate(candidates)
