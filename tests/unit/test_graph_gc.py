"""Unit tests for Graph GC safety invariants and EdgeProvenance weighting."""

from datetime import timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import GraphVersion, Job, Repository, utcnow
from backend.languages.python_analyzer import PythonAnalyzer
from backend.orchestration.graph_gc import GraphGarbageCollector


def test_python_analyzer_edge_provenance_and_weighting():
    """Verify that PythonAnalyzer tags edges with DIRECT_STATIC vs INFERRED provenance."""
    code = """
import os
from math import sqrt

def helper():
    return 42

def caller_func():
    val = helper()           # direct static call -> DIRECT_STATIC (1.0)
    proc = os.system("ls")     # attribute call -> INFERRED (0.7)
    return val
"""
    analyzer = PythonAnalyzer()
    symbols, relations = analyzer.parse_file("sample.py", code)

    # Find the relation for helper()
    helper_rel = next((r for r in relations if r.target_qualified_name == "helper"), None)
    assert helper_rel is not None
    assert helper_rel.edge_provenance == "DIRECT_STATIC"
    assert helper_rel.provenance_weight == 1.0

    # Find the relation for os.system()
    os_rel = next((r for r in relations if "os.system" in r.target_qualified_name), None)
    assert os_rel is not None
    assert os_rel.edge_provenance == "INFERRED"
    assert os_rel.provenance_weight == 0.7


@pytest.mark.asyncio
async def test_graph_gc_protects_active_jobs(db_session: AsyncSession):
    """Graph versions referenced by active jobs must never be collected."""
    repo = Repository(
        github_id=7701,
        full_name="test-org/gc-test-repo",
        owner="test-org",
        name="gc-test-repo",
    )
    db_session.add(repo)
    await db_session.flush()

    past_time = utcnow() - timedelta(hours=48)

    # 1. Graph version held by an active job (REVIEWING)
    gv_active = GraphVersion(
        repository_id=repo.id,
        commit_sha="commit_active_1",
        created_at=past_time,
    )
    db_session.add(gv_active)
    await db_session.flush()

    active_job = Job(
        repository_id=repo.id,
        pr_number=101,
        base_sha="base101",
        head_sha="head101",
        status="REVIEWING",
        graph_version_id=gv_active.id,
    )
    db_session.add(active_job)

    # 2. Graph version held by a completed job (COMPLETED)
    gv_completed = GraphVersion(
        repository_id=repo.id,
        commit_sha="commit_completed_2",
        created_at=past_time,
    )
    db_session.add(gv_completed)
    await db_session.flush()

    completed_job = Job(
        repository_id=repo.id,
        pr_number=102,
        base_sha="base102",
        head_sha="head102",
        status="COMPLETED",
        graph_version_id=gv_completed.id,
    )
    db_session.add(completed_job)

    # 3. Unreferenced old graph version
    gv_unreferenced = GraphVersion(
        repository_id=repo.id,
        commit_sha="commit_unref_3",
        created_at=past_time,
    )
    db_session.add(gv_unreferenced)

    # 4. Young graph version (within retention window)
    gv_young = GraphVersion(
        repository_id=repo.id,
        commit_sha="commit_young_4",
        created_at=utcnow(),
    )
    db_session.add(gv_young)

    await db_session.commit()

    # Run GC with 24-hour retention
    prunable = await GraphGarbageCollector.get_prunable_graph_versions(
        session=db_session,
        repository_id=repo.id,
        retention_hours=24,
    )
    prunable_ids = {gv.id for gv in prunable}

    # Invariants:
    # - gv_active MUST NOT be prunable
    assert gv_active.id not in prunable_ids

    # - gv_young MUST NOT be prunable (within retention)
    assert gv_young.id not in prunable_ids

    # - gv_completed and gv_unreferenced MUST be prunable
    assert gv_completed.id in prunable_ids
    assert gv_unreferenced.id in prunable_ids

    # Prune and verify deletion
    pruned_count = await GraphGarbageCollector.prune_stale_graphs(
        session=db_session,
        repository_id=repo.id,
        retention_hours=24,
    )
    assert pruned_count == 2

    # Verify gv_active is still in the database
    still_active = await db_session.scalar(
        select(GraphVersion).where(GraphVersion.id == gv_active.id)
    )
    assert still_active is not None
