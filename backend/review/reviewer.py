"""Strictly read-only Diff Reviewer agent."""

from typing import Any, Dict, List, Literal, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import GraphVersion, RepositorySnapshot
from backend.llm.factory import get_llm_provider
from backend.llm.protocol import LLMProvider, StructuredLLMRequest
from backend.llm.routing import ModelRouter, TaskType
from backend.observability.logging import get_logger, log_event
from backend.observability.metrics import MetricsTracker
from backend.review.evidence import EvidenceValidator
from backend.review.impact import ImpactAnalyzer
from backend.review.schemas import ReviewFinding, ReviewResult

logger = get_logger(__name__)

RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

REVIEWER_SYSTEM_PROMPT = """You are PRSmith's Read-Only Diff Reviewer.
Your purpose is to thoroughly analyze a Pull Request diff, understand its impact on the repository, and produce structured, evidence-backed findings.

CORE RULES:
1. Retrieval Before Generation: Base every finding on the provided repository context and diff.
2. Evidence Before Claims: Every claim about functions, callers, database access, or tests must reference concrete repository entities.
3. Fail Closed: If you lack sufficient context or the change touches unavailable external services, mark review_status as "INSUFFICIENT_CONTEXT" or "ESCALATED". DO NOT GUESS.
4. Categorize Precisely: Use BUG, TYPE_ERROR, SECURITY, PERFORMANCE, API_BREAK, TEST_FAILURE, CODE_SMELL, STYLE, ARCHITECTURE, or UNKNOWN.
5. Repairability Assessment: Mark findings as HIGH (local, deterministic, well-understood), MEDIUM (requires contextual reasoning), LOW (complex business logic / architecture), or NONE (insufficient evidence).
6. Never propose speculative changes without citing where in the repository the bug occurs.
7. Return strictly valid JSON adhering to the ReviewResult schema.
"""


class DiffReviewer:
    """Read-only agent that inspects PR diffs and generates structured findings."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm = llm_provider or get_llm_provider()
        self.router = ModelRouter(self.llm)
        self.evidence_validator = EvidenceValidator()
        self.impact_analyzer = ImpactAnalyzer()

    async def review_pr(
        self,
        session: AsyncSession,
        snapshot: RepositorySnapshot,
        graph_version: GraphVersion,
        pr_diff: str,
        context_items: List[Dict[str, Any]],
        risk_level: RiskLevel = "LOW",
        pr_number: Optional[int] = None,
        pr_title: Optional[str] = None,
    ) -> ReviewResult:
        """Run read-only code review over the PR diff and retrieved context."""
        repo_name = (
            snapshot.repository.full_name
            if snapshot.repository and hasattr(snapshot.repository, "full_name")
            else str(snapshot.repository_id)
        )
        resolved_pr_number = pr_number or getattr(snapshot, "pr_number", 0)
        resolved_pr_title = pr_title or getattr(snapshot, "pr_title", "PR")

        log_event(
            "review.started",
            repository=repo_name,
            head_sha=snapshot.head_sha,
            pr_number=resolved_pr_number,
            provider=self.llm.provider_name,
        )

        context_str = "\n\n".join(
            f"--- [{item.get('category', 'CONTEXT').upper()}] {item.get('file_path', '')} ---\n{item.get('content', '')}"
            for item in context_items
        )

        user_prompt = f"""=== PR METADATA ===
Repository: {repo_name}
PR #{resolved_pr_number}: {resolved_pr_title}
Base SHA: {snapshot.base_sha}
Head SHA: {snapshot.head_sha}
Risk Level: {risk_level}

=== REPOSITORY CONTEXT & KNOWLEDGE GRAPH ===
{context_str}

=== PULL REQUEST DIFF ===
{pr_diff}

Analyze the diff against the context and produce structured ReviewResult findings.
"""

        routed_model = self.router.route_model(
            task_type=TaskType.REVIEW, risk_level=risk_level
        )

        try:
            req = StructuredLLMRequest(
                system_prompt=REVIEWER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                model=routed_model,
                temperature=0.1,
            )
            response = await self.llm.generate_structured(req, schema=ReviewResult)

            result: ReviewResult = response.parsed or ReviewResult(
                review_status="NOT_ACTIONABLE", summary="No actionable issues detected."
            )

            # Validate each finding against the knowledge graph
            validated_issues = []
            for issue in result.issues:
                # 1. Hallucination gate
                _, enriched_issue = await self.evidence_validator.validate_finding_evidence(
                    session, graph_version.id, issue
                )
                # 2. Impact analysis enrichment
                enriched_issue = await self.impact_analyzer.enrich_finding_impact(
                    session, graph_version.id, enriched_issue
                )
                validated_issues.append(enriched_issue)

                # Record metrics
                MetricsTracker.record_review_finding(
                    enriched_issue.severity, enriched_issue.category, enriched_issue.repairability
                )
                log_event(
                    "review.finding_created",
                    finding_id=enriched_issue.id,
                    severity=enriched_issue.severity,
                    category=enriched_issue.category,
                )

            result.issues = validated_issues
            if not result.issues and result.review_status == "ACTIONABLE":
                result.review_status = "NOT_ACTIONABLE"

            return result

        except Exception as e:
            logger.error("Diff Reviewer execution failed", error=str(e), provider=self.llm.provider_name)
            return ReviewResult(
                review_status="ESCALATED",
                summary=f"Review failed due to unexpected error: {str(e)}",
                risk_level=risk_level,
            )
