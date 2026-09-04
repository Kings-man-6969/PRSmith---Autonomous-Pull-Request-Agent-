"""Applies and rolls back patches with incremental checkpoint tracking."""

from pathlib import Path
from typing import Tuple
from backend.observability.logging import get_logger, log_event
from backend.repository.git import GitOps

logger = get_logger(__name__)


class WorktreePatcher:
    """Manages patch application, checkpointing, and clean rollbacks."""

    @staticmethod
    def apply_patch_checkpoint(worktree_path: Path, patch_diff: str) -> Tuple[bool, str]:
        """Verify and apply patch to the worktree."""
        # 1. Dry-run verification
        ok, err = GitOps.check_patch(worktree_path, patch_diff)
        if not ok:
            log_event("patch.rejected", reason="git_apply_check_failed", error=err)
            return False, f"git apply --check failed: {err}"

        # 2. Apply to worktree
        applied_ok, apply_err = GitOps.apply_patch(worktree_path, patch_diff)
        if not applied_ok:
            log_event("patch.rejected", reason="git_apply_failed", error=apply_err)
            return False, f"git apply failed: {apply_err}"

        log_event("patch.applied", worktree=str(worktree_path))
        return True, ""

    @staticmethod
    def rollback_patch(worktree_path: Path) -> None:
        """Roll back failed patch state to ensure no contamination of subsequent iterations."""
        logger.info("Rolling back worktree to last known clean state", worktree=str(worktree_path))
        GitOps.rollback(worktree_path)
