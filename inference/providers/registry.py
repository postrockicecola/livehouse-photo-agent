"""
Registered inference backends for discovery (e.g. ``/api/infra/providers``).

Concrete providers implement ``InferenceProvider`` and set ``PROVIDER_ID``; this module
holds **catalog metadata** only (no runtime wiring).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderSpec:
    """Static catalog entry for operators and HTTP APIs."""

    id: str
    display_name: str
    supports_remote_endpoint: bool = False
    default_model_name: str | None = None
    description: str = ""


_SPECS: dict[str, ProviderSpec] = {}


def register_provider_spec(spec: ProviderSpec) -> None:
    _SPECS[spec.id] = spec


def get_provider_spec(provider_id: str) -> ProviderSpec | None:
    return _SPECS.get(provider_id)


def all_provider_specs() -> tuple[ProviderSpec, ...]:
    return tuple(sorted(_SPECS.values(), key=lambda s: s.id))


def build_providers_catalog(
    model_cfg: dict[str, Any],
    *,
    active_provider: str,
) -> dict[str, Any]:
    """
    Shape matches ``InfraProvidersResponse``: ``active_provider`` + ``providers`` list.

    Merges YAML ``model.*`` with the registry; ``runtime`` comes from in-process counters
    keyed by ``InferenceProvider.provider_id``.
    """
    from infra.metrics import provider_runtime_metrics

    active = str(active_provider or "ollama").strip().lower()
    runtime = provider_runtime_metrics()
    runtime_map = {str(x.get("provider")): x for x in runtime.get("providers", [])}

    specs_list = list(all_provider_specs())
    known = {s.id for s in specs_list}
    if active and active not in known:
        specs_list.append(
            ProviderSpec(
                id=active,
                display_name=active.replace("_", " ").title(),
                supports_remote_endpoint=True,
                default_model_name=None,
                description="Active config provider (extend registry when adding a first-class integration).",
            )
        )
        specs_list.sort(key=lambda s: s.id)

    items: list[dict[str, Any]] = []
    for spec in specs_list:
        is_active = spec.id == active
        endpoint: str | None = None
        model_name: str | None = None
        fallback_model_name: str | None = None
        if is_active:
            if spec.supports_remote_endpoint:
                ep = str(model_cfg.get("endpoint", "") or "").strip()
                endpoint = ep or None
            model_name = str(model_cfg.get("model_name", "") or "").strip() or None
            fb = str(model_cfg.get("fallback_model_name", "") or "").strip()
            fallback_model_name = fb or None
        else:
            model_name = spec.default_model_name

        items.append(
            {
                "name": spec.id,
                "display_name": spec.display_name,
                "enabled": is_active,
                "endpoint": endpoint,
                "model_name": model_name,
                "fallback_model_name": fallback_model_name,
                "runtime": runtime_map.get(spec.id),
                "description": spec.description or None,
                "supports_remote_endpoint": spec.supports_remote_endpoint,
            }
        )

    return {"active_provider": active, "providers": items}


# --- built-in backends (add new providers here + new InferenceProvider subclass) ---
register_provider_spec(
    ProviderSpec(
        id="ollama",
        display_name="Ollama",
        supports_remote_endpoint=True,
        default_model_name=None,
        description="Local or remote Ollama with /api/generate (e.g. LLaVA).",
    )
)
register_provider_spec(
    ProviderSpec(
        id="mock",
        display_name="Mock",
        supports_remote_endpoint=False,
        default_model_name="mock-vlm",
        description="In-process stub for tests and offline pipelines.",
    )
)
