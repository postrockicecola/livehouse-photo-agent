"""Human-readable pipeline log blocks (verdict lines, photographer summary, edit hints)."""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping

from utils.config_loader import ConfigLoader
from utils.stage3_dimensions import STAGE3_DIM_LABELS, STAGE3_DIM_ORDER
from engine.operators.stage2_prefilter import hamming_64, phash_dedup_settings

_IMG_EXT = {".jpg", ".jpeg", ".png"}


def stage3_fallback_flag(result: Dict[str, Any]) -> bool:
    """True when Stage3 used degraded queue/model path or neutral fallback scoring."""
    if result.get("error"):
        return True
    sm = result.get("stage3_meta") or {}
    out = str(sm.get("outcome") or "")
    if bool(result.get("inference_degraded")):
        return True
    return out in ("fallback_defaults", "degraded_inference")


def pipeline_logs_compact(config: Mapping[str, Any] | None) -> bool:
    """True when ``processing.delivery_quiet_logs`` requests minimal stdout (delivery mode)."""
    if not config:
        return False
    return bool((config.get("processing") or {}).get("delivery_quiet_logs"))


def classification_tier_from_score(score: float | None, config: Dict[str, Any]) -> str:
    c = ConfigLoader.get_classification_thresholds(config)
    try:
        s = float(score if score is not None else 0.0)
    except (TypeError, ValueError):
        s = 0.0
    if s >= float(c["best_threshold"]):
        return "BEST"
    if s >= float(c["keep_threshold"]):
        return "KEEP"
    return "TRASH"


def pick_primary_log_text(*, en: str, zh: str) -> str:
    en = (en or "").strip()
    zh = (zh or "").strip()
    if en:
        return en
    return zh


def one_sentence_verdict(result: Dict[str, Any], tier: str) -> str:
    rb = result.get("reason_bilingual")
    wb = result.get("weakness_bilingual")
    reason = ""
    if isinstance(rb, dict):
        reason = pick_primary_log_text(en=str(rb.get("en") or ""), zh=str(rb.get("zh") or ""))
    if not reason:
        reason = str(result.get("reason") or "").strip()
    weakness = ""
    if isinstance(wb, dict):
        weakness = pick_primary_log_text(en=str(wb.get("en") or ""), zh=str(wb.get("zh") or ""))
    if not weakness:
        weakness = str(result.get("weakness") or "").strip()
    sm = result.get("stage3_meta") or {}
    if sm.get("outcome") == "skipped_stage3_gating":
        return (weakness or reason or "Skipped deep analysis by Stage 2 gate; heuristic score only.")[:280]
    if tier == "BEST":
        return (reason or "Strong deliverable across the rubric.")[:280]
    if tier == "KEEP":
        return (reason or weakness or "Acceptable keeper with room to polish.")[:280]
    return (weakness or reason or "Below keep threshold; discard unless irreplaceable.")[:280]


def format_editing_suggestion_lines(suggestions: Any, *, max_total: int = 3) -> List[str]:
    lines: List[str] = []
    raw = suggestions if isinstance(suggestions, list) else []
    texts: List[str] = []
    for tip in raw[:max_total]:
        if isinstance(tip, dict):
            t = pick_primary_log_text(en=str(tip.get("en") or ""), zh=str(tip.get("zh") or ""))
        else:
            t = str(tip).strip()
        if t:
            texts.append(t)
    if not texts:
        return lines
    quick = texts[:2]
    adv = texts[2:3]
    if quick:
        lines.append("   🎨 Quick Fix")
        for t in quick:
            lines.append(f"      · {t[:280]}")
    if adv:
        lines.append("   🧪 Advanced")
        for t in adv:
            lines.append(f"      · {t[:280]}")
    return lines


def _is_fast_dimension_placeholder(result: Dict[str, Any]) -> bool:
    sr = result.get("stage3_result")
    if not isinstance(sr, dict) or sr.get("mode") != "fast":
        return False
    dims = sr.get("dimensions") or {}
    return all(dims.get(k) is None for k in STAGE3_DIM_ORDER)


def format_dimension_score_lines(result: Dict[str, Any]) -> List[str]:
    if _is_fast_dimension_placeholder(result):
        return []
    dims: Dict[str, Any] = {}
    sr_raw = result.get("stage3_result")
    if isinstance(sr_raw, dict) and isinstance(sr_raw.get("dimensions"), dict):
        dims = sr_raw["dimensions"]
    if not dims:
        dims = result.get("dimensions") or {}
    comments = result.get("dimension_comments") or {}
    lines: List[str] = []
    for key in STAGE3_DIM_ORDER:
        label = STAGE3_DIM_LABELS.get(key, key)
        raw = dims.get(key)
        if raw is None:
            continue
        try:
            score_s = f"{float(raw):.1f}"
        except (TypeError, ValueError):
            score_s = str(raw)
        note = comments.get(key, "")
        note_s = ""
        if isinstance(note, dict):
            note_s = pick_primary_log_text(en=str(note.get("en") or ""), zh=str(note.get("zh") or ""))
        elif note:
            note_s = str(note).strip()
        if note_s:
            lines.append(f"      · {label} {score_s}/10 — {note_s[:200]}")
        else:
            lines.append(f"      · {label} {score_s}/10")
    return lines


def build_stage3_image_log_lines(
    file_name: str,
    result: Dict[str, Any],
    *,
    progress: str,
    config: Dict[str, Any],
    compact: bool = False,
) -> List[str]:
    tier = classification_tier_from_score(result.get("score"), config)
    fs = result.get("score")
    try:
        score_disp = f"{float(fs):.1f}" if fs is not None else "—"
    except (TypeError, ValueError):
        score_disp = str(fs) if fs is not None else "—"

    if compact:
        return [f"[{progress}] {file_name} | {score_disp} | {tier}"]

    tier_icon = "⭐" if tier == "BEST" else ("✅" if tier == "KEEP" else "❌")
    lines: List[str] = [
        f"[{progress}] {tier_icon} {file_name} | Score: {score_disp} | {tier}",
        f"   → Verdict: {one_sentence_verdict(result, tier)}",
    ]

    dim_lines = format_dimension_score_lines(result)
    if _is_fast_dimension_placeholder(result):
        lines.append("   📐 Dimensions: fast estimation")
    elif dim_lines:
        lines.append("   📐 Dimensions:")
        lines.extend(dim_lines)

    rb = result.get("reason_bilingual")
    if isinstance(rb, dict) and (rb.get("en") or rb.get("zh")):
        h = pick_primary_log_text(en=str(rb.get("en") or ""), zh=str(rb.get("zh") or ""))
        if h:
            lines.append(f"   ✨ Highlight: {h[:240]}")
    elif (result.get("reason") or "").strip():
        lines.append(f"   ✨ Highlight: {str(result.get('reason'))[:240]}")

    wb = result.get("weakness_bilingual")
    if isinstance(wb, dict) and (wb.get("en") or wb.get("zh")):
        w = pick_primary_log_text(en=str(wb.get("en") or ""), zh=str(wb.get("zh") or ""))
        if w:
            lines.append(f"   ⚠️ Gap: {w[:240]}")
    elif (result.get("weakness") or "").strip():
        lines.append(f"   ⚠️ Gap: {str(result.get('weakness'))[:240]}")

    tags = result.get("tags") or []
    if tags:
        lines.append(f"   · Tags: {', '.join(str(t) for t in tags[:12])}")

    edit_lines = format_editing_suggestion_lines(result.get("editing_suggestions"))
    if edit_lines:
        lines.append("   ✂️ Edits:")
        lines.extend(edit_lines)
    return lines


def log_stage3_image_block(
    logger: logging.Logger,
    file_name: str,
    result: Dict[str, Any],
    progress: str,
    config: Dict[str, Any],
) -> None:
    cmp = pipeline_logs_compact(config)
    logger.info(
        "\n".join(build_stage3_image_log_lines(file_name, result, progress=progress, config=config, compact=cmp))
    )


def build_early_reject_log_lines(
    file_name: str,
    result: Dict[str, Any],
    *,
    progress: str,
    config: Dict[str, Any],
    route_note: str,
) -> List[str]:
    tier = classification_tier_from_score(result.get("score"), config)
    fs = result.get("score")
    try:
        score_disp = f"{float(fs):.1f}" if fs is not None else "—"
    except (TypeError, ValueError):
        score_disp = str(fs) if fs is not None else "—"
    if pipeline_logs_compact(config):
        return [f"[{progress}] {file_name} | {score_disp} | {tier} | {route_note}"]
    icon = "⭐" if tier == "BEST" else ("✅" if tier == "KEEP" else "❌")
    return [
        f"[{progress}] {icon} {file_name} | Score: {score_disp} | {tier}",
        f"   → Verdict: {one_sentence_verdict(result, tier)}",
        f"   📍 Route: {route_note}",
    ]


def _count_images(folder: Path | None) -> int:
    if folder is None or not folder.exists():
        return 0
    return sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _IMG_EXT)


def _folder_image_basenames(folders: Dict[str, Path]) -> set[str]:
    """Basenames of images currently in best / keep / trash (current roll on disk)."""
    names: set[str] = set()
    for folder in folders.values():
        if folder is None or not folder.exists():
            continue
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() in _IMG_EXT:
                names.add(p.name)
    return names


def collect_photographer_summary_data(
    *,
    folders: Dict[str, Path],
    log_file: Path,
    weak_dim_threshold: float = 5.0,
    config: Mapping[str, Any] | None = None,
    pipeline_stats: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    n_best = _count_images(folders.get("best"))
    n_keep = _count_images(folders.get("keep"))
    n_trash = _count_images(folders.get("trash"))
    total = n_best + n_keep + n_trash

    entries: List[Dict[str, Any]] = []
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    allowed_names = _folder_image_basenames(folders)
    rankable_by_name: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        pk = str(e.get("file_name") or e.get("image") or "").strip()
        if pk and pk in allowed_names:
            rankable_by_name[pk] = e
    rankable = list(rankable_by_name.values())

    def _score(e: Dict[str, Any]) -> float:
        try:
            return float(e.get("overall_score") if e.get("overall_score") is not None else e.get("score") or 0)
        except (TypeError, ValueError):
            return 0.0

    sorted_entries = sorted(rankable, key=_score, reverse=True)

    ps = phash_dedup_settings(config or {})
    max_h = int(ps.get("max_hamming", 10) or 10)

    unique: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()
    kept_phashes: list[int] = []
    for e in sorted_entries:
        pk = str(e.get("file_name") or e.get("image") or "").strip()
        if pk in seen_paths:
            continue
        dbg_raw = e.get("debug_info")
        dbg = dbg_raw if isinstance(dbg_raw, dict) else {}
        ph = int((dbg.get("phash") or 0) or 0)
        if ph > 0 and kept_phashes:
            if any(hamming_64(ph, kp) <= max_h for kp in kept_phashes):
                continue
        seen_paths.add(pk)
        if ph > 0:
            kept_phashes.append(ph)
        unique.append(e)

    roll_before = len(sorted_entries)
    roll_after = len(unique)
    roll_removed = roll_before - roll_after

    if pipeline_stats is not None and pipeline_stats.get("topk_dedup_before") is not None:
        topk_dedup_before = int(pipeline_stats["topk_dedup_before"])
        topk_dedup_after = int(pipeline_stats["topk_dedup_after"])
        topk_dedup_removed = int(pipeline_stats["topk_dedup_removed"])
    else:
        topk_dedup_before = roll_before
        topk_dedup_after = roll_after
        topk_dedup_removed = roll_removed
        if topk_dedup_removed > topk_dedup_before:
            logging.getLogger(__name__).warning(
                "topk_dedup invariant violated: removed_count=%s input_count=%s",
                topk_dedup_removed,
                topk_dedup_before,
            )

    top3 = unique[:3]
    top3_fmt = [
        f"{e.get('file_name') or e.get('image')} "
        f"({float(e.get('overall_score') if e.get('overall_score') is not None else e.get('score') or 0):.1f})"
        for e in top3
    ]

    weak_counter: Counter[str] = Counter()
    for e in entries:
        dims = e.get("dimensions") or {}
        if not dims:
            continue
        for k, v in dims.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv < weak_dim_threshold:
                weak_counter[k] += 1

    weak_patterns = [(STAGE3_DIM_LABELS.get(k, k), c) for k, c in weak_counter.most_common(8) if c > 0]

    return {
        "total": total,
        "n_best": n_best,
        "n_keep": n_keep,
        "n_trash": n_trash,
        "top3": top3_fmt,
        "weak_patterns": weak_patterns,
        "topk_dedup_before": topk_dedup_before,
        "topk_dedup_after": topk_dedup_after,
        "topk_dedup_removed": topk_dedup_removed,
    }


def log_photographer_summary(
    logger: logging.Logger,
    *,
    folders: Dict[str, Path],
    log_file: Path,
    compact: bool = False,
    config: Mapping[str, Any] | None = None,
    pipeline_stats: Mapping[str, Any] | None = None,
) -> None:
    data = collect_photographer_summary_data(
        folders=folders, log_file=log_file, config=config, pipeline_stats=pipeline_stats
    )
    if compact:
        logger.info(
            "Roll: %s | Best %s | Keep %s | Trash %s",
            data["total"],
            data["n_best"],
            data["n_keep"],
            data["n_trash"],
        )
        logger.info(
            "topk_dedup before_dedup=%s after_dedup=%s removed_count=%s",
            data.get("topk_dedup_before", 0),
            data.get("topk_dedup_after", 0),
            data.get("topk_dedup_removed", 0),
        )
        if data["top3"]:
            logger.info("Top: %s", " · ".join(data["top3"][:5]))
        return
    logger.info("   ─── Photographer summary ───")
    logger.info(
        "   📷 Roll: %s total | ⭐ Best: %s | ✅ Keep: %s | ❌ Trash: %s",
        data["total"],
        data["n_best"],
        data["n_keep"],
        data["n_trash"],
    )
    logger.info(
        "topk_dedup before_dedup=%s after_dedup=%s removed_count=%s",
        data.get("topk_dedup_before", 0),
        data.get("topk_dedup_after", 0),
        data.get("topk_dedup_removed", 0),
    )
    if data["top3"]:
        logger.info("   🏆 Top 3: %s", " · ".join(data["top3"]))
    if data["weak_patterns"]:
        parts = [f"{label} ×{n}" for label, n in data["weak_patterns"][:6]]
        logger.info("   📉 Weak dimensions (score under 5, across the roll): %s", ", ".join(parts))
    else:
        logger.info("   📉 Weak dimensions: (no aggregate — missing rubric scores in audit log)")
