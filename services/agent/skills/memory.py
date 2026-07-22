"""Long-term preference skills (durable across conversation resets)."""
from __future__ import annotations

from typing import Any, Callable, Optional

from services.agent.skills.base import SkillRegistry, SkillResult


class RememberPreferenceSkill:
    name = "remember_preference"
    description = (
        "Save a long-term user preference (e.g. prefer_high_contrast=true, "
        "avoid_silhouettes=true, language=zh). Survives chat reset; use for stable taste."
    )
    parameters = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Short preference key (snake_case)."},
            "value": {"type": "string", "description": "Preference value as text."},
        },
        "required": ["key", "value"],
        "additionalProperties": False,
    }

    def __init__(self, owner: str, *, persist: Optional[Callable[[str, str], None]] = None) -> None:
        self._owner = owner
        self._persist = persist

    def run(self, args: dict[str, Any]) -> SkillResult:
        key = str(args.get("key") or "").strip()
        value = str(args.get("value") or "").strip()
        if not key or not value:
            return SkillResult(ok=False, error="'key' and 'value' are required")
        if self._persist is None:
            return SkillResult(ok=False, error="preference persistence not configured")
        try:
            self._persist(key, value)
        except Exception as exc:
            return SkillResult(ok=False, error=str(exc))
        return SkillResult(
            ok=True,
            output=f"Remembered preference {key}={value}.",
            metadata={"key": key, "value": value, "owner": self._owner},
        )


class ListPreferencesSkill:
    name = "list_preferences"
    description = "List long-term preferences saved for this user/session owner."
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    def __init__(self, owner: str, *, loader: Optional[Callable[[], dict[str, str]]] = None) -> None:
        self._owner = owner
        self._loader = loader

    def run(self, args: dict[str, Any]) -> SkillResult:
        prefs = self._loader() if self._loader else {}
        if not prefs:
            return SkillResult(ok=True, output="No long-term preferences saved.", metadata={"preferences": {}})
        summary = "; ".join(f"{k}={v}" for k, v in prefs.items())
        return SkillResult(
            ok=True,
            output=f"Preferences: {summary}",
            metadata={"preferences": prefs, "owner": self._owner},
        )


def register_memory_skills(
    registry: SkillRegistry,
    *,
    owner: str,
    persist: Callable[[str, str], None],
    loader: Callable[[], dict[str, str]],
) -> None:
    registry.register(RememberPreferenceSkill(owner, persist=persist))
    registry.register(ListPreferencesSkill(owner, loader=loader))
