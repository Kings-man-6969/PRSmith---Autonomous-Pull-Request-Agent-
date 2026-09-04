"""Abstract base class for sandbox execution backends."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class SandboxResult(BaseModel):
    """Result of a command executed inside the sandbox."""

    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    oom_killed: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.oom_killed


class SandboxUnavailableError(RuntimeError):
    """Raised when the required container sandbox backend is unavailable for untrusted execution."""
    pass


class SandboxBackend(ABC):
    """Abstract interface for running commands in isolated execution environments."""

    @abstractmethod
    async def run_command(
        self,
        command: List[str],
        worktree_path: Path,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        network: bool = False,
    ) -> SandboxResult:
        """Run a command inside the isolated sandbox."""
        pass
