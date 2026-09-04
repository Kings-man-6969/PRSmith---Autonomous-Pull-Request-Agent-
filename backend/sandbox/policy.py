"""Sandbox policy configuration and enforcement."""

from pydantic import BaseModel
from backend.config import settings


class SandboxPolicy(BaseModel):
    """Resource limits and security constraints for sandbox execution."""

    cpu_limit: str = settings.SANDBOX_CPU_LIMIT
    memory_limit: str = settings.SANDBOX_MEMORY_LIMIT
    timeout_seconds: int = settings.SANDBOX_TIMEOUT_SECONDS
    network_enabled: bool = settings.SANDBOX_NETWORK_ENABLED
    read_only_root: bool = True
    user: str = settings.SANDBOX_USER

    # Denylist for dangerous commands
    FORBIDDEN_COMMANDS: list[str] = [
        "curl",
        "wget",
        "nc",
        "netcat",
        "ssh",
        "scp",
        "sudo",
        "chmod 777",
        "rm -rf /",
        ":(){ :|:& };:",
    ]

    def is_command_permitted(self, command: list[str]) -> bool:
        """Check if command is permitted under safety policy."""
        cmd_str = " ".join(command).lower()
        for forbidden in self.FORBIDDEN_COMMANDS:
            if forbidden in cmd_str:
                return False
        return True
