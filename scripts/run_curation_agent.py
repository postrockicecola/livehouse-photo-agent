#!/usr/bin/env python3
"""Run the curation agent over a session folder (demoable end-to-end).

Two backends:

    # No model needed — deterministic fake scores, shows the loop/trace/metrics:
    python scripts/run_curation_agent.py --source-dir /path/to/Previews --mock

    # Real VLM via the existing inference layer + Stage3 prompt/parser:
    python scripts/run_curation_agent.py --source-dir /path/to/Previews \
        --config configs/livehouse.yaml --max-inferences 20 --keepers 10

Candidates are seeded from a Stage2 manifest when present
(``<source>/.luma_pipeline_staged/eligible_after_stage2.jsonl``) so the agent
inherits cheap features; otherwise it falls back to listing image files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.agent import (  # noqa: E402
    AgentConfig,
    AnalyzeTool,
    CurationAgent,
    FinalizeTool,
    InspectTool,
    ToolRegistry,
    build_stage3_analyze_fn,
)
from services.agent.session import ensure_step_budget, load_candidates  # noqa: E402
from utils.stage3_dimensions import STAGE3_DIM_KEYS  # noqa: E402

logger = logging.getLogger("curation_agent")


def _mock_analyze_fn():
    """Deterministic pseudo-scores from the filename hash — no model required."""

    def _fn(image_path: str, tier: str) -> dict:
        h = int(hashlib.sha1(image_path.encode("utf-8")).hexdigest(), 16)
        score = 30.0 + (h % 700) / 10.0  # 30.0 .. 100.0
        # fast tier is deliberately less confident so escalation paths trigger
        confidence = 0.72 if tier == "fast" else 0.9
        return {
            "score": round(score, 1),
            "confidence": confidence,
            "dimensions": {k: (h >> (i * 3)) % 11 for i, k in enumerate(STAGE3_DIM_KEYS)},
            "verdict": f"mock {tier} verdict",
        }

    return _fn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-dir", required=True, help="folder of preview images")
    ap.add_argument("--config", default="configs/livehouse.yaml", help="used in real (non-mock) mode")
    ap.add_argument("--mock", action="store_true", help="use deterministic fake scores (no model)")
    ap.add_argument("--keepers", type=int, default=10)
    ap.add_argument("--keep-threshold", type=float, default=70.0)
    ap.add_argument("--max-inferences", type=int, default=40)
    ap.add_argument("--no-escalation", action="store_true")
    ap.add_argument(
        "--llm",
        action="store_true",
        help="drive the loop with the LLM planner (configured provider); falls back to heuristic on bad output",
    )
    ap.add_argument(
        "--llm-model",
        default=None,
        help="override the planner model (e.g. qwen2.5:3b-instruct) while scoring keeps the VLM",
    )
    ap.add_argument("--trace", action="store_true", help="print every step")
    ap.add_argument("--out", default=None, help="write the result (metrics + selection) to this JSON file")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    source_dir = Path(args.source_dir).expanduser().resolve()
    if not source_dir.is_dir():
        logger.error("source dir not found: %s", source_dir)
        return 2

    candidates = load_candidates(source_dir)
    if not candidates:
        logger.error("no candidate images found under %s", source_dir)
        return 2
    logger.info("loaded %s candidates", len(candidates))

    if args.mock:
        analyze_fn = _mock_analyze_fn()
    else:
        analyze_fn = build_stage3_analyze_fn(
            config_path=args.config,
            source_dir=str(source_dir),
            trace_id="curation_agent_cli",
        )

    tools = ToolRegistry(
        inspect=InspectTool(),
        analyze=AnalyzeTool(analyze_fn, default_tier="fast"),
        finalize=FinalizeTool(),
    )
    config = AgentConfig(
        target_keepers=args.keepers,
        keep_score_threshold=args.keep_threshold,
        max_inferences=args.max_inferences,
        allow_escalation=not args.no_escalation,
    )
    ensure_step_budget(config, len(candidates))
    agent = CurationAgent(tools=tools, config=config, planner=_build_planner(args))
    result = agent.run(candidates)

    if args.trace:
        for step in result.steps:
            c = step.call
            tail = f" -> {step.result.observation}" if step.result.ok else f" !! {step.result.error}"
            note = f"  [{step.reflection}]" if step.reflection else ""
            print(f"#{step.index:<3} [{c.source}] {c.action.value} {c.image_id or ''} :: {c.reason}{tail}{note}")

    print("\n=== curation agent result ===")
    print(json.dumps(result.metrics, indent=2))
    print("\nselected:")
    for cid in result.selected:
        c = next(x for x in result.candidates if x.image_id == cid)
        print(f"  {cid}  score={c.score}  conf={c.confidence}  tier={c.tier}")

    if args.out:
        by_id = {c.image_id: c for c in result.candidates}
        body = {
            "metrics": result.metrics,
            "selected": [
                {"image_id": cid, "score": by_id[cid].score, "confidence": by_id[cid].confidence,
                 "tier": by_id[cid].tier, "escalated": by_id[cid].escalated}
                for cid in result.selected if cid in by_id
            ],
        }
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("wrote result JSON to %s", out_path)
    return 0


def _build_planner(args):
    """LLM planner when ``--llm`` is set (real provider), else the heuristic default.

    ``--llm`` is ignored under ``--mock`` (the mock provider has no planner LLM), so
    the CLI still runs fully offline by default.
    """
    if not args.llm or args.mock:
        return None
    from services.agent.llm_backend import build_curation_llm_planner_from_config

    planner = build_curation_llm_planner_from_config(args.config, model_name=args.llm_model)
    logger.info("using LLM planner (%s) model=%s", type(planner).__name__, args.llm_model or "config")
    return planner


if __name__ == "__main__":
    raise SystemExit(main())
