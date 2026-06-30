"""
Structured job output metadata for infra (output / lineage registry).

**Write path:** ``JobExecutor`` finishes the pipeline, calls
:class:`build_success_artifact_event_payload`, then ``mark_job_succeeded`` → ``update_job_status`` appends
one ``job_events`` row with ``to_status = 'SUCCEEDED'`` and stores this payload in ``payload_json``.
:func:`utils.luma_brain.sync_job_artifacts_from_success_event` runs in the same transaction and **replaces**
``artifacts`` rows for that ``job_id``, pointing each row at the new success event via ``job_event_id``.

**Lineage model:** ``kind`` is a concrete file-type key (stable for code). ``taxonomy`` is a coarse bucket for
product semantics (dashboards, docs, future export jobs). See :data:`KIND_TO_TAXONOMY`.

**job_events vs artifacts:** The success ``job_events`` row is the immutable audit snapshot (full JSON).
The ``artifacts`` table is the normalized, queryable index of outputs; timeline UIs should treat artifacts
as **materialized at** ``generated_at`` and **accounted for in the ledger** by the linked ``job_event_id``.

Schema is intentionally small and JSON-serializable; gallery file layout on disk stays unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# -----------------------------------------------------------------------------
# Taxonomy: stable product-level buckets (use in filters / documentation).
# Reserved: export_package, model_output — no first-class writers on this path yet.
# -----------------------------------------------------------------------------
TAXONOMY_PREVIEW = "preview"
TAXONOMY_ANALYSIS_RESULTS = "analysis_results"
TAXONOMY_GALLERY_HTML = "gallery_html"
TAXONOMY_EXPORT_PACKAGE = "export_package"
TAXONOMY_MODEL_OUTPUT = "model_output"

# Stable kind strings for platform / UI consumers (concrete artifact types).
KIND_ANALYSIS_RESULTS = "analysis_results_json"
KIND_PREVIEW_HTML = "preview_html"
KIND_FOLDER_GALLERY_HTML = "folder_gallery_html"
KIND_GALLERY_LAUNCH_SCRIPT = "gallery_launch_script"

KIND_TO_TAXONOMY: dict[str, str] = {
    KIND_ANALYSIS_RESULTS: TAXONOMY_ANALYSIS_RESULTS,
    KIND_PREVIEW_HTML: TAXONOMY_PREVIEW,
    KIND_FOLDER_GALLERY_HTML: TAXONOMY_GALLERY_HTML,
    KIND_GALLERY_LAUNCH_SCRIPT: TAXONOMY_GALLERY_HTML,
}

# Optional per-kind roles (finer semantics inside taxonomy ``gallery_html`` / others).
ROLE_BY_KIND: dict[str, str] = {
    KIND_ANALYSIS_RESULTS: "structured_output",
    KIND_PREVIEW_HTML: "session_index_html",
    KIND_FOLDER_GALLERY_HTML: "folder_gallery_html",
    KIND_GALLERY_LAUNCH_SCRIPT: "gallery_launch_shell",
}


def taxonomy_for_kind(kind: str | None) -> str | None:
    """Map concrete ``kind`` to taxonomy bucket; unknown kinds return ``None``."""
    if not kind:
        return None
    return KIND_TO_TAXONOMY.get(str(kind).strip())


def select_primary_artifact(artifacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Choose the single **primary** output for lineage pointers (APIs, replay, downstream jobs).

    Order:
    1. First item with ``taxonomy == TAXONOMY_ANALYSIS_RESULTS`` (machine-readable pipeline SSOT).
    2. First item with ``kind == KIND_ANALYSIS_RESULTS`` (compat before taxonomy is present).
    3. Else first list item (job produced only ancillary files).
    """
    if not artifacts:
        return None
    for a in artifacts:
        if not isinstance(a, dict):
            continue
        if a.get("taxonomy") == TAXONOMY_ANALYSIS_RESULTS:
            return a
    for a in artifacts:
        if isinstance(a, dict) and a.get("kind") == KIND_ANALYSIS_RESULTS:
            return a
    for a in artifacts:
        if isinstance(a, dict):
            return a
    return None


def _abspath(p: str | Path | None) -> str | None:
    if p is None:
        return None
    try:
        return str(Path(p).resolve())
    except OSError:
        return str(p)


def build_success_artifact_event_payload(
    *,
    base: dict[str, Any],
    analysis_results_path: str | Path | None,
    preview_html_path: str | Path | None,
    folder_galleries: list[dict[str, Any]],
    launch_scripts: list[str | Path] | None = None,
    generated_at: int,
    source: str = "aesthetic_pipeline",
) -> dict[str, Any]:
    """
    Merge executor context (``base``) with pipeline outputs for ``mark_job_succeeded`` / job_events.

    Each artifact includes:

    - ``kind`` — concrete type (``KIND_*``).
    - ``taxonomy`` — coarse bucket (:data:`TAXONOMY_*`) for cross-job semantics.
    - ``role`` — optional subtype (e.g. launcher vs folder page under ``gallery_html``).
    - ``path``, ``generated_at`` (Unix seconds).
    - ``category`` — only on per-folder gallery rows (which subfolder the page covers).
    - ``stage`` / ``source`` — when ``base`` supplies ``stage_name`` or ``source`` names the producer.

    **metadata_json** in SQLite holds any extra keys (``category`` remains there too for registry rows).

    ``primary_artifact`` follows :func:`select_primary_artifact` (analysis JSON preferred).
    """
    out = dict(base)
    stage_hint = base.get("stage_name")
    stage_s = str(stage_hint).strip() if stage_hint not in (None, "") else None
    src_s = str(source).strip() if source else None

    def _lineage(item: dict[str, Any]) -> dict[str, Any]:
        k = str(item.get("kind") or "").strip()
        tax = taxonomy_for_kind(k)
        if tax:
            item["taxonomy"] = tax
        role = ROLE_BY_KIND.get(k)
        if role:
            item["role"] = role
        if stage_s:
            item["stage"] = stage_s
        if src_s:
            item["source"] = src_s
        return item

    arts: list[dict[str, Any]] = []
    if analysis_results_path:
        p = _abspath(analysis_results_path)
        if p:
            arts.append(
                _lineage(
                    {
                        "kind": KIND_ANALYSIS_RESULTS,
                        "path": p,
                        "generated_at": generated_at,
                    }
                )
            )
    if preview_html_path:
        p = _abspath(preview_html_path)
        if p:
            arts.append(
                _lineage(
                    {
                        "kind": KIND_PREVIEW_HTML,
                        "path": p,
                        "generated_at": generated_at,
                    }
                )
            )
    for fg in folder_galleries:
        p = _abspath(fg.get("path"))
        if not p:
            continue
        item: dict[str, Any] = {
            "kind": KIND_FOLDER_GALLERY_HTML,
            "path": p,
            "generated_at": generated_at,
        }
        cat = fg.get("category")
        if cat:
            item["category"] = str(cat)
        arts.append(_lineage(item))
    for script in launch_scripts or []:
        p = _abspath(script)
        if p:
            arts.append(
                _lineage(
                    {
                        "kind": KIND_GALLERY_LAUNCH_SCRIPT,
                        "path": p,
                        "generated_at": generated_at,
                    }
                )
            )

    out["artifacts"] = arts
    out["artifact_registry_version"] = 1
    primary = select_primary_artifact(arts)
    if primary is not None:
        out["primary_artifact"] = primary
    return out
