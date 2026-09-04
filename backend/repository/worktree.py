"""Isolated git worktree manager to prevent mutating the base repository."""

import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.observability.logging import get_logger

logger = get_logger(__name__)


class WorktreeManager:
    """Manages creation, lifecycle, and cleanup of isolated Git worktrees."""

    def __init__(self, base_repo_path: Path, worktree_base_dir: Optional[Path] = None):
        self.base_repo_path = base_repo_path
        self.worktree_base_dir = worktree_base_dir or settings.WORKTREE_BASE_DIR
        self.worktree_base_dir.mkdir(parents=True, exist_ok=True)

    def create_worktree(self, commit_sha: str) -> Path:
        """Create a new detached worktree at the specified commit."""
        unique_id = uuid.uuid4().hex[:12]
        worktree_path = self.worktree_base_dir / f"wt_{unique_id}"

        logger.info(
            "Creating isolated git worktree",
            base_repo=str(self.base_repo_path),
            commit_sha=commit_sha,
            worktree_path=str(worktree_path),
        )

        try:
            subprocess.run(
                [
                    "git",
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree_path),
                    commit_sha,
                ],
                cwd=str(self.base_repo_path),
                check=True,
                capture_output=True,
                text=True,
            )
            return worktree_path
        except subprocess.CalledProcessError as e:
            logger.error("Failed to create git worktree", error=e.stderr)
            raise RuntimeError(f"Could not create git worktree: {e.stderr}") from e

    def remove_worktree(self, worktree_path: Path) -> None:
        """Remove the isolated worktree and prune Git metadata."""
        if not worktree_path.exists():
            return

        logger.info("Cleaning up git worktree", worktree_path=str(worktree_path))
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_path)],
                cwd=str(self.base_repo_path),
                check=False,
                capture_output=True,
            )
        except Exception as e:
            logger.warning("git worktree remove failed, removing folder directly", error=str(e))

        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)

        try:
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(self.base_repo_path),
                check=False,
                capture_output=True,
            )
        except Exception:
            pass


@contextmanager
def isolated_worktree(base_repo_path: Path, commit_sha: str) -> Iterator[Path]:
    """Context manager for creating and automatically cleaning up an isolated worktree."""
    manager = WorktreeManager(base_repo_path)
    wt_path = manager.create_worktree(commit_sha)
    try:
        yield wt_path
    finally:
        manager.remove_worktree(wt_path)
