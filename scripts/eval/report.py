"""Markdown report renderer for Stage3 eval results.

Converts the structured dict produced by ``scripts.eval_stage3.build_report``
into a formatted Markdown document suitable for committing to ``reports/eval/``.

Typical usage::

    from scripts.eval.report import render_markdown
    md = render_markdown(rep, meta={"labels_path": "...", "predictions_path": "..."})
    Path("reports/eval/run_name.md").write_text(md)
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any


def _fmt(x: float | None, nd: int = 3) -> str:
    if x is None:
        return "n/a"
    try:
        if math.isnan(x):
            return "n/a"
    except TypeError:
        return "n/a"
    return f"{x:.{nd}f}"


def _bold_if(val: str, condition: bool) -> str:
    return f"**{val}**" if condition else val


def render_markdown(rep: dict[str, Any], *, meta: dict[str, Any] | None = None) -> str:
    """Render *rep* (from ``build_report``) as a Markdown string."""
    lines: list[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    meta = meta or {}

    labels_path = meta.get("labels_path", "—")
    preds_path = meta.get("predictions_path", "—")

    lines += [
        "# Stage3 Evaluation Report",
        "",
        f"**Generated:** {now}  ",
        f"**Labels:** `{labels_path}`  ",
        f"**Predictions:** `{preds_path}`  ",
        f"**Matched pairs:** {rep.get('matched', '?')}",
        "",
    ]

    if rep.get("labels_unmatched"):
        lines.append(
            f"> ⚠️ {len(rep['labels_unmatched'])} label(s) without a matching prediction "
            f"(e.g. `{rep['labels_unmatched'][0]}`)."
        )
        lines.append("")

    # ------------------------------------------------------------------
    # Overall score
    # ------------------------------------------------------------------
    o = rep.get("overall", {})
    lines += [
        "---",
        "",
        "## Overall Score Agreement (0–100 scale)",
        "",
        f"n = {o.get('n', '?')}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Spearman ρ | {_fmt(o.get('spearman'))} |",
        f"| Pearson r | {_fmt(o.get('pearson'))} |",
        f"| MAE | {_fmt(o.get('mae'), 2)} |",
        f"| RMSE | {_fmt(o.get('rmse'), 2)} |",
        "",
    ]

    sp = o.get("spearman")
    pe = o.get("pearson")
    if sp is not None and pe is not None and not math.isnan(sp) and not math.isnan(pe):
        gap = pe - sp
        if gap > 0.3:
            lines += [
                "> **Note:** Pearson ≫ Spearman (gap = "
                + f"{gap:.2f}) — the model tracks human scores linearly "
                "within the score range but struggles to rank photos "
                "relative to each other. See quintile calibration below.",
                "",
            ]
        elif sp > 0.7:
            lines += [
                "> ✅ Strong rank agreement (Spearman > 0.70) — model ordering "
                "closely matches human preferences.",
                "",
            ]

    # ------------------------------------------------------------------
    # Per-dimension
    # ------------------------------------------------------------------
    dims = rep.get("per_dimension", {})
    dim_rows = [(k, v) for k, v in dims.items() if v.get("n", 0) > 0]
    if dim_rows:
        lines += [
            "---",
            "",
            "## Per-Dimension MAE (0–10 scale)",
            "",
            f"Macro-avg MAE: **{_fmt(rep.get('macro_dim_mae'), 2)}**",
            "",
            "| Dimension | n | MAE | Spearman ρ |",
            "|-----------|---|-----|-----------|",
        ]
        for k, d in dim_rows:
            lines.append(f"| `{k}` | {d['n']} | {_fmt(d.get('mae'), 2)} | {_fmt(d.get('spearman'))} |")
        lines.append("")

    # ------------------------------------------------------------------
    # Calibration A/B
    # ------------------------------------------------------------------
    calib = rep.get("calibration", {})
    if calib:
        helped = sum(1 for c in calib.values() if c.get("delta", 0) < 0)
        lines += [
            "---",
            "",
            "## Calibration A/B (raw → calibrated, per dim)",
            "",
            f"Calibration improved MAE in **{helped}/{len(calib)}** dimensions (`*` = improved).",
            "",
            "| Dimension | n | Raw MAE | Cal MAE | Δ |",
            "|-----------|---|---------|---------|---|",
        ]
        for k, c in calib.items():
            delta = c.get("delta")
            delta_str = _fmt(delta, 2)
            if delta is not None and not math.isnan(delta) and delta < 0:
                delta_str = f"{delta_str} \\*"
            lines.append(
                f"| `{k}` | {c['n']} | {_fmt(c.get('mae_raw'), 2)} "
                f"| {_fmt(c.get('mae_cal'), 2)} | {delta_str} |"
            )
        lines.append("")

    # ------------------------------------------------------------------
    # Bias analysis
    # ------------------------------------------------------------------
    bias = rep.get("bias")
    if bias and bias.get("n", 0) >= 2:
        mb = bias.get("mean_bias", 0)
        direction = "over-scoring" if mb > 0 else "under-scoring"
        lines += [
            "---",
            "",
            "## Score Bias Analysis",
            "",
            f"The model shows a mean bias of **{mb:+.1f} pts** ({direction}). ",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Mean bias (model − human) | {mb:+.1f} pts |",
            f"| Median bias | {bias.get('median_bias', 0):+.1f} pts |",
            f"| % over-scored | {bias.get('pct_overscored', 0):.0f}% |",
            f"| % under-scored | {bias.get('pct_underscored', 0):.0f}% |",
            "",
        ]

    q_calib = rep.get("quintile_calibration")
    if q_calib:
        lines += [
            "### Calibration by Score Quintile",
            "",
            "Each row is an equal-population bin of human scores. "
            "Large positive bias in Q1 = model floors trash photos; "
            "large negative bias in Q5 = model ceiling on best shots.",
            "",
            "| Quintile | Range | n | Human μ | Model μ | Bias |",
            "|----------|-------|---|---------|---------|------|",
        ]
        for q in q_calib:
            bias_val = q.get("bias_mean", 0)
            bias_str = _bold_if(f"{bias_val:+.1f}", abs(bias_val) > 5)
            lines.append(
                f"| Q{q['quintile']} | {q['range']} | {q['n']} "
                f"| {q['human_mean']:.1f} | {q['model_mean']:.1f} | {bias_str} |"
            )
        lines.append("")

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    sel = rep.get("selection", {})
    if sel.get("n", 0) > 0:
        n_pos = sel.get("n_positives", 0)
        pos_rate = n_pos / sel["n"] * 100 if sel["n"] else 0
        lines += [
            "---",
            "",
            "## Selection Performance",
            "",
            f"**Corpus:** {sel['n']} photos | **Human-kept:** {n_pos} ({pos_rate:.0f}%)",
            "",
        ]
        if "mean_pred_keep" in sel:
            gap = sel.get("score_gap", float("nan"))
            lines += [
                "| Group | Mean model score |",
                "|-------|-----------------|",
                f"| keep=True | {_fmt(sel.get('mean_pred_keep'), 1)} |",
                f"| keep=False | {_fmt(sel.get('mean_pred_discard'), 1)} |",
                f"| **Score gap** | **{_fmt(gap, 1)} pts** |",
                "",
            ]
        at_k = sel.get("at_k", [])
        if at_k:
            lines += [
                "| k | Precision@k | Recall@k | Overlap |",
                "|---|-------------|----------|---------|",
            ]
            for r in at_k:
                lines.append(
                    f"| {r['k']} | {_fmt(r.get('precision'), 2)} "
                    f"| {_fmt(r.get('recall'), 2)} | {r.get('overlap', '?')} |"
                )
            lines.append("")

    lines += [
        "---",
        "",
        "*Generated by `scripts/eval_stage3.py run --output-md`*",
        "",
    ]
    return "\n".join(lines)
