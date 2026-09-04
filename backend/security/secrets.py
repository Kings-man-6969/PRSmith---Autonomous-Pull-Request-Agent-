"""Secret scanner for credentials, API tokens, and private keys."""

import re
from typing import List, Tuple

SECRET_PATTERNS = [
    (r"-----BEGIN [A-Z ]+ PRIVATE KEY-----", "PRIVATE_KEY"),
    (r"sk-[a-zA-Z0-9]{32,}", "OPENAI_KEY"),
    (r"ghp_[a-zA-Z0-9]{36}", "GITHUB_TOKEN"),
    (r"gho_[a-zA-Z0-9]{36}", "GITHUB_OAUTH"),
    (r"github_pat_[a-zA-Z0-9_]{50,}", "GITHUB_PAT"),
    (r"AKIA[0-9A-Z]{16}", "AWS_ACCESS_KEY"),
    (r"(?i)password\s*[:=]\s*['\"][^\n'\"]+['\"]", "PASSWORD"),
    (r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}", "BEARER_TOKEN"),
]


class SecretScanner:
    """Detects secrets in code diffs and repository context before LLM dispatch."""

    @staticmethod
    def scan(text: str) -> List[Tuple[str, str, int, int]]:
        """Returns list of (secret_type, matched_text, start_pos, end_pos)."""
        found = []
        for pattern, secret_type in SECRET_PATTERNS:
            for match in re.finditer(pattern, text):
                found.append((secret_type, match.group(0), match.start(), match.end()))
        return found
