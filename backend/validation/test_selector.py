"""Test selector using Knowledge Graph impact sets."""

from typing import List, Set
from sqlalchemy.ext.asyncio import AsyncSession

from backend.graph.queries import GraphQueries
from backend.observability.logging import get_logger

logger = get_logger(__name__)


class TestSelector:
    """Discovers targeted and integration tests covering changed symbols."""

    @staticmethod
    async def select_tests_for_symbols(
        session: AsyncSession, graph_version_id: str, symbol_names: List[str]
    ) -> List[str]:
        """Find test files and test functions related to changed symbols."""
        selected_tests: Set[str] = set()

        for sym in symbol_names:
            impact = await GraphQueries.get_impact(session, graph_version_id, sym)
            for test_node in impact.tests:
                if test_node.file_path:
                    # Format as pytest target: path/to/test.py::test_func or path/to/test.py
                    target = test_node.file_path
                    if test_node.name and test_node.name.startswith("test_"):
                        target = f"{test_node.file_path}::{test_node.name}"
                    selected_tests.add(target)

        logger.info(
            "Selected targeted tests via Knowledge Graph",
            symbols=symbol_names,
            count=len(selected_tests),
        )
        return list(selected_tests)
