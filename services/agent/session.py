"""Session helpers shared by the agent CLI and the job runner.

Keeps candidate loading and payload<->config mapping in one place so the offline
CLI (``scripts/run_curation_agent.py``) and the in-cluster job path
(``services/agent/job_runner.py``) build the agent from the *same* inputs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from services.agent.types import AgentConfig, Candidate

IMG_EXTS = (".jpg", ".jpeg", ".png")
STAGED_MANIFEST = Path(".luma_pipeline_staged") / "eligible_after_stage2.jsonl"


def load_candidates(source_dir: str | Path) -> list[Candidate]:
    """Seed candidates from the Stage2 manifest when present, else list image files.

    Using the Stage2 manifest means the agent inherits the cheap features
    (tech_score / fast_score / blur_type) the pipeline already computed, so its
    INSPECT steps are free and its ANALYZE ordering is informed.
    """
    src = Path(source_dir)
    manifest = src / STAGED_MANIFEST
    if manifest.exists():
        cands: list[Candidate] = []
        for line in manifest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            fn = str(r["file_name"])
            cands.append(
                Candidate(
                    image_id=fn,
                    image_path=str(src / fn),
                    features={
                        "tech_score": r.get("tech_score"),
                        "fast_score": r.get("fast_score"),
                        "blur_type": (r.get("debug_info") or {}).get("blur_type"),
                    },
                )
            )
        if cands:
            return cands
    files = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS)
    return [Candidate(image_id=p.name, image_path=str(p)) for p in files]


def ensure_step_budget(config: AgentConfig, n_candidates: int) -> AgentConfig:
    """Raise ``max_steps`` so the loop can inspect every candidate and still analyze.

    Each candidate costs one INSPECT step before any ANALYZE can run; with the
    default ceiling a large roll (e.g. 243 frames) would exhaust the step budget on
    inspection alone and force-finalize with zero analyses. Only ever raises, never
    lowers, an explicit budget.
    """
    needed = int(n_candidates) + int(config.max_inferences) + 20
    if config.max_steps < needed:
        config.max_steps = needed
    return config


def agent_config_from_payload(payload: Mapping[str, Any] | None) -> AgentConfig:
    """Build an :class:`AgentConfig` from a job payload's ``agent`` block (all optional)."""
    p = dict((payload or {}).get("agent") or {})
    defaults = AgentConfig()
    band = p.get("ambiguous_band")
    if isinstance(band, (list, tuple)) and len(band) == 2:
        ambiguous_band = (float(band[0]), float(band[1]))
    else:
        ambiguous_band = defaults.ambiguous_band
    return AgentConfig(
        target_keepers=int(p.get("target_keepers", defaults.target_keepers)),
        keep_score_threshold=float(p.get("keep_score_threshold", defaults.keep_score_threshold)),
        confidence_floor=float(p.get("confidence_floor", defaults.confidence_floor)),
        ambiguous_band=ambiguous_band,
        analyze_fast_score_floor=float(
            p.get("analyze_fast_score_floor", defaults.analyze_fast_score_floor)
        ),
        max_analyze_candidates=int(p.get("max_analyze_candidates", defaults.max_analyze_candidates)),
        max_steps=int(p.get("max_steps", defaults.max_steps)),
        max_inferences=int(p.get("max_inferences", defaults.max_inferences)),
        allow_escalation=bool(p.get("allow_escalation", defaults.allow_escalation)),
        base_tier=str(p.get("base_tier", defaults.base_tier)),
        escalation_tier=str(p.get("escalation_tier", defaults.escalation_tier)),
    )
