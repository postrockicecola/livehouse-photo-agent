"""Dataset registry: hydrate ``labels.jsonl`` + ``manifest.json`` → ``golden_item.v1``.

Usage::

    python -m quality.dataset \\
        --labels data/eval/labels.jsonl \\
        --manifest data/eval/manifest.json \\
        --out quality/store/datasets/golden_apr_jul_2026@0.2.0/items.jsonl

    from quality.dataset import hydrate_golden_items, load_dataset_registry
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from scripts.eval.labels import normalize_name
from utils.stage3_dimensions import STAGE3_DIM_KEYS

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_REGISTRY = _REPO / "data" / "eval" / "dataset_registry.json"
_DEFAULT_SPLITS = ("core",)
_VALID_SPLITS = frozenset(
    {"smoke", "core", "hard", "regression", "agent_chat"}
)


def _resolve(path: str | Path | None) -> Optional[Path]:
    if not path:
        return None
    p = Path(path)
    if p.is_file():
        return p
    alt = _REPO / path
    return alt if alt.is_file() else p


def load_dataset_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load optional registry metadata (name, version, smoke policy)."""
    reg_path = _resolve(path) if path else _DEFAULT_REGISTRY
    if reg_path is None or not reg_path.is_file():
        return {
            "name": "golden_apr_jul_2026",
            "version": "0.2.0",
            "labels": "data/eval/labels.jsonl",
            "manifest": "data/eval/manifest.json",
            "default_splits": list(_DEFAULT_SPLITS),
            "smoke_limit": 16,
        }
    raw = json.loads(reg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"dataset registry must be object: {reg_path}")
    return raw


def load_media_index(manifest_path: str | Path) -> dict[str, dict[str, Any]]:
    """Index ``manifest.json`` items by ``normalize_name(file)``."""
    path = _resolve(manifest_path) or Path(manifest_path)
    if not path.is_file():
        raise FileNotFoundError(f"dataset manifest not found: {manifest_path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected items list")
    index: dict[str, dict[str, Any]] = {}
    for rec in items:
        if not isinstance(rec, dict):
            continue
        file_name = rec.get("file") or rec.get("path")
        if not file_name:
            continue
        key = normalize_name(str(file_name))
        if not key:
            continue
        # First wins — same join rule as labels↔predictions.
        index.setdefault(key, rec)
    return index


def _clean_label(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Build ``label.v1``; null dims stay null (unlabeled), never coerced to 0."""
    label: dict[str, Any] = {"label_schema": "stage3_dims_v1"}
    overall = raw.get("overall")
    if overall is not None and not isinstance(overall, bool):
        try:
            label["overall"] = float(overall)
        except (TypeError, ValueError):
            pass
    dims_in = raw.get("dims") if isinstance(raw.get("dims"), dict) else {}
    dims_out: dict[str, Any] = {}
    for k in STAGE3_DIM_KEYS:
        if k not in dims_in:
            continue
        v = dims_in.get(k)
        if v is None:
            dims_out[k] = None
            continue
        if isinstance(v, bool):
            continue
        try:
            dims_out[k] = float(v)
        except (TypeError, ValueError):
            continue
    if dims_out:
        label["dims"] = dims_out
    keep = raw.get("keep")
    if isinstance(keep, bool):
        label["keep"] = keep
    elif keep is None and "keep" in raw:
        label["keep"] = None
    cat = raw.get("category")
    if cat in ("best", "keep", "trash"):
        label["category"] = cat
    notes = raw.get("notes")
    if notes is not None:
        label["notes"] = str(notes)
    return label


def _item_id(file_name: str, session: str | None, file_key: str) -> str:
    stem = Path(file_name).stem
    if session:
        # Prefer session__stem when file does not already embed the session.
        if stem.startswith(str(session).replace("-", "")) or "__" in stem:
            return stem
        return f"{session}__{stem}"
    return file_key or stem


def _assign_splits(
    *,
    file_key: str,
    index: int,
    default_splits: Iterable[str],
    smoke_keys: set[str],
    smoke_limit: int,
    hard_keys: set[str],
) -> list[str]:
    splits = {str(s) for s in default_splits if str(s) in _VALID_SPLITS}
    if not splits:
        splits.add("core")
    if file_key in smoke_keys or (smoke_limit > 0 and index < smoke_limit):
        splits.add("smoke")
    if file_key in hard_keys:
        splits.add("hard")
    return sorted(splits)


def hydrate_golden_item(
    rec: Mapping[str, Any],
    media: Mapping[str, Any] | None,
    *,
    splits: list[str],
    now: str | None = None,
    imported_from: str = "data/eval/labels.jsonl",
) -> dict[str, Any]:
    """Hydrate one labels.jsonl row into ``golden_item.v1``."""
    file_name = str(rec.get("file") or rec.get("path") or "")
    if not file_name:
        raise ValueError("label row missing 'file'")
    file_key = normalize_name(file_name)
    media = media or {}
    content_hash = media.get("sha256") or media.get("content_hash")
    if not (isinstance(content_hash, str) and len(content_hash) == 64):
        raise ValueError(f"missing content_hash for {file_name!r} (join via manifest)")
    content_hash = content_hash.lower()
    session = media.get("session")
    if session is not None:
        session = str(session)
    ts = now or datetime.now(timezone.utc).isoformat()
    item: dict[str, Any] = {
        "schema_version": "golden_item.v1",
        "item_id": _item_id(file_name, session, file_key),
        "file": Path(file_name).name or file_name,
        "file_key": file_key,
        "content_hash": content_hash,
        "label": _clean_label(rec),
        "splits": splits,
        "provenance": {
            "origin": "imported",
            "label_version": "labels_v0",
            "imported_from": imported_from,
        },
        "created_at": ts,
        "updated_at": ts,
    }
    uri = media.get("source_path") or media.get("uri")
    if uri:
        item["source_path"] = str(uri)
        item["uri"] = str(uri)
    if session:
        item["session"] = session
    slices = rec.get("slices")
    if isinstance(slices, list) and slices:
        item["slices"] = [str(s) for s in slices if s]
    return item


def hydrate_golden_items(
    labels_path: str | Path,
    manifest_path: str | Path,
    *,
    default_splits: Iterable[str] | None = None,
    smoke_limit: int = 16,
    smoke_file_keys: Iterable[str] | None = None,
    hard_file_keys: Iterable[str] | None = None,
    skip_missing_hash: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(items, errors)``. Errors are skip reasons (or hard fail if not skipping)."""
    labels_p = _resolve(labels_path) or Path(labels_path)
    if not labels_p.is_file():
        raise FileNotFoundError(f"labels not found: {labels_path}")
    index = load_media_index(manifest_path)
    defaults = list(default_splits) if default_splits is not None else list(_DEFAULT_SPLITS)
    smoke_keys = {normalize_name(k) for k in (smoke_file_keys or [])}
    hard_keys = {normalize_name(k) for k in (hard_file_keys or [])}
    imported_from = labels_p.as_posix()
    # Prefer repo-relative path when under repo root.
    try:
        imported_from = labels_p.resolve().relative_to(_REPO.resolve()).as_posix()
    except ValueError:
        pass

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    with labels_p.open("r", encoding="utf-8") as fh:
        row_i = 0
        for ln, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{labels_p}:{ln}: invalid JSON ({exc})")
                continue
            if not isinstance(rec, dict):
                errors.append(f"{labels_p}:{ln}: row must be object")
                continue
            file_name = rec.get("file") or rec.get("path")
            if not file_name:
                errors.append(f"{labels_p}:{ln}: missing file")
                continue
            key = normalize_name(str(file_name))
            media = index.get(key)
            splits = _assign_splits(
                file_key=key,
                index=row_i,
                default_splits=defaults,
                smoke_keys=smoke_keys,
                smoke_limit=smoke_limit,
                hard_keys=hard_keys,
            )
            try:
                item = hydrate_golden_item(
                    rec,
                    media,
                    splits=splits,
                    now=now,
                    imported_from=imported_from,
                )
            except ValueError as exc:
                msg = f"{labels_p}:{ln}: {exc}"
                if skip_missing_hash:
                    errors.append(msg)
                    continue
                raise ValueError(msg) from exc
            items.append(item)
            row_i += 1
    return items, errors


def write_golden_jsonl(path: str | Path, items: Iterable[Mapping[str, Any]]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(dict(item), ensure_ascii=False) + "\n")
    return out


def filter_by_split(items: Iterable[Mapping[str, Any]], split: str) -> list[dict[str, Any]]:
    return [dict(it) for it in items if split in (it.get("splits") or [])]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hydrate labels.jsonl + manifest.json → golden_item.v1 JSONL"
    )
    parser.add_argument("--registry", default=None, help="dataset_registry.json")
    parser.add_argument("--labels", default=None)
    parser.add_argument("--manifest", default=None, dest="dataset_manifest")
    parser.add_argument("--out", default=None, help="output JSONL path")
    parser.add_argument("--smoke-limit", type=int, default=None)
    parser.add_argument(
        "--skip-missing-hash",
        action="store_true",
        help="skip rows without manifest sha256 instead of failing",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="run Phase-0 contract checks on each item",
    )
    parser.add_argument(
        "--split",
        default=None,
        help="only emit items containing this split (e.g. smoke)",
    )
    args = parser.parse_args(argv)

    reg = load_dataset_registry(args.registry)
    labels = args.labels or reg.get("labels") or "data/eval/labels.jsonl"
    manifest = args.dataset_manifest or reg.get("manifest") or "data/eval/manifest.json"
    smoke_limit = (
        args.smoke_limit
        if args.smoke_limit is not None
        else int(reg.get("smoke_limit") or 16)
    )
    out = args.out or (
        f"quality/store/datasets/{reg.get('name', 'golden_apr_jul_2026')}@"
        f"{reg.get('version', '0.2.0')}/items.jsonl"
    )

    try:
        items, errors = hydrate_golden_items(
            labels,
            manifest,
            default_splits=reg.get("default_splits") or list(_DEFAULT_SPLITS),
            smoke_limit=smoke_limit,
            smoke_file_keys=reg.get("smoke_file_keys"),
            hard_file_keys=reg.get("hard_file_keys"),
            skip_missing_hash=args.skip_missing_hash,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.split:
        items = filter_by_split(items, args.split)

    if args.validate:
        from quality.validate_contracts import validate_document

        n_bad = 0
        for i, item in enumerate(items):
            errs = validate_document(item, f"item[{i}]")
            if errs:
                n_bad += 1
                for e in errs[:5]:
                    print(f"  - {e}", file=sys.stderr)
        if n_bad:
            print(f"validation failed for {n_bad}/{len(items)} items", file=sys.stderr)
            return 1

    write_golden_jsonl(out, items)
    print(f"wrote {len(items)} golden items → {out}")
    if errors:
        print(f"warnings: {len(errors)}", file=sys.stderr)
        for e in errors[:10]:
            print(f"  - {e}", file=sys.stderr)
    print(f"dataset={reg.get('name')}@{reg.get('version')} smoke_limit={smoke_limit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
