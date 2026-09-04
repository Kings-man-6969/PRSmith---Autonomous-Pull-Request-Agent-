"""REST API endpoints for repository management, discovery, and Knowledge Graph."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.credential_provider import CredentialExpiredError, GitHubCredentialProvider
from backend.auth.github_client import GitHubClient
from backend.auth.security import get_current_user, get_current_user_optional
from backend.config import settings
from backend.database.models import GraphEdge, GraphNode, GraphVersion, Job, Repository, User, UserRepository
from backend.database.sessions import get_db_session

router = APIRouter(prefix="/repositories", tags=["Repositories"])

_gh = GitHubClient()
_cred_provider = GitHubCredentialProvider()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _user_repo_dict(r: Repository, ur: UserRepository) -> Dict[str, Any]:
    return {
        "id": r.id,
        "full_name": r.full_name,
        "owner": r.owner,
        "name": r.name,
        "default_branch": r.default_branch,
        "private": r.private,
        "monitoring_enabled": ur.monitoring_enabled,
        "auto_repair_enabled": ur.auto_repair_enabled,
        "watched_branches": ur.watched_branches or [],
        "created_at": ur.created_at.isoformat() if ur.created_at else None,
    }


async def _get_user_repo_or_403(
    repo_id: str,
    user: User,
    session: AsyncSession,
) -> tuple[Repository, UserRepository]:
    """Verify tenant authorization: repository must be tracked by user via UserRepository."""
    stmt = (
        select(Repository, UserRepository)
        .join(UserRepository, UserRepository.repository_id == Repository.id)
        .where(
            Repository.id == repo_id,
            UserRepository.user_id == user.id,
        )
    )
    res = await session.execute(stmt)
    row = res.first()
    if not row:
        repo_exists = (await session.execute(select(Repository.id).where(Repository.id == repo_id))).scalars().first()
        if not repo_exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: You do not have access to this repository")
    return row[0], row[1]


# ── Pydantic request bodies ───────────────────────────────────────────────────

class RegisterRepoRequest(BaseModel):
    full_name: str  # e.g. "Kings-man-6969/PRSmith"

class MonitoringRequest(BaseModel):
    enabled: bool

class AutoRepairRequest(BaseModel):
    enabled: bool

class BranchesRequest(BaseModel):
    branches: List[str]

class GraphBuildRequest(BaseModel):
    branch: str = "main"

class DispatchReviewRequest(BaseModel):
    pr_number: int


# ── List tracked repos ────────────────────────────────────────────────────────

@router.get("", response_model=List[Dict[str, Any]])
async def list_repositories(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """List all repositories tracked by the authenticated user."""
    stmt = (
        select(Repository, UserRepository)
        .join(UserRepository, UserRepository.repository_id == Repository.id)
        .where(UserRepository.user_id == current_user.id)
        .order_by(Repository.full_name)
    )
    res = await session.execute(stmt)
    return [_user_repo_dict(r, ur) for r, ur in res.all()]


# ── Discover repos from GitHub account ───────────────────────────────────────

@router.get("/discover", response_model=List[Dict[str, Any]])
async def discover_github_repos(
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """Fetch repositories accessible to the user via OAuth token, or fallback to dev PAT if enabled."""
    token: Optional[str] = None
    if current_user:
        try:
            token = await _cred_provider.get_user_credential(current_user.id, session)
        except CredentialExpiredError as err:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(err)) from err

    if not token:
        token = _cred_provider.get_dev_pat()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to discover repositories",
        )

    try:
        # 1. Fetch repositories accessible to the authenticated token
        repos = await _gh.list_user_repos(token)
        seen_ids = {r["id"] for r in repos if "id" in r}

        # 2. Fetch explicit organization repositories for complete discovery
        headers = _gh._pat_headers(token)
        async with httpx.AsyncClient(timeout=10) as client:
            orgs_res = await client.get("https://api.github.com/user/orgs", headers=headers)
            if orgs_res.status_code == 200:
                for org in orgs_res.json():
                    org_login = org.get("login")
                    if not org_login:
                        continue
                    org_repos_res = await client.get(
                        f"https://api.github.com/orgs/{org_login}/repos?per_page=100",
                        headers=headers,
                    )
                    if org_repos_res.status_code == 200:
                        for orp in org_repos_res.json():
                            if orp.get("id") not in seen_ids:
                                seen_ids.add(orp["id"])
                                repos.append({
                                    "id": orp["id"],
                                    "full_name": orp["full_name"],
                                    "owner": orp.get("owner", {}).get("login", ""),
                                    "name": orp.get("name", ""),
                                    "default_branch": orp.get("default_branch", "main"),
                                    "private": orp.get("private", False),
                                })
        return repos
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}") from exc


# ── Register a repo ───────────────────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_repository(
    body: RegisterRepoRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Register a GitHub repository for tracking under the authenticated user."""
    full_name = body.full_name.strip()
    parts = full_name.split("/", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="full_name must be 'owner/repo'")

    owner, name = parts

    # Obtain credential for metadata lookup
    token: Optional[str] = None
    try:
        token = await _cred_provider.get_user_credential(current_user.id, session)
    except CredentialExpiredError:
        pass

    if not token:
        token = _cred_provider.get_dev_pat()

    if not token:
        raise HTTPException(status_code=401, detail="Valid GitHub credential required to register repository")

    # Fetch canonical repo metadata from GitHub
    try:
        headers = _gh._pat_headers(token)
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                f"https://api.github.com/repos/{owner}/{name}",
                headers=headers,
            )
            res.raise_for_status()
            meta = res.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error retrieving repo metadata: {exc}") from exc

    github_id = meta["id"]
    default_branch = meta.get("default_branch", "main")
    is_private = meta.get("private", False)

    # 1. Upsert canonical Repository record by github_id UNIQUE
    repo_stmt = select(Repository).where(Repository.github_id == github_id)
    repo = (await session.execute(repo_stmt)).scalars().first()
    if not repo:
        repo = Repository(
            github_id=github_id,
            full_name=full_name,
            owner=owner,
            name=name,
            default_branch=default_branch,
            private=is_private,
        )
        session.add(repo)
        await session.flush()
    else:
        # Update metadata
        repo.full_name = full_name
        repo.owner = owner
        repo.name = name
        repo.default_branch = default_branch
        repo.private = is_private

    # 2. Upsert UserRepository association record (idempotent per user)
    assoc_stmt = select(UserRepository).where(
        UserRepository.user_id == current_user.id,
        UserRepository.repository_id == repo.id,
    )
    ur = (await session.execute(assoc_stmt)).scalars().first()
    if not ur:
        ur = UserRepository(
            user_id=current_user.id,
            repository_id=repo.id,
            monitoring_enabled=False,
            auto_repair_enabled=False,
            watched_branches=[],
        )
        session.add(ur)

    await session.commit()
    return _user_repo_dict(repo, ur)


# ── Remove / untrack a repo ───────────────────────────────────────────────────

@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_repository(
    repo_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove a repository association from the current user's tracked list."""
    _, ur = await _get_user_repo_or_403(repo_id, current_user, session)
    await session.delete(ur)
    await session.commit()


# ── Toggle monitoring ─────────────────────────────────────────────────────────

@router.patch("/{repo_id}/monitoring")
async def set_monitoring(
    repo_id: str,
    body: MonitoringRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Enable or disable continuous PR monitoring for the current user's repository."""
    repo, ur = await _get_user_repo_or_403(repo_id, current_user, session)
    ur.monitoring_enabled = body.enabled
    await session.commit()
    return _user_repo_dict(repo, ur)


# ── Toggle auto repair ───────────────────────────────────────────────────────

@router.patch("/{repo_id}/auto-repair")
async def set_auto_repair(
    repo_id: str,
    body: AutoRepairRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Enable or disable automatic patch repair loop on review completion for the user."""
    repo, ur = await _get_user_repo_or_403(repo_id, current_user, session)
    ur.auto_repair_enabled = body.enabled
    await session.commit()
    return _user_repo_dict(repo, ur)


# ── Set watched branches ──────────────────────────────────────────────────────

@router.patch("/{repo_id}/branches")
async def set_watched_branches(
    repo_id: str,
    body: BranchesRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Set which branches to watch for PRs (empty = all branches) for the user."""
    repo, ur = await _get_user_repo_or_403(repo_id, current_user, session)
    ur.watched_branches = list(body.branches)
    await session.commit()
    return _user_repo_dict(repo, ur)


# ── Open PRs for a repo ───────────────────────────────────────────────────────

@router.get("/{repo_id}/prs")
async def list_open_prs(
    repo_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """List open pull requests for a tracked repository via GitHub API."""
    repo, _ = await _get_user_repo_or_403(repo_id, current_user, session)

    token: Optional[str] = None
    try:
        token = await _cred_provider.get_user_credential(current_user.id, session)
    except CredentialExpiredError:
        pass

    if not token:
        token = _cred_provider.get_dev_pat()

    if not token:
        raise HTTPException(status_code=401, detail="Valid GitHub credential required to fetch PRs")

    try:
        return await _gh.list_open_prs(token, repo.owner, repo.name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}") from exc


# ── Knowledge Graph ───────────────────────────────────────────────────────────

@router.get("/{repo_id}/graph/status")
async def get_graph_status(
    repo_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Get the latest Knowledge Graph version metadata for an authorized repository."""
    await _get_user_repo_or_403(repo_id, current_user, session)

    stmt = (
        select(GraphVersion)
        .where(GraphVersion.repository_id == repo_id)
        .order_by(GraphVersion.created_at.desc())
    )
    gv = (await session.execute(stmt)).scalars().first()

    if not gv:
        return {"status": "NOT_BUILT", "node_count": 0, "edge_count": 0}

    return {
        "status": "READY",
        "graph_version_id": gv.id,
        "commit_sha": gv.commit_sha,
        "is_incremental": gv.is_incremental,
        "node_count": gv.node_count,
        "edge_count": gv.edge_count,
        "last_verified": gv.last_verified.isoformat() if gv.last_verified else None,
    }


@router.get("/{repo_id}/graph/nodes")
async def get_graph_nodes(
    repo_id: str,
    limit: int = Query(default=300, le=1000),
    node_type: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """Get Knowledge Graph nodes for visualization for an authorized repository."""
    await _get_user_repo_or_403(repo_id, current_user, session)

    stmt = select(GraphNode).where(GraphNode.repository_id == repo_id)
    if node_type:
        stmt = stmt.where(GraphNode.node_type == node_type)
    stmt = stmt.limit(limit)

    nodes = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": n.id,
            "node_type": n.node_type,
            "name": n.name,
            "qualified_name": n.qualified_name,
            "file_path": n.file_path,
            "start_line": n.start_line,
            "end_line": n.end_line,
            "source_commit": n.source_commit,
        }
        for n in nodes
    ]


@router.get("/{repo_id}/graph/edges")
async def get_graph_edges(
    repo_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """Get Knowledge Graph edges for visualization for an authorized repository."""
    await _get_user_repo_or_403(repo_id, current_user, session)

    stmt = (
        select(GraphEdge)
        .join(GraphVersion, GraphEdge.graph_version_id == GraphVersion.id)
        .where(GraphVersion.repository_id == repo_id)
    )
    edges = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": e.id,
            "source_node_id": e.source_node_id,
            "target_node_id": e.target_node_id,
            "edge_type": e.relationship_type,
        }
        for e in edges
    ]


# ── Manual dispatch endpoints ─────────────────────────────────────────────────

@router.post("/{repo_id}/graph/build")
async def trigger_graph_build(
    repo_id: str,
    body: GraphBuildRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Manually trigger a Knowledge Graph build for an authorized repository branch."""
    await _get_user_repo_or_403(repo_id, current_user, session)

    try:
        from worker.tasks.graph import build_knowledge_graph  # type: ignore[import]
        task = build_knowledge_graph.apply_async(
            kwargs={
                "repo_id": repo_id,
                "branch": body.branch,
                "triggered_by_user_id": current_user.id,
            },
            queue="graph_build",
        )
        return {"task_id": task.id, "status": "QUEUED", "branch": body.branch}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {exc}") from exc


@router.post("/{repo_id}/dispatch/review")
async def dispatch_review(
    repo_id: str,
    body: DispatchReviewRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Manually trigger a PR review agent on an authorized repository pull request."""
    repo, _ = await _get_user_repo_or_403(repo_id, current_user, session)

    # Create Job stamped with triggered_by_user_id
    job = Job(
        repository_id=repo.id,
        pr_number=body.pr_number,
        pr_title=f"Manual review for PR #{body.pr_number}",
        base_sha="",
        head_sha="",
        status="RECEIVED",
        triggered_by_user_id=current_user.id,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    try:
        from worker.tasks.review import run_pr_review  # type: ignore[import]
        task = run_pr_review.apply_async(
            kwargs={
                "job_id": job.id,
                "repo_id": repo_id,
                "owner": repo.owner,
                "repo_name": repo.name,
                "pr_number": body.pr_number,
                "triggered_by_user_id": current_user.id,
            },
            queue="review",
        )
        return {"task_id": task.id, "job_id": job.id, "status": "QUEUED"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {exc}") from exc
