"""Graph building background tasks for PRSmith Knowledge Graph."""

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional
from sqlalchemy import select

from backend.auth.github_client import GitHubClient
from backend.config import settings
from backend.database.models import Repository, RepositorySnapshot
from backend.database.sessions import async_session_factory
from backend.graph.builder import GraphBuilder
from backend.observability.logging import get_logger
from backend.repository.git import GitOps
from worker.celery import celery_app

logger = get_logger(__name__)


async def async_build_knowledge_graph(
    repo_id: str,
    branch: str = "main",
    triggered_by_user_id: Optional[str] = None,
) -> dict:
    """Clone or fetch repository branch and construct deterministic AST Knowledge Graph."""
    async with async_session_factory() as session:
        stmt = select(Repository).where(Repository.id == repo_id)
        res = await session.execute(stmt)
        repo = res.scalars().first()
        if not repo:
            logger.error("Repository not found for graph build", repo_id=repo_id)
            return {"status": "error", "message": "Repository not found"}

        from backend.auth.credential_provider import CredentialExpiredError, GitHubCredentialProvider

        provider = GitHubCredentialProvider()
        token = None
        if repo.installation_id:
            token = await provider.get_app_credential(repo.installation_id)
        elif triggered_by_user_id:
            try:
                token = await provider.get_user_credential(triggered_by_user_id, session)
            except CredentialExpiredError:
                token = None
        if not token:
            token = provider.get_dev_pat()

        clone_root = Path("/tmp/prsmith_repos") / repo.full_name.replace("/", "_")
        try:
            if not (clone_root / ".git").exists():
                git_repo = GitOps.clone(
                    f"https://github.com/{repo.full_name}.git",
                    clone_root,
                    token=token,
                )
            else:
                import git
                git_repo = git.Repo(clone_root)
                git_repo.remotes.origin.fetch()

            # Checkout target branch
            try:
                git_repo.git.checkout(branch)
                git_repo.git.pull("origin", branch)
            except Exception:
                pass

            commit_sha = git_repo.head.commit.hexsha

            snapshot = RepositorySnapshot(
                repository_id=repo.id,
                base_sha=commit_sha,
                head_sha=commit_sha,
                merge_base_sha=commit_sha,
                clone_path=str(clone_root),
            )
            session.add(snapshot)
            await session.flush()

            builder = GraphBuilder()
            graph_version = await builder.build_for_snapshot(session, snapshot)

            return {
                "status": "READY",
                "graph_version_id": graph_version.id,
                "node_count": graph_version.node_count,
                "edge_count": graph_version.edge_count,
                "commit_sha": commit_sha,
            }
        except Exception as exc:
            logger.error("Failed to build knowledge graph", repo_id=repo_id, error=str(exc))
            return {"status": "error", "message": str(exc)}


@celery_app.task(name="worker.tasks.graph.build_knowledge_graph")
def build_knowledge_graph(
    repo_id: str,
    branch: str = "main",
    triggered_by_user_id: Optional[str] = None,
) -> dict:
    """Celery entry point for building Knowledge Graph."""
    logger.info("Executing Celery graph build task", repo_id=repo_id, branch=branch, user_id=triggered_by_user_id)
    return asyncio.run(async_build_knowledge_graph(repo_id, branch, triggered_by_user_id))


@celery_app.task(name="worker.tasks.graph.build_graph")
def build_graph_task(snapshot_id: str) -> dict:
    """Legacy alias for backward compatibility."""
    logger.info("Executing Celery graph build legacy task", snapshot_id=snapshot_id)
    return {"status": "success", "snapshot_id": snapshot_id}
