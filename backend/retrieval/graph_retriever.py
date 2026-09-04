"""Structural context retrieval using the Knowledge Graph."""

from typing import Any, Dict, List
from sqlalchemy.ext.asyncio import AsyncSession

from backend.graph.queries import GraphQueries
from backend.observability.logging import get_logger

logger = get_logger(__name__)


class GraphRetriever:
    """Retrieves structurally connected context (callers, callees, tests, APIs)."""

    @staticmethod
    async def retrieve_for_symbols(
        session: AsyncSession, graph_version_id: str, symbol_names: List[str]
    ) -> List[Dict[str, Any]]:
        """Retrieve impact subgraphs for a list of changed symbols."""
        results = []
        seen_ids = set()

        for sym in symbol_names:
            impact = await GraphQueries.get_impact(session, graph_version_id, sym)

            for caller in impact.callers:
                if caller.id not in seen_ids:
                    seen_ids.add(caller.id)
                    results.append({
                        "id": caller.id,
                        "file_path": caller.file_path,
                        "symbol_name": caller.name,
                        "node_type": caller.node_type,
                        "relationship": "caller",
                        "related_to": sym,
                        "lines": (caller.start_line, caller.end_line),
                    })

            for callee in impact.callees:
                if callee.id not in seen_ids:
                    seen_ids.add(callee.id)
                    results.append({
                        "id": callee.id,
                        "file_path": callee.file_path,
                        "symbol_name": callee.name,
                        "node_type": callee.node_type,
                        "relationship": "callee",
                        "related_to": sym,
                        "lines": (callee.start_line, callee.end_line),
                    })

            for test in impact.tests:
                if test.id not in seen_ids:
                    seen_ids.add(test.id)
                    results.append({
                        "id": test.id,
                        "file_path": test.file_path,
                        "symbol_name": test.name,
                        "node_type": "Test",
                        "relationship": "test",
                        "related_to": sym,
                        "lines": (test.start_line, test.end_line),
                    })

        return results
