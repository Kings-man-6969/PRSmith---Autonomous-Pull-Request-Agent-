"""Risk analyzer evaluating Pull Requests before triggering expensive processing."""

import re
from dataclasses import dataclass, field
from typing import List, Literal
from backend.config import settings


@dataclass
class RiskAssessment:
    """Calculated risk metrics for a Pull Request."""

    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    risk_score: int  # 0 to 100
    reasons: List[str] = field(default_factory=list)
    changed_files_count: int = 0
    lines_added: int = 0
    lines_deleted: int = 0
    has_auth_changes: bool = False
    has_migration_changes: bool = False
    has_security_boundary_changes: bool = False


class PRRiskAnalyzer:
    """Assesses Pull Request risk based on diff dimensions and sensitive file paths."""

    AUTH_PATTERNS = [r"auth", r"login", r"password", r"jwt", r"token", r"oauth", r"session"]
    MIGRATION_PATTERNS = [r"migration", r"alembic", r"schema", r"\.sql$"]
    SECURITY_PATTERNS = [r"crypto", r"ssl", r"tls", r"firewall", r"policy", r"permission"]

    @classmethod
    def analyze(
        cls,
        changed_files: List[str],
        lines_added: int,
        lines_deleted: int,
    ) -> RiskAssessment:
        score = 0
        reasons = []

        total_lines = lines_added + lines_deleted

        # 1. Size thresholds
        if total_lines > 1000:
            score += 30
            reasons.append(f"Very large diff ({total_lines} lines changed).")
        elif total_lines > 400:
            score += 15
            reasons.append(f"Substantial diff ({total_lines} lines changed).")

        if len(changed_files) > 15:
            score += 25
            reasons.append(f"Wide blast radius ({len(changed_files)} files modified).")
        elif len(changed_files) > 8:
            score += 10

        # 2. Sensitive domains
        has_auth = False
        has_migration = False
        has_security = False

        for f in changed_files:
            f_lower = f.lower()
            if any(re.search(p, f_lower) for p in cls.AUTH_PATTERNS):
                has_auth = True
            if any(re.search(p, f_lower) for p in cls.MIGRATION_PATTERNS):
                has_migration = True
            if any(re.search(p, f_lower) for p in cls.SECURITY_PATTERNS):
                has_security = True

        if has_auth:
            score += 30
            reasons.append("Touches authentication/authorization code.")
        if has_migration:
            score += 25
            reasons.append("Contains database schema migrations.")
        if has_security:
            score += 20
            reasons.append("Modifies cryptographic or security boundary components.")

        score = min(100, score)

        if score >= settings.PR_RISK_CRITICAL_THRESHOLD:
            level = "CRITICAL"
        elif score >= settings.PR_RISK_HIGH_THRESHOLD:
            level = "HIGH"
        elif score >= 35:
            level = "MEDIUM"
        else:
            level = "LOW"

        return RiskAssessment(
            risk_level=level,
            risk_score=score,
            reasons=reasons,
            changed_files_count=len(changed_files),
            lines_added=lines_added,
            lines_deleted=lines_deleted,
            has_auth_changes=has_auth,
            has_migration_changes=has_migration,
            has_security_boundary_changes=has_security,
        )
