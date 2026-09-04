"""Docker-based sandbox execution backend.

Provides risk-reduced execution of untrusted code inside a containerized sandbox.
Strictly fails closed when Docker is unavailable in production.
"""

import asyncio
import time
from pathlib import Path
from typing import Dict, List, Optional
import docker

from backend.config import settings
from backend.observability.logging import get_logger
from backend.sandbox.interface import SandboxBackend, SandboxResult, SandboxUnavailableError
from backend.sandbox.policy import SandboxPolicy

logger = get_logger(__name__)


class DockerBackend(SandboxBackend):
    """Executes validation commands inside isolated, hardened Docker containers."""

    def __init__(
        self,
        image: Optional[str] = None,
        policy: Optional[SandboxPolicy] = None,
    ):
        self.image = image or settings.SANDBOX_DOCKER_IMAGE
        self.policy = policy or SandboxPolicy()
        self.client: Optional[docker.DockerClient] = None
        self._available: bool = False

        try:
            self.client = docker.from_env()
            self.client.ping()
            self._available = True
        except Exception as exc:
            self.client = None
            self._available = False
            logger.warning(
                "Docker daemon unavailable at initialization",
                environment=settings.ENVIRONMENT,
                allow_dev_subprocess=settings.ALLOW_LOCAL_DEV_EXECUTION,
                error=str(exc),
            )

    def _ensure_docker_client(self) -> bool:
        """Attempt to re-establish connection to Docker daemon if previously down."""
        if self._available and self.client:
            return True
        try:
            self.client = docker.from_env()
            self.client.ping()
            self._available = True
            return True
        except Exception:
            self.client = None
            self._available = False
            return False

    async def run_command(
        self,
        command: List[str],
        worktree_path: Path,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        network: bool = False,
    ) -> SandboxResult:
        """Run command in container or fail-closed if Docker is unavailable."""
        if not self.policy.is_command_permitted(command):
            return SandboxResult(
                command=" ".join(command),
                exit_code=1,
                stderr="Execution blocked: Command violated sandbox security policy.",
            )

        cmd_timeout = timeout or self.policy.timeout_seconds
        start_time = time.time()

        # Check/refresh Docker daemon availability
        if self._ensure_docker_client() and self.client:
            return await self._run_docker(
                command=command,
                worktree_path=worktree_path,
                env=env,
                cmd_timeout=cmd_timeout,
                start_time=start_time,
                network=network,
            )

        # Docker is unavailable: enforce fail-closed security boundary
        if settings.ENVIRONMENT == "production":
            raise SandboxUnavailableError(
                "Docker sandbox is unavailable in production. "
                "Untrusted repository code cannot be executed on the host. "
                "Job will be escalated or retried."
            )

        if not settings.ALLOW_LOCAL_DEV_EXECUTION:
            raise SandboxUnavailableError(
                "Docker daemon unavailable and ALLOW_LOCAL_DEV_EXECUTION is False. "
                "Untrusted code execution blocked."
            )

        logger.warning(
            "DEV ONLY: Subprocess fallback active — NEVER enable ALLOW_LOCAL_DEV_EXECUTION in production",
            command=" ".join(command),
            worktree=str(worktree_path),
        )
        return await self._run_subprocess(
            command=command,
            worktree_path=worktree_path,
            env=env,
            cmd_timeout=cmd_timeout,
            start_time=start_time,
        )

    async def _run_docker(
        self,
        command: List[str],
        worktree_path: Path,
        env: Optional[Dict[str, str]],
        cmd_timeout: int,
        start_time: float,
        network: bool,
    ) -> SandboxResult:
        """Execute command inside hardened Docker container."""
        assert self.client is not None
        container = None
        try:
            container_workdir = "/workspace"
            # Strip sensitive environment variables
            clean_env = {
                k: v
                for k, v in (env or {}).items()
                if not any(
                    s in k.lower()
                    for s in ["key", "secret", "token", "password", "aws", "auth"]
                )
            }

            container = self.client.containers.create(
                image=self.image,
                command=command,
                working_dir=container_workdir,
                environment=clean_env,
                network_mode="none" if not network else "bridge",
                mem_limit=self.policy.memory_limit,
                nano_cpus=int(float(self.policy.cpu_limit) * 1e9),
                user=self.policy.user,
                read_only=True,
                tmpfs={"/tmp": "size=256m"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                pids_limit=128,
                volumes={
                    str(worktree_path): {
                        "bind": container_workdir,
                        "mode": "rw",
                    }
                },
            )
            container.start()

            res = await asyncio.wait_for(
                asyncio.to_thread(container.wait), timeout=cmd_timeout
            )
            exit_code = res.get("StatusCode", 1)
            logs = container.logs(stdout=True, stderr=True)
            duration_ms = int((time.time() - start_time) * 1000)

            return SandboxResult(
                command=" ".join(command),
                exit_code=exit_code,
                stdout=logs.decode("utf-8", errors="ignore"),
                duration_ms=duration_ms,
            )
        except asyncio.TimeoutError:
            if container:
                try:
                    container.kill()
                except Exception:
                    pass
            return SandboxResult(
                command=" ".join(command),
                exit_code=-1,
                stderr="Command timed out in container sandbox.",
                timed_out=True,
                duration_ms=int(cmd_timeout * 1000),
            )
        except Exception as e:
            logger.error("Docker container execution failed", error=str(e))
            return SandboxResult(
                command=" ".join(command),
                exit_code=1,
                stderr=f"Docker container error: {str(e)}",
            )
        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    async def _run_subprocess(
        self,
        command: List[str],
        worktree_path: Path,
        env: Optional[Dict[str, str]],
        cmd_timeout: int,
        start_time: float,
    ) -> SandboxResult:
        """Run via local subprocess strictly for non-production development."""
        try:
            clean_env = {
                k: v
                for k, v in (env or {}).items()
                if not any(
                    s in k.lower()
                    for s in ["key", "secret", "token", "password", "aws", "auth"]
                )
            }
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(worktree_path),
                env=clean_env or None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=cmd_timeout
                )
                duration_ms = int((time.time() - start_time) * 1000)
                return SandboxResult(
                    command=" ".join(command),
                    exit_code=proc.returncode or 0,
                    stdout=stdout_bytes.decode("utf-8", errors="ignore"),
                    stderr=stderr_bytes.decode("utf-8", errors="ignore"),
                    duration_ms=duration_ms,
                )
            except asyncio.TimeoutError:
                proc.kill()
                return SandboxResult(
                    command=" ".join(command),
                    exit_code=-1,
                    stderr="Command timed out in subprocess execution.",
                    timed_out=True,
                    duration_ms=int(cmd_timeout * 1000),
                )
        except Exception as e:
            return SandboxResult(
                command=" ".join(command),
                exit_code=1,
                stderr=f"Execution error: {str(e)}",
            )
