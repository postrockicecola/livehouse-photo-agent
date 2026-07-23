"""Studio: discover archive sessions, active pointer, analysis readiness, pipeline hints."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from services.pipeline_stages import CANONICAL_PIPELINE_STAGES
from utils.runtime_session import archive_root_for_previews, read_latest_session_pointer

# Short labels for the studio pipeline bar (subset of canonical stages).
STUDIO_PIPELINE: tuple[tuple[str, str], ...] = (
    ("PREPARE_INPUT", "PREPARE"),
    ("STAGE1_FILTER", "S1"),
    ("STAGE2_FAST_SCORE", "S2"),
    ("STAGE3_VLM", "S3"),
    ("WRITE_ARTIFACT", "WRITE"),
)

_STAGE_INDEX = {name: i for i, (name, _) in enumerate(STUDIO_PIPELINE)}

RUNNABLE_ANALYZE_STATUSES: tuple[str, ...] = (
    "QUEUED",
    "CLAIMED",
    "PREPROCESSING",
    "INFERENCING",
    "POSTPROCESSING",
    "FAILED_RETRYABLE",
)

_ACTIVE_ANALYZE_STATUSES: frozenset[str] = frozenset(
    s for s in RUNNABLE_ANALYZE_STATUSES if s != "QUEUED"
)


def _pick_preferred_analyze_job_id(candidates: list[tuple[int, str]]) -> int | None:
    """Prefer pipeline-active jobs over duplicate QUEUED rows; FIFO among QUEUED only."""
    if not candidates:
        return None
    active = [jid for jid, st in candidates if st in _ACTIVE_ANALYZE_STATUSES]
    if active:
        return max(active)
    queued = [jid for jid, st in candidates if st == "QUEUED"]
    if queued:
        return min(queued)
    return max(jid for jid, _ in candidates)

_STATUS_STAGE_HINT: dict[str, str] = {
    "QUEUED": "PREPARE_INPUT",
    "CLAIMED": "PREPARE_INPUT",
    "PREPROCESSING": "STAGE1_FILTER",
    "INFERENCING": "STAGE3_VLM",
    "POSTPROCESSING": "WRITE_ARTIFACT",
    "SUCCEEDED": "FINALIZE",
    "FAILED_RETRYABLE": "WRITE_ARTIFACT",
    "FAILED_PERMANENT": "WRITE_ARTIFACT",
    "DEAD_LETTERED": "WRITE_ARTIFACT",
    "CANCELLED": "WRITE_ARTIFACT",
}

_SKIP_ARCHIVE_DIRS = frozenset({".runtime", "runtime", ".git", ".DS_Store"})

_SOURCE_DIR_RE = re.compile(r"""^\s*source_dir:\s*['"]?([^'"]+)['"]?\s*$""")


def read_source_dir_from_yaml(config_path: str | Path | None = None) -> str | None:
    """Read ``paths.source_dir`` without PyYAML (for Studio CLI / minimal envs)."""
    if config_path is None:
        raw = os.environ.get("LIVEHOUSE_CONFIG", "configs/livehouse.yaml").strip()
        config_path = Path(raw)
    else:
        config_path = Path(config_path)
    if not config_path.is_file():
        return None
    try:
        for line in config_path.read_text(encoding="utf-8").splitlines():
            m = _SOURCE_DIR_RE.match(line)
            if m:
                return m.group(1).strip()
    except OSError:
        return None
    return None


def resolve_default_archive_root(fallback_previews: str | Path) -> Path:
    """Archive root from env, active previews, or ``fallback_previews`` layout."""
    env = (os.environ.get("LUMA_ARCHIVE_ROOT") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()

    previews = Path(fallback_previews).expanduser().resolve()
    ar = archive_root_for_previews(previews)
    if ar is not None and ar.is_dir():
        return ar

    if previews.is_dir() and previews.name.lower() == "previews":
        return previews.parent.parent
    return previews.parent if previews.is_dir() else previews


def _score_from_analysis_row(row: dict[str, Any]) -> float:
    try:
        raw = row.get("overall_score")
        if raw is None and isinstance(row.get("scores"), dict):
            raw = row["scores"].get("overall")
        if raw is None:
            raw = row.get("score")
        if raw is not None:
            return float(raw)
    except (TypeError, ValueError):
        pass
    return -1.0


def _best_preview_cover_quoted(previews_dir: Path) -> str:
    """Gallery ``/image?path=`` token for highest ``overall_score`` row, else first preview JPEG."""
    from urllib.parse import quote

    if not previews_dir.is_dir():
        return ""
    results_path = previews_dir / "analysis_results.json"
    if results_path.is_file():
        try:
            size = results_path.stat().st_size
            if size <= 14_000_000:
                data = json.loads(results_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, list) and data:
                    best = max(data, key=lambda r: _score_from_analysis_row(r) if isinstance(r, dict) else -1.0)
                    if isinstance(best, dict):
                        path = str(best.get("path") or "").strip()
                        file_name = str(best.get("file") or "").strip()
                        if not path and file_name:
                            path = str((previews_dir / file_name).resolve())
                        elif path and not path.startswith("/"):
                            path = str((previews_dir / path.replace("./", "")).resolve())
                        if path:
                            return quote(str(Path(path).expanduser().resolve()), safe="")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return _first_preview_cover_quoted(previews_dir)


def _first_preview_cover_quoted(previews_dir: Path) -> str:
    """URL-safe path token for gallery ``/image?path=`` (first preview JPEG, stable sort)."""
    if not previews_dir.is_dir():
        return ""
    try:
        from urllib.parse import quote

        names = sorted(
            ent.name
            for ent in previews_dir.iterdir()
            if ent.is_file() and ent.suffix.lower() in {".jpg", ".jpeg"}
        )
    except OSError:
        return ""
    if not names:
        return ""
    full = str((previews_dir / names[0]).resolve())
    return quote(full, safe="")


def _count_preview_images(previews_dir: Path) -> int:
    if not previews_dir.is_dir():
        return 0
    n = 0
    try:
        for ent in previews_dir.iterdir():
            if not ent.is_file():
                continue
            if ent.suffix.lower() in {".jpg", ".jpeg"}:
                n += 1
    except OSError:
        return 0
    return n


def _resolve_analysis_row_path(row: dict[str, Any], previews_dir: Path) -> str:
    path = str(row.get("path") or "").strip()
    file_name = str(row.get("file") or "").strip()
    if not path and file_name:
        path = str((previews_dir / file_name).resolve())
    elif path and not path.startswith("/"):
        path = str((previews_dir / path.replace("./", "")).resolve())
    elif path:
        path = str(Path(path).expanduser().resolve())
    return path


def _path_quoted_for_row(row: dict[str, Any], previews_dir: Path) -> str:
    from urllib.parse import quote

    path = _resolve_analysis_row_path(row, previews_dir)
    if path and not os.path.isfile(path):
        fn = str(row.get("file") or "").strip() or os.path.basename(path)
        if fn:
            for sub in ("AI_Best_90+", "AI_Keep_60-90", "AI_Trash_Below60", ""):
                alt = previews_dir / sub / fn if sub else previews_dir / fn
                if alt.is_file():
                    path = str(alt.resolve())
                    break
    if not path or not os.path.isfile(path):
        return ""
    return quote(path, safe="")


def _composition_score(row: dict[str, Any]) -> float:
    dims = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    for src in (
        row.get("composition"),
        scores.get("composition"),
        dims.get("composition_framing"),
        dims.get("composition"),
    ):
        try:
            if src is not None:
                v = float(src)
                if v >= 0:
                    return v
        except (TypeError, ValueError):
            continue
    return -1.0


def _emotion_score(row: dict[str, Any]) -> float:
    dims = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    parts: list[float] = []
    for src in (
        dims.get("moment_peak"),
        dims.get("atmosphere_impact"),
        row.get("energy"),
        scores.get("energy"),
    ):
        try:
            if src is not None:
                v = float(src)
                if v >= 0:
                    parts.append(v)
        except (TypeError, ValueError):
            continue
    if not parts:
        return -1.0
    return sum(parts) / len(parts)


def _row_identity(row: dict[str, Any], previews_dir: Path) -> str:
    path = _resolve_analysis_row_path(row, previews_dir)
    if path:
        return path
    return str(row.get("file") or "").strip()


def featured_frames_for_session(
    previews_dir: Path,
    *,
    min_count: int = 3,
    max_count: int = 5,
) -> list[dict[str, Any]]:
    """Pick 3–5 showcase frames: top aesthetic, composition, emotion, then overall fill."""
    if not previews_dir.is_dir():
        return []
    results_path = previews_dir / "analysis_results.json"
    if not results_path.is_file():
        return []
    try:
        if results_path.stat().st_size > 14_000_000:
            return []
        data = json.loads(results_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []

    rows = [r for r in data if isinstance(r, dict)]
    if not rows:
        return []

    categories: tuple[tuple[str, str, Any], ...] = (
        ("Aesthetic", "Aesthetic", _score_from_analysis_row),
        ("Composition", "Composition", _composition_score),
        ("Emotion", "Emotion", _emotion_score),
    )

    picked: list[dict[str, Any]] = []
    used: set[str] = set()

    def append_row(row: dict[str, Any], highlight: str, score_label: str, score_value: float) -> None:
        ident = _row_identity(row, previews_dir)
        if not ident or ident in used:
            return
        pq = _path_quoted_for_row(row, previews_dir)
        if not pq:
            return
        used.add(ident)
        if score_label == "Aesthetic":
            display = f"{score_value:.1f}".rstrip("0").rstrip(".")
        else:
            display = f"{score_value:.1f}"
        picked.append(
            {
                "path_quoted": pq,
                "file": str(row.get("file") or "").strip() or None,
                "highlight": highlight,
                "score_label": score_label,
                "score_value": round(score_value, 2),
                "score_display": display,
            }
        )

    for highlight, score_label, scorer in categories:
        ranked = sorted(rows, key=lambda r: scorer(r), reverse=True)
        for row in ranked:
            score = scorer(row)
            if score < 0:
                continue
            before = len(used)
            append_row(row, highlight, score_label, score)
            if len(used) > before:
                break

    if len(picked) < max_count:
        for row in sorted(rows, key=_score_from_analysis_row, reverse=True):
            if len(picked) >= max_count:
                break
            score = _score_from_analysis_row(row)
            if score < 0:
                continue
            append_row(row, "Featured", "Aesthetic", score)

    return picked[:max_count]


def analysis_results_ready(previews_dir: Path) -> bool:
    """True when ``analysis_results.json`` looks like a non-empty gallery dataset."""
    p = previews_dir / "analysis_results.json"
    if not p.is_file():
        return False
    try:
        if p.stat().st_size < 4:
            return False
        head = p.read_text(encoding="utf-8", errors="replace")[:65536].lstrip()
    except OSError:
        return False
    if not head.startswith("["):
        return True
    inner = head[1:].lstrip()
    return bool(inner) and not inner.startswith("]")


def _session_date_key(name: str) -> int:
    if len(name) >= 10 and name[4:5] == "-" and name[7:8] == "-":
        try:
            import datetime as dt

            t = dt.datetime.strptime(name[:10], "%Y-%m-%d")
            return int(t.timestamp())
        except ValueError:
            pass
    return 0


def _session_sort_time(row: dict[str, Any]) -> int:
    """Primary sort time: ``YYYY-MM-DD`` prefix in folder name, else session_dir mtime."""
    sk = str(row.get("session_key") or "")
    t = _session_date_key(sk)
    if t != 0:
        return t
    sd = str(row.get("session_dir") or "").strip()
    if sd:
        try:
            return int(Path(sd).stat().st_mtime)
        except OSError:
            pass
    return 0


def sort_session_rows(rows: list[dict[str, Any]], *, descending: bool = True) -> list[dict[str, Any]]:
    """Sort by time, then ``session_key`` lexicographically (ties always A→Z)."""

    def compare(a: dict[str, Any], b: dict[str, Any]) -> int:
        ta = _session_sort_time(a)
        tb = _session_sort_time(b)
        if ta != tb:
            return (tb - ta) if descending else (ta - tb)
        sa = str(a.get("session_key") or "")
        sb = str(b.get("session_key") or "")
        if sa < sb:
            return -1
        if sa > sb:
            return 1
        return 0

    from functools import cmp_to_key

    return sorted(rows, key=cmp_to_key(compare))


def _has_raw_or_previews(session_dir: Path) -> bool:
    for sub in ("RAW", "Raw", "raw"):
        if (session_dir / sub).is_dir():
            return True
    prev = session_dir / "Previews"
    return prev.is_dir()


def scan_archive_session_dirs(archive_root: Path) -> list[dict[str, Any]]:
    """Filesystem sessions under *archive_root* (newest date prefix first)."""
    out: list[dict[str, Any]] = []
    if not archive_root.is_dir():
        return out
    try:
        entries = list(archive_root.iterdir())
    except OSError:
        return out

    for ent in entries:
        if not ent.is_dir():
            continue
        name = ent.name
        if name.startswith(".") or name in _SKIP_ARCHIVE_DIRS:
            continue
        if not _has_raw_or_previews(ent):
            continue
        previews = ent / "Previews"
        if not previews.is_dir():
            for sub in ("RAW", "Raw", "raw"):
                if (ent / sub).is_dir():
                    previews = ent / "Previews"
                    break
        preview_count = _count_preview_images(previews) if previews.is_dir() else 0
        cover = _best_preview_cover_quoted(previews) if previews.is_dir() else ""
        out.append(
            {
                "session_key": name,
                "session_dir": str(ent.resolve()),
                "previews_dir": str(previews.resolve()) if previews.is_dir() else "",
                "preview_count": preview_count,
                "has_analysis_results": analysis_results_ready(previews) if previews.is_dir() else False,
                "cover_path_quoted": cover,
                "brain_session_id": None,
                "photos_ingested": 0,
                "photos_analyzed": 0,
                "source": "filesystem",
            }
        )

    return sort_session_rows(out, descending=True)


def _brain_sessions(conn: sqlite3.Connection, *, limit: int = 80) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          s.id,
          s.session_key,
          s.previews_dir,
          s.session_dir,
          COALESCE(SUM(CASE WHEN p.status = 'INGESTED' THEN 1 ELSE 0 END), 0) AS photos_ingested,
          COALESCE(SUM(CASE WHEN p.status = 'ANALYZED' THEN 1 ELSE 0 END), 0) AS photos_analyzed
        FROM sessions s
        LEFT JOIN photos p ON p.session_id = s.id
        GROUP BY s.id
        ORDER BY s.started_at DESC
        LIMIT ?
        """,
        (max(1, limit),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        previews_s = str(r["previews_dir"] or "").strip()
        previews = Path(previews_s) if previews_s else None
        preview_count = _count_preview_images(previews) if previews and previews.is_dir() else 0
        session_dir = str(r["session_dir"] or "").strip()
        if not session_dir and previews:
            session_dir = str(previews.parent.resolve())
        cover = _best_preview_cover_quoted(previews) if previews and previews.is_dir() else ""
        out.append(
            {
                "session_key": str(r["session_key"]),
                "session_dir": session_dir,
                "previews_dir": previews_s,
                "preview_count": preview_count,
                "has_analysis_results": analysis_results_ready(previews) if previews and previews.is_dir() else False,
                "cover_path_quoted": cover,
                "brain_session_id": int(r["id"]),
                "photos_ingested": int(r["photos_ingested"]),
                "photos_analyzed": int(r["photos_analyzed"]),
                "source": "brain",
            }
        )
    return out


def merge_session_lists(
    filesystem: list[dict[str, Any]],
    brain: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge by normalized ``previews_dir``; brain stats win on collision."""
    by_previews: dict[str, dict[str, Any]] = {}
    for row in filesystem:
        pd = str(row.get("previews_dir") or "").strip()
        key = str(Path(pd).resolve()) if pd else str(row.get("session_dir") or "")
        if key:
            by_previews[key] = dict(row)

    for row in brain:
        pd = str(row.get("previews_dir") or "").strip()
        if not pd:
            continue
        try:
            key = str(Path(pd).expanduser().resolve())
        except OSError:
            key = pd
        prev = by_previews.get(key)
        merged = dict(prev) if prev else {}
        merged.update(row)
        if prev:
            merged["preview_count"] = max(int(prev.get("preview_count") or 0), int(row.get("preview_count") or 0))
            merged["has_analysis_results"] = bool(prev.get("has_analysis_results")) or bool(
                row.get("has_analysis_results")
            )
            if not str(merged.get("cover_path_quoted") or "").strip():
                merged["cover_path_quoted"] = prev.get("cover_path_quoted") or row.get("cover_path_quoted") or ""
            merged["source"] = "brain+filesystem" if prev.get("source") == "filesystem" else row.get("source")
        by_previews[key] = merged

    out = list(by_previews.values())
    # Include brain rows without previews path
    seen_dirs = {str(r.get("previews_dir") or "") for r in out}
    for row in brain:
        pd = str(row.get("previews_dir") or "").strip()
        if pd and pd not in seen_dirs:
            out.append(row)

    return sort_session_rows(out, descending=True)


def _latest_job_status_by_session_id(conn: sqlite3.Connection) -> dict[int, str]:
    """Most recent analyze job status per brain session (single table scan)."""
    try:
        rows = conn.execute(
            """
            SELECT session_id, status, COALESCE(updated_at, created_at) AS ts
            FROM jobs
            WHERE session_id IS NOT NULL
              AND job_type IN ('ANALYZE_SESSION', 'ANALYZE_PATH')
            ORDER BY session_id ASC, ts DESC, id DESC
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[int, str] = {}
    for row in rows:
        sid = row["session_id"]
        if sid is None:
            continue
        try:
            key = int(sid)
        except (TypeError, ValueError):
            continue
        if key not in out:
            out[key] = str(row["status"] or "")
    return out


def _attach_session_job_status(
    items: list[dict[str, Any]],
    status_by_session: dict[int, str],
) -> None:
    for row in items:
        sid = row.get("brain_session_id")
        if sid is None:
            continue
        try:
            st = status_by_session.get(int(sid))
        except (TypeError, ValueError):
            st = None
        if st:
            row["last_job_status"] = st


def list_studio_sessions(
    conn: sqlite3.Connection | None,
    archive_root: Path,
    *,
    limit: int = 80,
) -> list[dict[str, Any]]:
    fs = scan_archive_session_dirs(archive_root)
    brain: list[dict[str, Any]] = []
    status_by_session: dict[int, str] = {}
    if conn is not None:
        brain = _brain_sessions(conn, limit=limit)
        status_by_session = _latest_job_status_by_session_id(conn)
    merged = merge_session_lists(fs, brain)
    _attach_session_job_status(merged, status_by_session)
    return merged[: max(1, limit)]


def _session_display_date(session_key: str) -> str:
    sk = str(session_key or "").strip()
    if len(sk) >= 10 and sk[4:5] == "-" and sk[7:8] == "-":
        return sk[:10]
    return sk


def list_recent_deliveries(
    sessions: list[dict[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Sessions with a non-empty ``analysis_results.json`` export, newest first."""
    rows: list[dict[str, Any]] = []
    for s in sessions:
        if not s.get("has_analysis_results"):
            continue
        pd = str(s.get("previews_dir") or "").strip()
        if not pd:
            continue
        try:
            n = _count_analysis_export(Path(pd))
        except OSError:
            n = None
        if n is None or n <= 0:
            continue
        key = str(s.get("session_key") or "")
        funnel = s.get("funnel") if isinstance(s.get("funnel"), dict) else {}
        imported = (
            funnel.get("imported")
            if isinstance(funnel, dict)
            else None
        )
        if imported is None:
            imported = s.get("photos_ingested") or s.get("preview_count")
        try:
            imported_n = int(imported) if imported is not None else 0
        except (TypeError, ValueError):
            imported_n = 0
        rows.append(
            {
                "session_key": key,
                "session_date": _session_display_date(key),
                "photos_imported": max(0, imported_n),
                "photos_exported": int(n),
                "previews_dir": pd,
                "sort_time": _session_sort_time(s),
            }
        )
    rows.sort(key=lambda r: (int(r.get("sort_time") or 0), str(r.get("session_date") or "")), reverse=True)
    out: list[dict[str, Any]] = []
    for r in rows[: max(1, limit)]:
        out.append(
            {
                "session_key": r["session_key"],
                "session_date": r["session_date"],
                "photos_imported": int(r.get("photos_imported") or 0),
                "photos_exported": r["photos_exported"],
                "previews_dir": r["previews_dir"],
            }
        )
    return out


def active_session_from_archive(archive_root: Path) -> dict[str, Any] | None:
    from utils.runtime_session import read_newest_latest_session_pointer

    hit = read_newest_latest_session_pointer(base_dir=archive_root)
    ref = hit[1] if hit is not None else read_latest_session_pointer(archive_root)
    if not ref:
        return None
    previews_s = str(ref.get("previews_dir") or "").strip()
    if not previews_s:
        return None
    previews = Path(previews_s)
    session_dir = str(ref.get("session_dir") or previews.parent)
    session_key = Path(session_dir).name if session_dir else previews.parent.name
    return {
        "session_key": session_key,
        "session_dir": session_dir,
        "previews_dir": previews_s,
        "preview_count": _count_preview_images(previews) if previews.is_dir() else 0,
        "has_analysis_results": analysis_results_ready(previews) if previews.is_dir() else False,
        "raw_dir": str(ref.get("raw_dir") or ""),
    }


def _normalize_previews_path(previews_dir: str) -> str:
    return str(Path(previews_dir).expanduser().resolve())


def find_brain_session_id(conn: sqlite3.Connection, previews_dir: str) -> int | None:
    target = _normalize_previews_path(previews_dir)
    rows = conn.execute(
        "SELECT id, previews_dir FROM sessions ORDER BY started_at DESC LIMIT 200",
    ).fetchall()
    for r in rows:
        pd = str(r["previews_dir"] or "").strip()
        if not pd:
            continue
        try:
            if _normalize_previews_path(pd) == target:
                return int(r["id"])
        except OSError:
            continue
    return None


def _analyze_path_matches_previews(payload_json: Any, previews_dir: str) -> bool:
    payload = _parse_event_payload(payload_json)
    sd = str(payload.get("source_dir") or "").strip()
    if not sd:
        return False
    try:
        return _normalize_previews_path(sd) == _normalize_previews_path(previews_dir)
    except OSError:
        return False


def find_runnable_analyze_job_id(
    conn: sqlite3.Connection,
    *,
    previews_dir: str,
    brain_session_id: int | None,
) -> int | None:
    """Return an in-flight analyze job id for this previews dir, if any."""
    if brain_session_id is not None:
        placeholders = ",".join("?" * len(RUNNABLE_ANALYZE_STATUSES))
        rows = conn.execute(
            f"""
            SELECT id, status
            FROM jobs
            WHERE job_type = 'ANALYZE_SESSION'
              AND session_id = ?
              AND status IN ({placeholders})
            """,
            (brain_session_id, *RUNNABLE_ANALYZE_STATUSES),
        ).fetchall()
        picked = _pick_preferred_analyze_job_id([(int(r["id"]), str(r["status"])) for r in rows])
        if picked is not None:
            return picked

    placeholders = ",".join("?" * len(RUNNABLE_ANALYZE_STATUSES))
    rows = conn.execute(
        f"""
        SELECT id, status, payload_json
        FROM jobs
        WHERE job_type = 'ANALYZE_PATH'
          AND status IN ({placeholders})
        ORDER BY id DESC
        LIMIT 200
        """,
        RUNNABLE_ANALYZE_STATUSES,
    ).fetchall()
    candidates: list[tuple[int, str]] = []
    for row in rows:
        if _analyze_path_matches_previews(row["payload_json"], previews_dir):
            candidates.append((int(row["id"]), str(row["status"])))
    return _pick_preferred_analyze_job_id(candidates)


def _parse_event_payload(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def pipeline_view_from_job(job: dict[str, Any] | None, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build studio pipeline bar state from job row + recent events."""
    labels = [short for _, short in STUDIO_PIPELINE]
    stages_full = list(CANONICAL_PIPELINE_STAGES)
    current_index = -1
    failed = False
    complete = False

    if job:
        st = str(job.get("status") or "")
        if st in {"FAILED_RETRYABLE", "FAILED_PERMANENT", "DEAD_LETTERED", "CANCELLED"}:
            failed = True
        if st == "SUCCEEDED":
            complete = True
            current_index = len(STUDIO_PIPELINE) - 1
        hint = _STATUS_STAGE_HINT.get(st)
        if hint and hint in _STAGE_INDEX:
            current_index = max(current_index, _STAGE_INDEX[hint])

        stage_name = str(job.get("stage_name") or "").strip()
        if stage_name in _STAGE_INDEX:
            current_index = max(current_index, _STAGE_INDEX[stage_name])

    for ev in events:
        payload = _parse_event_payload(ev.get("payload_json"))
        sn = str(payload.get("stage_name") or "").strip()
        if sn in _STAGE_INDEX:
            current_index = max(current_index, _STAGE_INDEX[sn])
        msg = str(ev.get("message") or "")
        for full in stages_full:
            if full in msg and full in _STAGE_INDEX:
                current_index = max(current_index, _STAGE_INDEX[full])

    if complete:
        current_index = len(STUDIO_PIPELINE) - 1

    return {
        "labels": labels,
        "current_index": current_index,
        "complete": complete,
        "failed": failed,
    }


def _find_audit_log(previews_dir: Path) -> Path | None:
    session = previews_dir.parent
    for sub in ("", "RAW", "Raw", "raw"):
        root = session / sub if sub else session
        candidate = root / "aesthetic_audit.jsonl"
        if candidate.is_file():
            return candidate
    return None


def _count_analysis_export(previews_dir: Path) -> int | None:
    path = previews_dir / "analysis_results.json"
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
        if isinstance(data, list):
            return len(data)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return None


def _pipeline_funnel_counts(*, preview_count: int, previews_dir: Path | None) -> dict[str, int] | None:
    """Approximate stage in/out counts from audit JSONL + analysis export."""
    inp = max(0, int(preview_count))
    exported: int | None = None
    if previews_dir is not None and previews_dir.is_dir():
        exported = _count_analysis_export(previews_dir)

    log: Path | None = None
    if previews_dir is not None:
        log = _find_audit_log(previews_dir)

    if log is None:
        if inp <= 0 and exported is None:
            return None
        out = exported if exported is not None else inp
        picked = exported if exported is not None else inp
        return {"in": inp, "s1": inp, "s2": inp, "s3": out, "picked": picked, "out": out}

    s1_out = s2_out = s3_out = 0
    audited = 0
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        audited += 1
        tags = {str(t) for t in (row.get("tags") or [])}
        if "technical_issue" in tags:
            continue
        s1_out += 1
        if "low_quality" in tags or "stage2_prefilter" in tags:
            continue
        s2_out += 1
        sm = row.get("stage3_meta") if isinstance(row.get("stage3_meta"), dict) else {}
        outcome = str(sm.get("outcome") or "")
        dims = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
        if outcome.startswith("skipped") and not dims:
            continue
        if dims or outcome in ("success", "fallback_defaults", "degraded_inference", "cache_hit", "vlm_error"):
            s3_out += 1

    base_in = inp if inp > 0 else audited
    out = exported if exported is not None else s3_out
    s3_val = s3_out if s3_out > 0 else s2_out
    picked = out if out is not None and out > 0 else s3_val
    return {
        "in": base_in,
        "s1": s1_out if s1_out > 0 else base_in,
        "s2": s2_out if s2_out > 0 else s1_out,
        "s3": s3_val,
        "picked": picked if picked > 0 else None,
        "out": out if out > 0 else s3_out,
    }


def _ms_to_sec(raw: Any) -> float | None:
    try:
        if raw is None:
            return None
        v = float(raw)
        if v <= 0:
            return None
        return round(v / 1000.0, 1)
    except (TypeError, ValueError):
        return None


def _stage_durations_sec(
    job: dict[str, Any] | None,
    events: list[dict[str, Any]],
) -> list[float | None]:
    """Five stage durations aligned with STUDIO_PIPELINE."""
    durs: list[float | None] = [None] * len(STUDIO_PIPELINE)

    if job:
        pre = _ms_to_sec(job.get("preprocess_ms"))
        inf = _ms_to_sec(job.get("inference_ms"))
        post = _ms_to_sec(job.get("postprocess_ms"))
        if pre is not None:
            durs[1] = pre
        if inf is not None:
            durs[2] = round(inf * 0.35, 1)
            durs[3] = round(inf * 0.65, 1)
        if post is not None:
            durs[4] = post

    status_ts: dict[str, int] = {}
    stage_ts: dict[int, list[int]] = {i: [] for i in range(len(STUDIO_PIPELINE))}
    for ev in events:
        ts = ev.get("created_at")
        if ts is None:
            continue
        try:
            t = int(ts)
        except (TypeError, ValueError):
            continue
        to_st = str(ev.get("to_status") or "")
        if to_st and to_st not in status_ts:
            status_ts[to_st] = t
        payload = _parse_event_payload(ev.get("payload_json"))
        sn = str(payload.get("stage_name") or "").strip()
        if sn in _STAGE_INDEX:
            stage_ts[_STAGE_INDEX[sn]].append(t)
        if payload.get("pipeline_metrics"):
            dur = payload.get("duration_sec") or payload.get("wall_seconds")
            try:
                sec = float(dur)
            except (TypeError, ValueError):
                sec = None
            if sec is not None and sec > 0 and sn in _STAGE_INDEX:
                durs[_STAGE_INDEX[sn]] = round(sec, 1)

    if status_ts.get("PREPROCESSING") and status_ts.get("INFERENCING"):
        d0 = status_ts["INFERENCING"] - status_ts.get("CLAIMED", status_ts["PREPROCESSING"])
        if d0 > 0:
            durs[0] = round(float(d0), 1)
    if status_ts.get("INFERENCING") and status_ts.get("POSTPROCESSING"):
        span = status_ts["POSTPROCESSING"] - status_ts["INFERENCING"]
        if span > 0 and durs[2] is None and durs[3] is None:
            durs[2] = round(span * 0.35, 1)
            durs[3] = round(span * 0.65, 1)
    if status_ts.get("POSTPROCESSING") and status_ts.get("SUCCEEDED"):
        span = status_ts["SUCCEEDED"] - status_ts["POSTPROCESSING"]
        if span > 0 and durs[4] is None:
            durs[4] = round(float(span), 1)

    for idx, times in stage_ts.items():
        if len(times) >= 2 and durs[idx] is None:
            durs[idx] = round(float(max(times) - min(times)), 1)

    return durs


PHOTOGRAPHY_WORKFLOW_LABELS: tuple[str, ...] = (
    "Imported",
    "Filtered",
    "AI Scored",
    "Picked",
    "Exported",
)


def _topk_picked_from_events(events: list[dict[str, Any]]) -> int | None:
    for ev in reversed(events):
        payload = _parse_event_payload(ev.get("payload_json"))
        for blob in (payload, payload.get("pipeline_metrics") if isinstance(payload.get("pipeline_metrics"), dict) else {}):
            if not isinstance(blob, dict):
                continue
            raw = blob.get("topk_dedup_after")
            if raw is None:
                continue
            try:
                n = int(raw)
            except (TypeError, ValueError):
                continue
            if n >= 0:
                return n
    return None


def _workflow_highlight_index(*, current_index: int, complete: bool) -> int:
    if complete:
        return len(PHOTOGRAPHY_WORKFLOW_LABELS) - 1
    if current_index <= 0:
        return 0 if current_index == 0 else -1
    if current_index == 1:
        return 1
    if current_index in (2, 3):
        return 2
    if current_index >= 4:
        return 3
    return -1


def photography_workflow_stages(
    *,
    funnel: dict[str, int] | None,
    events: list[dict[str, Any]],
    current_index: int,
    complete: bool,
    failed: bool,
) -> list[dict[str, Any]]:
    """User-facing session funnel (Imported → Exported), aligned with studio audit counts."""
    fin = funnel or {}
    topk = _topk_picked_from_events(events)
    picked = topk if topk is not None else fin.get("picked")
    if picked is None:
        picked = fin.get("out")
    counts = [
        fin.get("in"),
        fin.get("s1"),
        fin.get("s3"),
        picked,
        fin.get("out"),
    ]
    hi = _workflow_highlight_index(current_index=current_index, complete=complete)
    stages: list[dict[str, Any]] = []
    for i, label in enumerate(PHOTOGRAPHY_WORKFLOW_LABELS):
        if complete:
            state = "done"
        elif failed and hi == i:
            state = "failed"
        elif hi < 0:
            state = "pending"
        elif i < hi:
            state = "done"
        elif i == hi:
            state = "active" if not complete else "done"
        else:
            state = "pending"
        stages.append(
            {
                "label": label,
                "count": counts[i] if i < len(counts) else None,
                "state": state,
            }
        )
    if complete:
        for s in stages:
            s["state"] = "done"
    return stages


def pipeline_stages_detail(
    job: dict[str, Any] | None,
    events: list[dict[str, Any]],
    *,
    preview_count: int,
    previews_dir: Path | None,
    current_index: int,
    complete: bool,
    failed: bool,
) -> list[dict[str, Any]]:
    labels = [short for _, short in STUDIO_PIPELINE]
    funnel = _pipeline_funnel_counts(
        preview_count=preview_count,
        previews_dir=previews_dir,
    )
    durs = _stage_durations_sec(job, events)

    fin = funnel or {}
    counts = {
        0: (fin.get("in"), fin.get("in")),
        1: (fin.get("in"), fin.get("s1")),
        2: (fin.get("s1"), fin.get("s2")),
        3: (fin.get("s2"), fin.get("s3")),
        4: (fin.get("s3"), fin.get("out")),
    }

    stages: list[dict[str, Any]] = []
    for i, label in enumerate(labels):
        if complete:
            state = "done"
        elif failed and i == current_index:
            state = "failed"
        elif current_index >= 0 and i < current_index:
            state = "done"
        elif current_index == i:
            state = "active"
        else:
            state = "pending"

        cin, cout = counts.get(i, (None, None))
        stages.append(
            {
                "label": label,
                "state": state,
                "count_in": cin,
                "count_out": cout,
                "duration_sec": durs[i] if i < len(durs) else None,
            }
        )
    return stages


def pipeline_view_with_stages(
    job: dict[str, Any] | None,
    events: list[dict[str, Any]],
    *,
    preview_count: int,
    previews_dir: Path | None,
) -> dict[str, Any]:
    view = pipeline_view_from_job(job, events)
    funnel = _pipeline_funnel_counts(
        preview_count=preview_count,
        previews_dir=previews_dir,
    )
    stages = pipeline_stages_detail(
        job,
        events,
        preview_count=preview_count,
        previews_dir=previews_dir,
        current_index=int(view.get("current_index") or -1),
        complete=bool(view.get("complete")),
        failed=bool(view.get("failed")),
    )
    view["stages"] = stages
    view["workflow_stages"] = photography_workflow_stages(
        funnel=funnel,
        events=events,
        current_index=int(view.get("current_index") or -1),
        complete=bool(view.get("complete")),
        failed=bool(view.get("failed")),
    )
    return view


def latest_job_for_previews(
    conn: sqlite3.Connection,
    *,
    previews_dir: str,
    brain_session_id: int | None,
    limit_events: int = 40,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Most recent analyze job + recent events for studio status."""
    job: dict[str, Any] | None = None

    runnable_id = find_runnable_analyze_job_id(
        conn,
        previews_dir=previews_dir,
        brain_session_id=brain_session_id,
    )
    if runnable_id is not None:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (runnable_id,)).fetchone()
        if row is not None:
            job = dict(row)

    if job is None and brain_session_id is not None:
        row = conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE session_id = ?
              AND job_type = 'ANALYZE_SESSION'
            ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
            LIMIT 1
            """,
            (brain_session_id,),
        ).fetchone()
        if row is not None:
            job = dict(row)
        else:
            row = conn.execute(
                """
                SELECT *
                FROM jobs
                WHERE session_id = ?
                  AND job_type = 'PIPELINE_STAGE'
                ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
                LIMIT 1
                """,
                (brain_session_id,),
            ).fetchone()
            if row is not None:
                job = dict(row)

    if job is None:
        rows = conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE job_type = 'ANALYZE_PATH'
            ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
            LIMIT 200
            """,
        ).fetchall()
        for row in rows:
            if _analyze_path_matches_previews(row["payload_json"], previews_dir):
                job = dict(row)
                break

    if job is None:
        return None, []

    job_id = int(job["id"])
    ev_rows = conn.execute(
        """
        SELECT id, job_id, from_status, to_status, created_at, message, payload_json
        FROM job_events
        WHERE job_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (job_id, max(1, limit_events)),
    ).fetchall()
    events = [dict(r) for r in reversed(ev_rows)]
    return job, events


def job_elapsed_seconds(job: dict[str, Any] | None) -> int | None:
    if not job:
        return None
    start = job.get("started_at") or job.get("claimed_at") or job.get("enqueued_at")
    end = job.get("finished_at")
    now = int(time.time())
    try:
        t0 = int(start) if start else None
    except (TypeError, ValueError):
        t0 = None
    if t0 is None:
        return None
    t1 = int(end) if end else now
    return max(0, t1 - t0)


def session_activity_label(
    job: dict[str, Any] | None,
    *,
    has_analysis_results: bool,
) -> str:
    if job:
        st = str(job.get("status") or "")
        if st in {"QUEUED", "CLAIMED", "PREPROCESSING", "INFERENCING", "POSTPROCESSING", "FAILED_RETRYABLE"}:
            return "running"
        if st == "SUCCEEDED" and has_analysis_results:
            return "analyzed"
        if st in {"FAILED_PERMANENT", "DEAD_LETTERED", "CANCELLED"}:
            return "failed"
    if has_analysis_results:
        return "analyzed"
    return "idle"


def collect_lifetime_stats(
    conn: sqlite3.Connection | None,
    archive_root: Path,
) -> dict[str, Any]:
    """Landing / Studio lifetime totals (brain SSOT with archive filesystem fallback)."""
    fs_sessions = scan_archive_session_dirs(archive_root)
    fs_session_count = len(fs_sessions)
    fs_photo_count = sum(int(s.get("preview_count") or 0) for s in fs_sessions)
    fs_exported = _sum_fs_exported_photos(fs_sessions)

    brain_session_count = 0
    brain_photo_count = 0
    brain_analyzed_count = 0
    if conn is not None:
        try:
            brain_session_count = int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
            brain_photo_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM photos WHERE status IN ('INGESTED', 'ANALYZING', 'ANALYZED')"
                ).fetchone()[0]
            )
            brain_analyzed_count = int(
                conn.execute("SELECT COUNT(*) FROM photos WHERE status = 'ANALYZED'").fetchone()[0]
            )
        except sqlite3.Error:
            brain_session_count = 0
            brain_photo_count = 0
            brain_analyzed_count = 0

    if brain_session_count > 0 or brain_photo_count > 0:
        sessions_total = max(brain_session_count, fs_session_count)
        photos_total = max(brain_photo_count, fs_photo_count)
        source = "brain" if brain_session_count >= fs_session_count else "mixed"
    else:
        sessions_total = fs_session_count
        photos_total = fs_photo_count
        source = "filesystem"

    exported_total = max(fs_exported, brain_analyzed_count)

    avg_processing_sec: int | None = None
    total_runtime_sec: int | None = None
    if conn is not None:
        try:
            row = conn.execute(
                """
                SELECT AVG(
                  CASE
                    WHEN total_latency_ms IS NOT NULL AND total_latency_ms > 0
                      THEN total_latency_ms / 1000.0
                    WHEN finished_at IS NOT NULL AND started_at IS NOT NULL AND finished_at > started_at
                      THEN (finished_at - started_at) * 1.0
                    WHEN updated_at IS NOT NULL AND created_at IS NOT NULL AND updated_at > created_at
                      THEN (updated_at - created_at) * 1.0
                    ELSE NULL
                  END
                )
                FROM jobs
                WHERE status = 'SUCCEEDED'
                  AND job_type IN ('ANALYZE_SESSION', 'ANALYZE_PATH')
                """
            ).fetchone()
            if row and row[0] is not None:
                avg_processing_sec = max(0, int(round(float(row[0]))))
        except sqlite3.Error:
            avg_processing_sec = None
        try:
            row = conn.execute(
                """
                SELECT SUM(
                  CASE
                    WHEN total_latency_ms IS NOT NULL AND total_latency_ms > 0
                      THEN total_latency_ms / 1000.0
                    WHEN finished_at IS NOT NULL AND started_at IS NOT NULL AND finished_at > started_at
                      THEN (finished_at - started_at) * 1.0
                    ELSE 0
                  END
                )
                FROM jobs
                WHERE status = 'SUCCEEDED'
                  AND job_type IN ('ANALYZE_SESSION', 'ANALYZE_PATH')
                """
            ).fetchone()
            if row and row[0] is not None:
                total_runtime_sec = max(0, int(round(float(row[0]))))
        except sqlite3.Error:
            total_runtime_sec = None

    auto_reject_rate_pct: int | None = None
    average_keep_rate_pct: int | None = None
    if photos_total > 0 and exported_total >= 0:
        keep = min(100.0, max(0.0, 100.0 * float(exported_total) / float(photos_total)))
        average_keep_rate_pct = int(round(keep))
        auto_reject_rate_pct = int(round(max(0.0, min(99.0, 100.0 - keep))))

    total_runtime_hours: float | None = None
    if total_runtime_sec is not None and total_runtime_sec > 0:
        total_runtime_hours = round(total_runtime_sec / 3600.0, 1)

    return {
        "sessions_total": sessions_total,
        "photos_total": photos_total,
        "exported_photos_total": exported_total,
        "avg_processing_sec": avg_processing_sec,
        "auto_reject_rate_pct": auto_reject_rate_pct,
        "average_keep_rate_pct": average_keep_rate_pct,
        "total_runtime_sec": total_runtime_sec,
        "total_runtime_hours": total_runtime_hours,
        # Legacy alias (Studio v1)
        "auto_filter_rate_pct": auto_reject_rate_pct,
        "source": source,
    }


def _sum_fs_exported_photos(fs_sessions: list[dict[str, Any]]) -> int:
    total = 0
    for row in fs_sessions:
        pd = str(row.get("previews_dir") or "").strip()
        if not pd:
            continue
        try:
            n = _count_analysis_export(Path(pd))
        except OSError:
            n = None
        if n is not None and n > 0:
            total += int(n)
    return total
