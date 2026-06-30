"""Label schema, prediction loading, and filename joining for Stage3 eval.

Ground-truth label file is JSONL (one object per line). Scales match the
pipeline: ``overall`` is 0-100, per-dimension scores are 0-10.

Example label line::

    {"file": "DSC06002.jpg", "overall": 82,
     "dims": {"focus_sharpness": 7, "moment_peak": 9},
     "keep": true, "notes": "peak jump, slight clip"}

All fields except ``file`` are optional:
- ``overall`` missing  -> excluded from overall correlation metrics.
- ``dims`` partial      -> only present dimensions are scored.
- ``keep`` missing      -> excluded from selection precision/recall.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from utils.stage3_dimensions import STAGE3_DIM_KEYS

DIM_KEYS: tuple[str, ...] = STAGE3_DIM_KEYS

# Suffixes appended by render/export/preview steps; stripped when matching names.
_STRIP_SUFFIXES = ("_rendered", "_out", "_film", "_preview", "_thumb", "_overlay", "_mask")


def normalize_name(name: str | None) -> str:
    """Canonical join key: basename, no extension, known suffixes removed, lowercased."""
    if not name:
        return ""
    stem = Path(str(name)).name
    dot = stem.rfind(".")
    if dot > 0:
        stem = stem[:dot]
    stem = stem.lower()
    changed = True
    while changed:
        changed = False
        for suf in _STRIP_SUFFIXES:
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
                changed = True
    return stem


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _clean_dims(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k in DIM_KEYS:
        v = _num(raw.get(k))
        if v is not None:
            out[k] = v
    return out


@dataclass
class Label:
    file: str
    overall: Optional[float] = None
    dims: dict[str, float] = field(default_factory=dict)
    keep: Optional[bool] = None
    notes: str = ""

    @property
    def key(self) -> str:
        return normalize_name(self.file)


@dataclass
class Prediction:
    file: str
    overall: Optional[float] = None
    dims_cal: dict[str, float] = field(default_factory=dict)
    dims_raw: dict[str, float] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return normalize_name(self.file)


def load_labels(path: str | Path) -> list[Label]:
    out: list[Label] = []
    with open(path, "r", encoding="utf-8") as fh:
        for ln, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{ln}: invalid JSON ({e})") from e
            f = rec.get("file") or rec.get("path")
            if not f:
                raise ValueError(f"{path}:{ln}: missing 'file'")
            keep = rec.get("keep")
            out.append(
                Label(
                    file=str(f),
                    overall=_num(rec.get("overall")),
                    dims=_clean_dims(rec.get("dims")),
                    keep=bool(keep) if isinstance(keep, bool) else None,
                    notes=str(rec.get("notes") or ""),
                )
            )
    return out


def _extract_prediction(rec: dict[str, Any]) -> Prediction:
    sr = rec.get("stage3_result") if isinstance(rec.get("stage3_result"), dict) else {}
    postp = rec.get("stage3_postprocess") if isinstance(rec.get("stage3_postprocess"), dict) else {}

    file = rec.get("file") or rec.get("path") or ""
    overall = _num(rec.get("score"))
    if overall is None:
        overall = _num(rec.get("overall_score"))  # pipeline analysis_results.json field
    if overall is None:
        overall = _num(sr.get("score"))

    dims_cal = _clean_dims(rec.get("dimensions"))
    if not dims_cal:
        dims_cal = _clean_dims(sr.get("dimensions"))

    dims_raw = _clean_dims(rec.get("dimensions_raw"))
    if not dims_raw:
        dims_raw = _clean_dims(postp.get("dimensions_raw"))

    return Prediction(file=str(file), overall=overall, dims_cal=dims_cal, dims_raw=dims_raw)


def load_predictions(path: str | Path) -> list[Prediction]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        # tolerate {"results": [...]} or {file: record} shapes
        if isinstance(data.get("results"), list):
            data = data["results"]
        else:
            data = list(data.values())
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of prediction records")
    return [_extract_prediction(r) for r in data if isinstance(r, dict)]


@dataclass
class Joined:
    pairs: list[tuple[Label, Prediction]]
    labels_only: list[str]
    preds_only: list[str]

    @property
    def n_matched(self) -> int:
        return len(self.pairs)


def join_labels_predictions(labels: Iterable[Label], preds: Iterable[Prediction]) -> Joined:
    pred_by_key: dict[str, Prediction] = {}
    for p in preds:
        if p.key:
            pred_by_key.setdefault(p.key, p)
    pairs: list[tuple[Label, Prediction]] = []
    labels_only: list[str] = []
    used: set[str] = set()
    for lb in labels:
        p = pred_by_key.get(lb.key)
        if p is None:
            labels_only.append(lb.file)
            continue
        pairs.append((lb, p))
        used.add(lb.key)
    preds_only = [p.file for k, p in pred_by_key.items() if k not in used]
    return Joined(pairs=pairs, labels_only=labels_only, preds_only=preds_only)


def make_label_template(preds: Iterable[Prediction], *, prefill: bool) -> list[dict[str, Any]]:
    """Build label skeletons from predictions.

    ``prefill=False`` (default) leaves scores null to avoid anchoring bias.
    ``prefill=True`` seeds AI values for faster correction-style labeling.
    """
    rows: list[dict[str, Any]] = []
    for p in preds:
        if prefill:
            dims = {k: p.dims_cal.get(k) for k in DIM_KEYS}
            overall = p.overall
        else:
            dims = {k: None for k in DIM_KEYS}
            overall = None
        rows.append({"file": Path(p.file).name or p.file, "overall": overall, "dims": dims, "keep": None, "notes": ""})
    return rows
