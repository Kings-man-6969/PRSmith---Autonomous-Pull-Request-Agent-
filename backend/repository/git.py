"""Git operations abstraction for cloning, diffing, and inspecting repositories."""

import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple
import git

from backend.observability.logging import get_logger

logger = get_logger(__name__)


class GitOps:
    """Provides safe Git primitives for repository analysis and manipulation."""

    @staticmethod
    def clone(repo_url: str, dest_dir: Path, token: Optional[str] = None) -> git.Repo:
        """Clone a repository to dest_dir with optional token authentication."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        auth_url = repo_url
        if token and "github.com" in repo_url:
            auth_url = repo_url.replace("https://", f"https://{token}@")

        logger.info("Cloning repository", repo_url=repo_url, dest_dir=str(dest_dir))
        return git.Repo.clone_from(auth_url, dest_dir)

    @staticmethod
    def fetch_pr(repo_path: Path, pr_number: int, head_sha: str) -> None:
        """Fetch a specific PR ref into the local repository."""
        repo = git.Repo(repo_path)
        ref_spec = f"pull/{pr_number}/head:pr-{pr_number}"
        repo.remotes.origin.fetch(ref_spec)

    @staticmethod
    def get_merge_base(repo_path: Path, base_sha: str, head_sha: str) -> str:
        """Compute the common ancestor merge-base between two commits."""
        repo = git.Repo(repo_path)
        base_commit = repo.commit(base_sha)
        head_commit = repo.commit(head_sha)
        merge_bases = repo.merge_base(base_commit, head_commit)
        if not merge_bases:
            return base_sha
        return merge_bases[0].hexsha

    @staticmethod
    def get_diff(repo_path: Path, base_sha: str, head_sha: str) -> str:
        """Get unified diff between base and head commit."""
        repo = git.Repo(repo_path)
        diff_output = repo.git.diff(f"{base_sha}...{head_sha}", unified=3)
        return str(diff_output)

    @staticmethod
    def get_changed_files(repo_path: Path, base_sha: str, head_sha: str) -> List[str]:
        """Get list of changed file paths between base and head."""
        repo = git.Repo(repo_path)
        output = repo.git.diff(f"{base_sha}...{head_sha}", name_only=True)
        if not output.strip():
            return []
        return [f.strip() for f in output.strip().splitlines() if f.strip()]

    @staticmethod
    def check_patch(worktree_path: Path, patch_diff: str) -> Tuple[bool, str]:
        """Verify if a patch applies cleanly without applying it (git apply --check)."""
        try:
            process = subprocess.run(
                ["git", "apply", "--check", "-"],
                input=patch_diff,
                cwd=str(worktree_path),
                text=True,
                capture_output=True,
                check=False,
            )
            if process.returncode == 0:
                return True, ""
            return False, process.stderr or process.stdout
        except Exception as e:
            return False, str(e)

    @staticmethod
    def apply_patch(worktree_path: Path, patch_diff: str) -> Tuple[bool, str]:
        """Apply patch to worktree (git apply)."""
        try:
            process = subprocess.run(
                ["git", "apply", "--whitespace=fix", "-"],
                input=patch_diff,
                cwd=str(worktree_path),
                text=True,
                capture_output=True,
                check=False,
            )
            if process.returncode == 0:
                return True, ""
            return False, process.stderr or process.stdout
        except Exception as e:
            return False, str(e)

    @staticmethod
    def rollback(worktree_path: Path) -> None:
        """Rollback uncommitted changes in the worktree."""
        try:
            subprocess.run(
                ["git", "checkout", "HEAD", "--", "."],
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
            )
        except Exception as e:
            logger.error("Failed to rollback worktree", error=str(e), worktree=str(worktree_path))
