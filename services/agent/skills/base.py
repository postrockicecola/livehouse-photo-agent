"""Skill protocol + registry: the contract every generic Agent Skill implements.

A ``Skill`` is a self-describing tool: a ``name``, a human/LLM ``description``, a
JSON-schema ``parameters`` block, and a ``run(args) -> SkillResult``. The registry
dispatches by name, never lets a skill exception crash the loop (errors come back as
a failed ``SkillResult``), and renders the whole set as OpenAI function-calling
``tools`` specs so a planner LLM can pick and fill a call.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class SkillResult:
    """Uniform skill output: ok flag, string output, error, and structured metadata."""

    ok: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_observation(self) -> dict[str, Any]:
        """Compact dict an agent loop can append to the trace / feed back to the LLM."""
        obs: dict[str, Any] = {"ok": self.ok}
        if self.output:
            obs["output"] = self.output
        if self.error:
            obs["error"] = self.error
        if self.metadata:
            obs["metadata"] = self.metadata
        return obs


@runtime_checkable
class Skill(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the call arguments

    def run(self, args: dict[str, Any]) -> SkillResult: ...


class SkillRegistry:
    """Holds skills, dispatches by name, and exports function-calling tool specs."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if not getattr(skill, "name", ""):
            raise ValueError("skill must have a non-empty name")
        if skill.name in self._skills:
            raise ValueError(f"skill {skill.name!r} already registered")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return list(self._skills)

    def dispatch(self, name: str, args: dict[str, Any] | None = None) -> SkillResult:
        """Run a skill by name. Unknown skills and skill exceptions become error results
        (never propagated) so a tool-using loop stays alive and can self-correct."""
        skill = self._skills.get(name)
        if skill is None:
            return SkillResult(ok=False, error=f"unknown skill: {name!r}")
        try:
            return skill.run(dict(args or {}))
        except Exception as exc:  # a misbehaving skill must not kill the agent loop
            logger.exception("skill %s raised", name)
            return SkillResult(ok=False, error=f"skill {name!r} crashed: {exc}")

    def tool_specs(self) -> list[dict[str, Any]]:
        """OpenAI/vLLM function-calling ``tools`` array for the whole registry."""
        return [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.parameters,
                },
            }
            for s in self._skills.values()
        ]
