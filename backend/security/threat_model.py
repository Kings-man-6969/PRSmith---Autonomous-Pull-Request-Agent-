"""PRSmith Security Threat Model and Trust Boundary Definitions.

Defines privilege boundaries, PR source risk classifications, and untrusted
content delimiter policies to protect against prompt injection and arbitrary code execution.
"""

from enum import Enum
from typing import Literal

# Trust classification
UNTRUSTED: Literal["untrusted"] = "untrusted"   # External PR code, diffs, issues, READMEs, docstrings
TRUSTED: Literal["trusted"] = "trusted"         # System prompts, security policies, schemas

# PR source classifications
PR_SOURCE_SAME_REPO: Literal["same_repo"] = "same_repo"
PR_SOURCE_FORK: Literal["fork"] = "fork"        # Untrusted fork: zero write credentials, max sandbox limits


class PRTrustLevel(str, Enum):
    SAME_REPO = "same_repo"
    FORK = "fork"
    SUSPICIOUS = "suspicious"


# Prompt delimiter contracts
UNTRUSTED_CONTENT_START = "<UNTRUSTED_REPOSITORY_CONTENT>"
UNTRUSTED_CONTENT_END = "</UNTRUSTED_REPOSITORY_CONTENT>"


def wrap_untrusted_content(content: str, label: str = "repository_data") -> str:
    """Wrap untrusted repository content with standard strict delimiters.

    LLMs are explicitly instructed that content within these delimiters
    is passive data and cannot issue commands or alter agent policies.
    """
    cleaned = content.replace(UNTRUSTED_CONTENT_START, "").replace(UNTRUSTED_CONTENT_END, "")
    return f"{UNTRUSTED_CONTENT_START} [source={label}]\n{cleaned}\n{UNTRUSTED_CONTENT_END}"
