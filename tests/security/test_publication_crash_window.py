"""Failure injection test: Worker crashes after posting to GitHub before marking DB as POSTED.

Verifies that on subsequent attempt / retry, marker reconciliation detects the existing
GitHub comment and marks the PublishedReview row as POSTED without posting a duplicate.
"""

import uuid
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select

from backend.database.models import Job, PublishedReview, Repository, ReviewRun
from backend.database.sessions import async_session_factory
from backend.review.publisher import claim_publication, publish_review_comment


@pytest.mark.asyncio
async def test_publication_crash_window_reconciles_via_marker():
    async with async_session_factory() as session:
        unique_suffix = uuid.uuid4().hex[:8]
        repo = Repository(
            id=str(uuid.uuid4()),
            github_id=int(uuid.uuid4().int % 1000000),
            full_name=f"acme/crash-repo-{unique_suffix}",
            owner="acme",
            name=f"crash-repo-{unique_suffix}",
        )
        session.add(repo)
        await session.flush()

        head_sha = "aabbccddee11223344556677889900aabbccddee"
        job = Job(
            id=str(uuid.uuid4()),
            repository_id=repo.id,
            pr_number=88,
            base_sha="0011223344556677889900aabbccddee11223344",
            head_sha=head_sha,
            status="PUBLISHING",
        )
        session.add(job)
        await session.flush()

        chain_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        idempotency_key = f"pub:{repo.id}:88:{chain_hash}"

        # 1. Worker 1 claims publication in DB
        pub = await claim_publication(
            session=session,
            idempotency_key=idempotency_key,
            chain_hash=chain_hash,
            job=job,
            worker_id="worker-crash-1",
        )
        assert pub is not None
        assert pub.publication_status == "CLAIMED"

        # 2. Simulate GitHub comment was posted by Worker 1, but worker process crashed before DB update
        mock_github = AsyncMock()
        existing_comment_id = "gh-comment-posted-before-crash-999"
        expected_marker = f"<!-- prsmith:{idempotency_key} -->"
        mock_github.find_comment_by_marker.return_value = {
            "id": existing_comment_id,
            "body": f"{expected_marker}\n\nReview results content",
        }

        # 3. Retry / Recovery: Worker 2 or retry runner executes publish_review_comment
        reconciled = await publish_review_comment(
            session=session,
            pub=pub,
            repo_full_name=repo.full_name,
            pr_number=job.pr_number,
            review_body="Review results content",
            github_client=mock_github,
        )

        # Invariants:
        # - publication_status advanced to POSTED
        assert reconciled.publication_status == "POSTED"
        # - github_comment_id assigned to the existing comment
        assert reconciled.github_comment_id == str(existing_comment_id)
        # - GitHub post API was NEVER called again (zero duplicates posted)
        mock_github.post_review_comment.assert_not_called()
