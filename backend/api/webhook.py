"""GitHub Webhook Receiver with HMAC verification and idempotency deduplication."""

import hashlib
import hmac
import uuid
from typing import Any, Dict
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database.models import Job, Repository, WebhookDelivery
from backend.database.sessions import get_db_session
from backend.observability.logging import get_logger, log_event

logger = get_logger(__name__)
router = APIRouter(tags=["Webhooks"])


def verify_github_signature(secret: str, signature: str, payload_bytes: bytes) -> bool:
    """Verify HMAC SHA-256 signature from GitHub."""
    if not secret or not signature:
        return True  # Allow in dev if secret not configured

    if not signature.startswith("sha256="):
        return False

    expected_sig = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_sig, signature)


@router.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def handle_github_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_github_delivery: str = Header(..., alias="X-GitHub-Delivery"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
) -> Dict[str, str]:
    """Process incoming GitHub webhook events idempotently."""
    body_bytes = await request.body()

    # 1. HMAC Verification
    if settings.GITHUB_WEBHOOK_SECRET and not verify_github_signature(
        settings.GITHUB_WEBHOOK_SECRET, x_hub_signature_256 or "", body_bytes
    ):
        logger.warning("Invalid webhook signature rejected", delivery_id=x_github_delivery)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid HMAC signature",
        )

    payload = await request.json()
    log_event("webhook.received", event=x_github_event, delivery_id=x_github_delivery)

    # 2. Atomic Idempotency Deduplication (v5 frozen plan)
    repo_data = payload.get("repository", {})
    repo_full_name = repo_data.get("full_name", "")
    pr_data = payload.get("pull_request", {})
    pr_number = pr_data.get("number")
    delivery_uuid = str(uuid.uuid4())

    delivery_values = {
        "id": delivery_uuid,
        "delivery_id": x_github_delivery,
        "event_type": x_github_event,
        "repository_full_name": repo_full_name,
        "pr_number": pr_number,
        "payload": payload,
        "processed": False,
    }

    # Use dialect-aware atomic INSERT ON CONFLICT DO NOTHING
    dialect_name = ""
    if session.bind:
        dialect_name = session.bind.dialect.name

    if "sqlite" in dialect_name or "sqlite" in str(settings.DATABASE_URL):
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        stmt_delivery = (
            sqlite_insert(WebhookDelivery)
            .values(**delivery_values)
            .on_conflict_do_nothing(index_elements=["delivery_id"])
            .returning(WebhookDelivery.id)
        )
    else:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt_delivery = (
            pg_insert(WebhookDelivery)
            .values(**delivery_values)
            .on_conflict_do_nothing(index_elements=["delivery_id"])
            .returning(WebhookDelivery.id)
        )

    res_delivery = await session.execute(stmt_delivery)
    inserted_id = res_delivery.scalar_one_or_none()

    if inserted_id is None:
        logger.info("Ignoring duplicate webhook delivery via atomic unique constraint", delivery_id=x_github_delivery)
        return {"status": "ignored_duplicate", "delivery_id": x_github_delivery}

    # 3. Handle pull_request events (opened, synchronize, reopened)
    action = payload.get("action", "")
    if x_github_event == "pull_request" and action in ["opened", "synchronize", "reopened"]:
        head_sha = pr_data.get("head", {}).get("sha")
        base_sha = pr_data.get("base", {}).get("sha")
        title = pr_data.get("title", "")

        # Upsert repository
        stmt_repo = select(Repository).where(Repository.full_name == repo_full_name)
        repo_res = await session.execute(stmt_repo)
        repo = repo_res.scalars().first()

        if not repo:
            repo = Repository(
                id=str(uuid.uuid4()),
                github_id=repo_data.get("id", 0),
                full_name=repo_full_name,
                owner=repo_data.get("owner", {}).get("login", ""),
                name=repo_data.get("name", ""),
                default_branch=repo_data.get("default_branch", "main"),
                installation_id=payload.get("installation", {}).get("id"),
            )
            session.add(repo)
            await session.flush()

        # Create Job record in RECEIVED state with version=1
        job = Job(
            id=str(uuid.uuid4()),
            repository_id=repo.id,
            pr_number=pr_number,
            pr_title=title,
            base_sha=base_sha,
            head_sha=head_sha,
            status="RECEIVED",
            version=1,
        )
        session.add(job)

        # Enqueue OutboxEvent in the same database transaction (P0 reliability)
        from backend.database.models import OutboxEvent

        outbox_event = OutboxEvent(
            id=str(uuid.uuid4()),
            event_type="review.dispatch",
            aggregate_id=job.id,
            payload={
                "repository": repo_full_name,
                "pr_number": pr_number,
                "head_sha": head_sha,
                "base_sha": base_sha,
            },
            status="PENDING",
            attempts=0,
        )
        session.add(outbox_event)
        await session.commit()

        log_event("job.created", job_id=job.id, repo=repo_full_name, pr_number=pr_number, outbox_id=outbox_event.id)
        return {"status": "enqueued", "job_id": job.id, "delivery_id": x_github_delivery}

    # For other event types, commit the delivery record
    await session.commit()
    return {"status": "ignored_event_type", "event": x_github_event}
