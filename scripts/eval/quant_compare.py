#!/usr/bin/env python3
"""Compare model *quantization* arms on quality **and** serving cost in one table.

Quantization (INT8/INT4 via AWQ/GPTQ, KV-cache quant, …) is a cost lever: it trades a
(hopefully small) quality loss for higher throughput / lower $/1k. To make that trade
legible this tool joins the two measurements the repo already produces, per arm:

- **quality** — a ``scripts/eval_stage3.py`` report JSON (``overall.spearman`` / ``mae``
  vs human labels on the fixed eval set), so "did quantization hurt ranking?" is answered
  on ground truth, not vibes.
- **serving** — a ``scripts/load_test.py`` report JSON (``throughput_rps`` /
  ``latency_p99_ms`` / ``decode_tokens_per_sec`` / ``est_cost_per_1k_usd`` at a chosen
  concurrency), so "what did we save?" is answered on the same harness for every arm.

It then prints each arm and its delta vs a baseline arm (default: the first one), e.g.
``int4: Spearman −0.01, $/1k −38%`` — the headline quantization-cost result.

This script is hardware-free: it only *reads* existing report JSONs. Produce those first
on whatever machine has the GPU (one ``eval_stage3`` + one ``load_test`` per arm), then
join them here. ``--simulate-example`` writes a synthetic fp16-vs-int4 pair for a smoke
test / to show the output shape without any GPU.

Example::

    python scripts/eval/quant_compare.py \
      --arm fp16:reports/eval/qwen2vl_fp16.json:reports/loadtest/qwen2vl_fp16.json \
      --arm int4:reports/eval/qwen2vl_awq_int4.json:reports/loadtest/qwen2vl_awq_int4.json \
      --baseline fp16 --concurrency max \
      --out reports/eval/quant_compare.json --out-md reports/eval/quant_compare.md
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUANT_COMPARE_SCHEMA_VERSION = "1"


# --------------------------------------------------------------------------- parsing


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_arm_spec(spec: str) -> tuple[str, str, Optional[str]]:
    """``label:quality.json[:loadtest.json]`` → (label, quality_path, loadtest_path|None).

    Windows-style drive letters are not supported in the spec (use forward-slash
    relative paths from the repo root, which is how the report tools write them).
    """
    parts = spec.split(":")
    if len(parts) < 2:
        raise ValueError(f"arm spec must be 'label:quality.json[:loadtest.json]', got {spec!r}")
    label = parts[0].strip()
    quality = parts[1].strip()
    loadtest = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None
    if not label or not quality:
        raise ValueError(f"arm spec needs a non-empty label and quality path, got {spec!r}")
    return label, quality, loadtest


def _pick_scenario(loadtest_doc: dict[str, Any], concurrency: str) -> Optional[dict[str, Any]]:
    """Select one load-test scenario row by concurrency ('max' = highest throughput)."""
    scenarios = loadtest_doc.get("scenarios") or []
    if not scenarios:
        return None
    if concurrency == "max":
        return max(scenarios, key=lambda s: float(s.get("throughput_rps") or 0.0))
    want = int(concurrency)
    for s in scenarios:
        if int(s.get("concurrency") or -1) == want:
            return s
    return None


def _num(v: Any) -> Optional[float]:
    """Coerce to float, mapping None/NaN to None so deltas stay honest."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


# --------------------------------------------------------------------- summarize / diff


def summarize_arm(
    label: str,
    quality_doc: dict[str, Any],
    loadtest_doc: Optional[dict[str, Any]],
    *,
    concurrency: str = "max",
) -> dict[str, Any]:
    """Flatten one arm's quality + serving report into a comparable record."""
    overall = quality_doc.get("overall") or {}
    row: dict[str, Any] = {
        "arm": label,
        "spearman": _num(overall.get("spearman")),
        "pearson": _num(overall.get("pearson")),
        "mae": _num(overall.get("mae")),
        "rmse": _num(overall.get("rmse")),
        "quality_n": overall.get("n"),
        # serving fields default to None until a load-test doc is supplied
        "concurrency": None,
        "throughput_rps": None,
        "latency_p99_ms": None,
        "decode_tokens_per_sec": None,
        "est_cost_per_1k_usd": None,
        "cost_basis": None,
    }
    if loadtest_doc is not None:
        sc = _pick_scenario(loadtest_doc, concurrency)
        if sc is not None:
            row.update(
                concurrency=sc.get("concurrency"),
                throughput_rps=_num(sc.get("throughput_rps")),
                latency_p99_ms=_num(sc.get("latency_p99_ms")),
                decode_tokens_per_sec=_num(sc.get("decode_tokens_per_sec")),
                est_cost_per_1k_usd=_num(sc.get("est_cost_per_1k_usd")),
                cost_basis=sc.get("cost_basis"),
            )
    return row


def _pct_change(new: Optional[float], base: Optional[float]) -> Optional[float]:
    if new is None or base is None or base == 0:
        return None
    return round((new - base) / abs(base) * 100.0, 2)


def _ratio(new: Optional[float], base: Optional[float]) -> Optional[float]:
    if new is None or base is None or base == 0:
        return None
    return round(new / base, 3)


def diff_arm(arm: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """Deltas of one arm vs the baseline arm (the quantization trade-off, signed)."""
    d_spearman = (
        round(arm["spearman"] - base["spearman"], 4)
        if arm["spearman"] is not None and base["spearman"] is not None
        else None
    )
    d_mae = (
        round(arm["mae"] - base["mae"], 4)
        if arm["mae"] is not None and base["mae"] is not None
        else None
    )
    # Cost *savings*: positive % = cheaper than baseline.
    cost_change = _pct_change(arm["est_cost_per_1k_usd"], base["est_cost_per_1k_usd"])
    return {
        "arm": arm["arm"],
        "d_spearman": d_spearman,
        "d_mae": d_mae,
        "throughput_speedup_x": _ratio(arm["throughput_rps"], base["throughput_rps"]),
        "decode_speedup_x": _ratio(arm["decode_tokens_per_sec"], base["decode_tokens_per_sec"]),
        "p99_change_pct": _pct_change(arm["latency_p99_ms"], base["latency_p99_ms"]),
        "cost_savings_pct": (None if cost_change is None else round(-cost_change, 2)),
    }


def compare(arms: list[dict[str, Any]], baseline_label: str) -> dict[str, Any]:
    by_label = {a["arm"]: a for a in arms}
    if baseline_label not in by_label:
        raise ValueError(f"baseline {baseline_label!r} not among arms {list(by_label)}")
    base = by_label[baseline_label]
    deltas = [diff_arm(a, base) for a in arms if a["arm"] != baseline_label]
    return {
        "schema_version": QUANT_COMPARE_SCHEMA_VERSION,
        "tool": "quant_compare",
        "baseline": baseline_label,
        "arms": arms,
        "deltas": deltas,
    }


# ------------------------------------------------------------------------------ render


def _fmt(v: Optional[float], spec: str = ".4f", dash: str = "-") -> str:
    return format(v, spec) if v is not None else dash


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Quantization comparison (quality vs serving cost)",
        "",
        f"Baseline arm: `{report['baseline']}`.",
        "",
        "| arm | Spearman | MAE | concurrency | rps | p99 ms | decode tok/s | $/1k |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in report["arms"]:
        lines.append(
            f"| {a['arm']} | {_fmt(a['spearman'], '.3f')} | {_fmt(a['mae'], '.2f')} | "
            f"{a['concurrency'] if a['concurrency'] is not None else '-'} | "
            f"{_fmt(a['throughput_rps'], '.2f')} | "
            f"{int(a['latency_p99_ms']) if a['latency_p99_ms'] is not None else '-'} | "
            f"{_fmt(a['decode_tokens_per_sec'], '.1f')} | {_fmt(a['est_cost_per_1k_usd'], '.4f')} |"
        )
    lines += [
        "",
        f"### Deltas vs `{report['baseline']}`",
        "",
        "| arm | ΔSpearman | ΔMAE | throughput | decode | p99 | cost savings |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in report["deltas"]:
        lines.append(
            f"| {d['arm']} | {_fmt(d['d_spearman'], '+.4f')} | {_fmt(d['d_mae'], '+.3f')} | "
            f"{(str(d['throughput_speedup_x']) + 'x') if d['throughput_speedup_x'] is not None else '-'} | "
            f"{(str(d['decode_speedup_x']) + 'x') if d['decode_speedup_x'] is not None else '-'} | "
            f"{_fmt(d['p99_change_pct'], '+.1f') + '%' if d['p99_change_pct'] is not None else '-'} | "
            f"{_fmt(d['cost_savings_pct'], '+.1f') + '%' if d['cost_savings_pct'] is not None else '-'} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_table(report: dict[str, Any]) -> str:
    out = [f"\n=== quantization compare (baseline={report['baseline']}) ===",
           f"{'arm':<10} {'spearman':>9} {'mae':>7} {'rps':>8} {'p99ms':>7} {'$/1k':>9}"]
    for a in report["arms"]:
        out.append(
            f"{a['arm']:<10} {_fmt(a['spearman'], '>9.3f')} {_fmt(a['mae'], '>7.2f')} "
            f"{_fmt(a['throughput_rps'], '>8.2f')} "
            f"{(format(int(a['latency_p99_ms']), '>7') if a['latency_p99_ms'] is not None else '>7'.format('-')):>7} "
            f"{_fmt(a['est_cost_per_1k_usd'], '>9.4f')}"
        )
    out.append(f"\n--- deltas vs {report['baseline']} ---")
    for d in report["deltas"]:
        save = f"{d['cost_savings_pct']:+.1f}%" if d['cost_savings_pct'] is not None else "-"
        spd = f"{d['throughput_speedup_x']}x" if d['throughput_speedup_x'] is not None else "-"
        out.append(
            f"{d['arm']:<10} dSpearman={_fmt(d['d_spearman'], '+.4f')} "
            f"dMAE={_fmt(d['d_mae'], '+.3f')} throughput={spd} cost_savings={save}"
        )
    return "\n".join(out)


# ----------------------------------------------------------------------- example data


def _simulated_example_arms() -> list[dict[str, Any]]:
    """Synthetic fp16 vs AWQ-int4 pair (illustrative only — not real measurements)."""
    fp16_quality = {"overall": {"n": 250, "spearman": 0.368, "pearson": 0.803, "mae": 6.50, "rmse": 10.08}}
    int4_quality = {"overall": {"n": 250, "spearman": 0.359, "pearson": 0.791, "mae": 6.71, "rmse": 10.42}}
    fp16_load = {"scenarios": [
        {"concurrency": 8, "throughput_rps": 12.0, "latency_p99_ms": 740,
         "decode_tokens_per_sec": 900.0, "est_cost_per_1k_usd": 0.0278, "cost_basis": "gpu_hourly"},
    ]}
    int4_load = {"scenarios": [
        {"concurrency": 8, "throughput_rps": 19.4, "latency_p99_ms": 520,
         "decode_tokens_per_sec": 1460.0, "est_cost_per_1k_usd": 0.0172, "cost_basis": "gpu_hourly"},
    ]}
    return [
        summarize_arm("fp16", fp16_quality, fp16_load, concurrency="max"),
        summarize_arm("int4_awq", int4_quality, int4_load, concurrency="max"),
    ]


# ------------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--arm",
        action="append",
        default=[],
        metavar="label:quality.json[:loadtest.json]",
        help="An arm: a label, its eval_stage3 quality JSON, and optionally its load_test JSON.",
    )
    ap.add_argument("--baseline", default=None, help="Arm label to diff against (default: first arm).")
    ap.add_argument("--concurrency", default="max", help="Load-test concurrency to report ('max' or an int).")
    ap.add_argument("--out", default=None, help="Write the comparison JSON here.")
    ap.add_argument("--out-md", default=None, help="Write a Markdown table here.")
    ap.add_argument("--json", action="store_true", help="Print JSON to stdout instead of a table.")
    ap.add_argument("--simulate-example", action="store_true", help="Use a synthetic fp16/int4 pair (no files).")
    args = ap.parse_args()

    if args.simulate_example:
        arms = _simulated_example_arms()
    else:
        if not args.arm:
            ap.error("provide at least one --arm (or --simulate-example)")
        arms = []
        for spec in args.arm:
            label, quality_path, loadtest_path = parse_arm_spec(spec)
            quality_doc = _load_json(quality_path)
            loadtest_doc = _load_json(loadtest_path) if loadtest_path else None
            arms.append(summarize_arm(label, quality_doc, loadtest_doc, concurrency=args.concurrency))

    baseline = args.baseline or arms[0]["arm"]
    report = compare(arms, baseline)

    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.out_md:
        p = Path(args.out_md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_markdown(report) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
