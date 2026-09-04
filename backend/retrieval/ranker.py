"""Context ranker enforcing prioritization and strict token budgets."""

from typing import Any, Dict, List
from backend.config import settings
from backend.observability.logging import get_logger

logger = get_logger(__name__)

PRIORITY_MAP = {
    "pr_diff": 1,
    "changed_symbol": 2,
    "caller": 3,
    "callee": 4,
    "test": 5,
    "interface": 6,
    "api_contract": 7,
    "db_interaction": 8,
    "configuration": 9,
    "semantic": 10,
    "documentation": 11,
}


class ContextRanker:
    """Ranks and truncates retrieved context to fit comfortably in LLM token budgets."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token estimator (~4 characters per token)."""
        return max(1, len(text) // 4)

    @classmethod
    def rank_and_truncate(
        cls,
        context_items: List[Dict[str, Any]],
        max_tokens: int = settings.MAX_TOKEN_BUDGET,
    ) -> List[Dict[str, Any]]:
        """Sort context items by priority and pack within token budget."""
        # Sort by priority rank
        sorted_items = sorted(
            context_items,
            key=lambda x: PRIORITY_MAP.get(x.get("category", "semantic"), 99),
        )

        packed = []
        current_tokens = 0

        for item in sorted_items:
            content = item.get("content", "")
            item_tokens = cls.estimate_tokens(content)

            if current_tokens + item_tokens <= max_tokens:
                packed.append(item)
                current_tokens += item_tokens
            else:
                # If item is high-priority, try truncated slice
                remaining = max_tokens - current_tokens
                if remaining > 100:
                    truncated_content = content[: remaining * 4]
                    item["content"] = truncated_content + "\n...[TRUNCATED]"
                    packed.append(item)
                    current_tokens += remaining
                break

        logger.info(
            "Ranked and packed context",
            total_items=len(context_items),
            packed_items=len(packed),
            total_tokens=current_tokens,
            max_tokens=max_tokens,
        )
        return packed
