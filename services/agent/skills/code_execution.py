"""Sandboxed Python code-execution skill.

Running model-authored code is the highest-risk Agent Skill, so isolation is layered:

1. **Separate process** — code runs in a fresh ``python -I`` (isolated mode: ignores
   ``PYTHONPATH``/user site) subprocess, never in the agent process.
2. **Wall-clock timeout** — ``subprocess`` is killed past ``timeout_s``.
3. **Resource limits (POSIX)** — CPU seconds, address space, and output file size are
   capped via ``resource.setrlimit`` in a ``preexec_fn`` so a runaway script can't burn
   the host.
4. **Scrubbed environment + temp cwd** — minimal env, an isolated working directory.
5. **Output cap** — stdout/stderr truncated to keep the observation small.

Honest boundary (documented, not hidden): this is process-level hardening, **not** a
security boundary against a determined adversary. True isolation (no network, syscall
filtering, fs jails) needs a container / gVisor / Firecracker — which is exactly where
this skill is meant to be deployed in production (see ``deploy/k8s``). The interface
here is the seam that a stronger backend slots behind.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from typing import Any

from services.agent.skills.base import SkillResult

try:  # POSIX-only; absent on Windows
    import resource
except ImportError:  # pragma: no cover - platform dependent
    resource = None  # type: ignore[assignment]


_OUTPUT_CAP = 8192  # chars of stdout/stderr kept in the observation


def _truncate(text: str, cap: int = _OUTPUT_CAP) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    return text[:cap] + f"\n...[truncated {len(text) - cap} chars]", True


def _make_preexec(cpu_s: int, mem_mb: int, fsize_mb: int):
    """Build a ``preexec_fn`` that applies rlimits in the child (POSIX only)."""
    if resource is None:  # pragma: no cover - platform dependent
        return None

    def _limit() -> None:
        os.setsid()  # own process group so we can kill the whole tree on timeout
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        if mem_mb > 0:
            nbytes = mem_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))
            except (ValueError, OSError):  # some platforms (macOS) restrict RLIMIT_AS
                pass
        if fsize_mb > 0:
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_mb * 1024 * 1024,) * 2)

    return _limit


class PythonExecSkill:
    """Execute a short Python snippet in an isolated subprocess and capture output."""

    name = "python_exec"
    description = (
        "Execute a self-contained Python 3 snippet in an isolated subprocess "
        "(timeout + resource limits) and return its stdout/stderr. No network or "
        "host filesystem access should be assumed. Use print() to return results."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to execute."},
            "timeout_s": {"type": "integer", "minimum": 1, "maximum": 60,
                          "description": "Wall-clock timeout (default 5)."},
        },
        "required": ["code"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        default_timeout_s: int = 5,
        cpu_seconds: int = 5,
        mem_mb: int = 256,
        fsize_mb: int = 8,
    ) -> None:
        self._default_timeout = default_timeout_s
        self._cpu = cpu_seconds
        self._mem_mb = mem_mb
        self._fsize_mb = fsize_mb

    def run(self, args: dict[str, Any]) -> SkillResult:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return SkillResult(ok=False, error="'code' must be a non-empty string")
        try:
            timeout = int(args.get("timeout_s") or self._default_timeout)
        except (TypeError, ValueError):
            timeout = self._default_timeout
        timeout = max(1, min(60, timeout))

        with tempfile.TemporaryDirectory(prefix="agent_pyexec_") as workdir:
            script = os.path.join(workdir, "snippet.py")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(code)
            # Minimal, scrubbed environment; isolated cwd; isolated interpreter mode.
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": workdir, "TMPDIR": workdir}
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", "-B", script],
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    preexec_fn=_make_preexec(self._cpu, self._mem_mb, self._fsize_mb),
                )
            except subprocess.TimeoutExpired as exc:
                partial, _ = _truncate(exc.stdout or "" if isinstance(exc.stdout, str) else "")
                return SkillResult(
                    ok=False,
                    output=partial,
                    error=f"timed out after {timeout}s",
                    metadata={"timed_out": True, "timeout_s": timeout},
                )

        stdout, out_trunc = _truncate(proc.stdout or "")
        stderr, err_trunc = _truncate(proc.stderr or "")
        return SkillResult(
            ok=(proc.returncode == 0),
            output=stdout,
            error=(stderr or None) if proc.returncode != 0 else None,
            metadata={
                "returncode": proc.returncode,
                "stderr": stderr,
                "truncated": out_trunc or err_trunc,
                "timeout_s": timeout,
            },
        )
