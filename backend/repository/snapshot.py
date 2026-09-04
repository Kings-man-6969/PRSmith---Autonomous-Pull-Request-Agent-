"""Immutable repository snapshot data model."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class RepositorySnapshot:
    """Immutable representation of a repository state for a specific job."""

    repository_id: str
    repository_full_name: str
    base_sha: str
    head_sha: str
    merge_base_sha: str
    clone_path: Path
    created_at: datetime = field(default_factory=datetime.utcnow)
    pr_number: Optional[int] = None
    pr_title: str = ""

    def is_stale(self, current_head_sha: str) -> bool:
        """Check if this snapshot has been superseded by a newer commit."""
        return self.head_sha != current_head_sha
