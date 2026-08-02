#!/usr/bin/env python3
"""Measure gallery_search empty-result modes: VLM-absent vs synonym-gap vs true-negative.

Answers: when text search returns nothing, is it because (a) the session has no
usable VLM text, (b) the concept exists in the corpus but the query phrasing
missed the synonym/expand table, or (c) the concept is genuinely absent?

Run::

    python -m scripts.eval.measure_gallery_search_gaps
    python -m scripts.eval.measure_gallery_search_gaps \\
        --session-dir data/eval/images --label recorded_250
    python -m scripts.eval.measure_gallery_search_gaps --json \\
        --out reports/eval/gallery_search_gaps.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality.agent_cases import load_agent_cases, session_dir  # noqa: E402
from services.agent.skills.gallery import (  # noqa: E402
    GallerySearchSkill,
    _QUERY_SYNONYMS,
    _expand_query_terms,
    _is_boilerplate_reason,
    _is_pipeline_tag,
    _load_rows,
    _query_hit_score,
    _text_blob,
)

# Probe pack: (query, family, notes)
# family groups paraphrases that should hit the same visual/semantic concept when present.
_PROBE_QUERIES: list[tuple[str, str, str]] = [
    # In-table Chinese (should expand to English tags)
    ("鼓手特写", "drummer", "case search_drummer"),
    ("鼓手", "drummer", "case grounding"),
    ("吉他手", "guitarist", "case search_guitarist"),
    ("吉他", "guitarist", "case repeat_tool"),
    ("舞台全景", "wide_stage", "case search_wide_stage"),
    ("全景", "wide_stage", "case compound panorama"),
    ("贝斯手", "bassist", "case json_leak"),
    ("萨克斯风", "saxophone", "case empty_semantic_query (expect absent)"),
    # OOV / near-miss paraphrases (concept may exist; phrasing not in synonym table)
    ("架子鼓那哥们", "drummer", "OOV paraphrase"),
    ("打鼓的人", "drummer", "OOV paraphrase"),
    ("percussion", "drummer", "OOV English; not in synonym table"),
    ("六弦", "guitarist", "OOV slang"),
    ("电吉他手", "guitarist", "partial OOV"),
    ("大场面俯拍", "wide_stage", "OOV paraphrase"),
    ("establishing shot", "wide_stage", "in-table English via 全景 group only if trigger hits"),
    ("主唱剪影", "singer_backlight", "compound; singer+backlight groups"),
    ("歌手", "singer", "in-table"),
    ("观众灯海", "crowd", "in-table"),
    ("sax", "saxophone", "OOV short; expect absent on livehouse sets"),
    ("saxophone", "saxophone", "OOV English; expect absent"),
]


@dataclass
class SessionCorpusStats:
    path: str
    label: str
    n_rows: int
    vlm_content_count: int
    pipeline_only: bool
    rows_with_semantic_tags: int
    rows_with_content_text: int
    semantic_tag_vocab: list[dict[str, Any]]
    text_token_samples: list[str]


@dataclass
class ProbeResult:
    query: str
    family: str
    notes: str
    hit_count: int
    top_files: list[str]
    expanded_terms: list[str]
    concept_present_in_corpus: bool
    oracle_hit_count: int
    classification: str
    # hit | empty_vlm_absent | empty_synonym_gap | empty_true_negative | empty_other


def _content_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for t in row.get("tags") or []:
        if not _is_pipeline_tag(str(t)):
            parts.append(str(t))
    cap = str(row.get("caption") or "").strip()
    if cap and not _is_boilerplate_reason(cap):
        parts.append(cap)
    rb = row.get("reason_bilingual") if isinstance(row.get("reason_bilingual"), dict) else {}
    for side in ("zh", "en"):
        s = str(rb.get(side) or "").strip()
        if s and not _is_boilerplate_reason(s):
            parts.append(s)
    reason = str(row.get("reason") or "").strip()
    if reason and not _is_boilerplate_reason(reason):
        parts.append(reason)
    return " ".join(parts).lower()


def analyze_corpus(rows: list[dict[str, Any]], *, path: str, label: str) -> SessionCorpusStats:
    tag_counts: Counter[str] = Counter()
    vlm = 0
    sem_tag_rows = 0
    content_text_rows = 0
    samples: list[str] = []
    for r in rows:
        sem_tags = [str(t).strip() for t in (r.get("tags") or []) if str(t).strip() and not _is_pipeline_tag(str(t))]
        text = _content_text(r)
        if sem_tags:
            sem_tag_rows += 1
            for t in sem_tags:
                tag_counts[t] += 1
        if text.strip():
            content_text_rows += 1
            if len(samples) < 8:
                samples.append(text.strip()[:120])
        if sem_tags or text.strip():
            vlm += 1
    return SessionCorpusStats(
        path=path,
        label=label,
        n_rows=len(rows),
        vlm_content_count=vlm,
        pipeline_only=(vlm == 0 and len(rows) > 0),
        rows_with_semantic_tags=sem_tag_rows,
        rows_with_content_text=content_text_rows,
        semantic_tag_vocab=[{"tag": k, "count": v} for k, v in tag_counts.most_common(24)],
        text_token_samples=samples,
    )


def _family_terms(family: str) -> list[str]:
    """Oracle terms that indicate the concept is present in corpus text."""
    # Map families onto synonym groups + a few extra oracles.
    family_to_triggers: dict[str, tuple[str, ...]] = {
        "drummer": ("鼓手", "drummer", "drums", "drum", "打鼓", "架子鼓"),
        "guitarist": ("吉他", "guitar", "guitarist", "六弦"),
        "wide_stage": ("全景", "wide stage", "panorama", "establishing", "大场面"),
        "bassist": ("贝斯", "bass", "bassist"),
        "singer": ("歌手", "主唱", "singer", "vocalist", "vocals"),
        "singer_backlight": ("歌手", "主唱", "singer", "剪影", "逆光", "silhouette", "backlight"),
        "crowd": ("观众", "crowd", "audience", "灯海", "pit"),
        "saxophone": ("萨克斯", "sax", "saxophone"),
    }
    triggers = family_to_triggers.get(family, (family,))
    terms: list[str] = []
    seen: set[str] = set()
    for t in triggers:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            terms.append(tl)
        for group in _QUERY_SYNONYMS:
            if any(k.lower() == tl or tl in k.lower() or k.lower() in tl for k in group):
                for k in group:
                    kl = k.lower()
                    if kl not in seen:
                        seen.add(kl)
                        terms.append(kl)
    return terms


def concept_present(rows: list[dict[str, Any]], family: str) -> tuple[bool, int]:
    terms = _family_terms(family)
    hits = 0
    for r in rows:
        blob = _text_blob(r)
        if _query_hit_score(blob, terms) > 0:
            hits += 1
    return hits > 0, hits


def classify_probe(
    *,
    hit_count: int,
    corpus: SessionCorpusStats,
    concept_in_corpus: bool,
    family: str,
) -> str:
    if hit_count > 0:
        return "hit"
    if corpus.pipeline_only or corpus.vlm_content_count == 0:
        return "empty_vlm_absent"
    if concept_in_corpus:
        return "empty_synonym_gap"
    # Sax / absent instruments on a rock livehouse set → true negative.
    if family in {"saxophone"} or family.endswith("_absent"):
        return "empty_true_negative"
    return "empty_true_negative" if not concept_in_corpus else "empty_other"


def probes_from_agent_cases() -> list[tuple[str, str, str]]:
    """Pull query strings from scripted gallery_search tool calls in cases."""
    out: list[tuple[str, str, str]] = []
    for case in load_agent_cases():
        tags = set(case.get("tags") or [])
        if "search" not in tags and "semantic" not in tags:
            continue
        for raw in case.get("model_queue") or []:
            try:
                obj = json.loads(raw) if isinstance(raw, str) and raw.strip().startswith("{") else None
            except json.JSONDecodeError:
                obj = None
            if not isinstance(obj, dict) or obj.get("tool") != "gallery_search":
                continue
            args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
            q = str(args.get("query") or "").strip()
            if not q:
                continue
            fam = "other"
            for cand_q, cand_f, _ in _PROBE_QUERIES:
                if cand_q in q or q in cand_q:
                    fam = cand_f
                    break
            else:
                for group_name, triggers in (
                    ("drummer", ("鼓", "drum")),
                    ("guitarist", ("吉他", "guitar")),
                    ("wide_stage", ("全景", "wide", "panorama")),
                    ("bassist", ("贝斯", "bass")),
                    ("saxophone", ("萨克斯", "sax")),
                ):
                    if any(t in q.lower() for t in triggers):
                        fam = group_name
                        break
            out.append((q, fam, f"case:{case.get('id')}"))
    return out


def run_session(
    *,
    session_path: Path,
    label: str,
    extra_probes: Optional[list[tuple[str, str, str]]] = None,
) -> dict[str, Any]:
    rows = _load_rows(str(session_path))
    corpus = analyze_corpus(rows, path=str(session_path), label=label)
    skill = GallerySearchSkill(str(session_path))

    probes = list(_PROBE_QUERIES)
    if extra_probes:
        seen = {(q, f) for q, f, _ in probes}
        for q, f, n in extra_probes:
            if (q, f) not in seen:
                probes.append((q, f, n))
                seen.add((q, f))

    results: list[ProbeResult] = []
    for query, family, notes in probes:
        present, oracle_n = concept_present(rows, family)
        meta = skill.run({"query": query, "limit": 8}).metadata or {}
        files = [str(f) for f in (meta.get("files") or [])]
        hit_n = len(files)
        classification = classify_probe(
            hit_count=hit_n,
            corpus=corpus,
            concept_in_corpus=present,
            family=family,
        )
        results.append(
            ProbeResult(
                query=query,
                family=family,
                notes=notes,
                hit_count=hit_n,
                top_files=files[:5],
                expanded_terms=_expand_query_terms(query)[:16],
                concept_present_in_corpus=present,
                oracle_hit_count=oracle_n,
                classification=classification,
            )
        )

    counts = Counter(r.classification for r in results)
    empty_n = sum(1 for r in results if r.classification.startswith("empty_"))
    gap_share = (
        round(counts.get("empty_synonym_gap", 0) / empty_n, 3) if empty_n else None
    )
    vlm_share = (
        round(counts.get("empty_vlm_absent", 0) / empty_n, 3) if empty_n else None
    )
    return {
        "label": label,
        "session": asdict(corpus),
        "summary": {
            "probes": len(results),
            "hits": counts.get("hit", 0),
            "empty_total": empty_n,
            "empty_vlm_absent": counts.get("empty_vlm_absent", 0),
            "empty_synonym_gap": counts.get("empty_synonym_gap", 0),
            "empty_true_negative": counts.get("empty_true_negative", 0),
            "empty_other": counts.get("empty_other", 0),
            "empty_synonym_gap_share_of_empty": gap_share,
            "empty_vlm_absent_share_of_empty": vlm_share,
            "hit_rate": round(counts.get("hit", 0) / len(results), 3) if results else 0.0,
            "vlm_content_rate": round(corpus.vlm_content_count / corpus.n_rows, 3)
            if corpus.n_rows
            else 0.0,
        },
        "probes": [asdict(r) for r in results],
        "synonym_gap_examples": [
            asdict(r) for r in results if r.classification == "empty_synonym_gap"
        ],
        "hit_examples": [asdict(r) for r in results if r.classification == "hit"][:8],
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--session-dir",
        action="append",
        default=[],
        help="Previews-like dir containing analysis_results.json (repeatable). "
        "Default: smoke fixture + data/eval/images when present.",
    )
    p.add_argument("--label", action="append", default=[], help="Label aligned with --session-dir")
    p.add_argument("--include-cases", action="store_true", default=True)
    p.add_argument("--no-cases", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)

    sessions: list[tuple[Path, str]] = []
    if args.session_dir:
        for i, raw in enumerate(args.session_dir):
            path = Path(raw)
            label = args.label[i] if i < len(args.label) else path.name
            sessions.append((path, label))
    else:
        sessions.append((session_dir("smoke"), "smoke_fixture"))
        images = ROOT / "data" / "eval" / "images"
        if (images / "analysis_results.json").is_file():
            sessions.append((images, "recorded_250"))

    extra = [] if args.no_cases else probes_from_agent_cases()
    reports = [run_session(session_path=path, label=label, extra_probes=extra) for path, label in sessions]
    doc = {
        "schema_version": "gallery_search_gaps.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": (
            "When gallery_search returns empty, is it synonym-table miss "
            "or missing VLM text (tags/captions/reasons)?"
        ),
        "sessions": reports,
        "takeaway": _takeaway(reports),
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.out}")

    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        _print_human(doc)
    return 0


def _takeaway(reports: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for r in reports:
        s = r["summary"]
        sess = r["session"]
        lines.append(
            f"{r['label']}: vlm_content_rate={s['vlm_content_rate']} "
            f"(tags={sess['rows_with_semantic_tags']}/{sess['n_rows']}, "
            f"text={sess['rows_with_content_text']}/{sess['n_rows']}); "
            f"hit_rate={s['hit_rate']}; "
            f"empty={{vlm_absent:{s['empty_vlm_absent']}, "
            f"synonym_gap:{s['empty_synonym_gap']}, "
            f"true_neg:{s['empty_true_negative']}}}"
        )
        if s["empty_vlm_absent"] and s["empty_vlm_absent"] >= s["empty_synonym_gap"]:
            lines.append(
                f"  → {r['label']}: empty results dominated by missing/unusable VLM text, "
                "not synonym gaps. Vector search would also starve without content signals "
                "(or needs image-CLIP, not text embeddings over empty tags)."
            )
        if s["empty_synonym_gap"]:
            gaps = ", ".join(
                f"{x['query']!r}→{x['family']}" for x in r.get("synonym_gap_examples") or []
            )
            lines.append(f"  → synonym_gap probes: {gaps}")
    return lines


def _print_human(doc: dict[str, Any]) -> None:
    print("Gallery search gap measurement")
    print(f"generated_at: {doc['generated_at']}")
    print(f"question: {doc['question']}")
    print()
    for r in doc["sessions"]:
        s = r["summary"]
        sess = r["session"]
        print(f"## {r['label']}  ({sess['path']})")
        print(
            f"  rows={sess['n_rows']}  vlm_content={sess['vlm_content_count']} "
            f"({s['vlm_content_rate']:.0%})  "
            f"semantic_tags_rows={sess['rows_with_semantic_tags']}  "
            f"content_text_rows={sess['rows_with_content_text']}"
        )
        if sess["semantic_tag_vocab"]:
            top = ", ".join(f"{t['tag']}×{t['count']}" for t in sess["semantic_tag_vocab"][:8])
            print(f"  top tags: {top}")
        else:
            print("  top tags: (none — search leans on caption/reason text)")
        print(
            f"  probes={s['probes']}  hits={s['hits']} ({s['hit_rate']:.0%})  "
            f"empty={s['empty_total']} "
            f"[vlm_absent={s['empty_vlm_absent']}, "
            f"synonym_gap={s['empty_synonym_gap']}, "
            f"true_neg={s['empty_true_negative']}]"
        )
        if r.get("synonym_gap_examples"):
            print("  synonym gaps:")
            for g in r["synonym_gap_examples"]:
                print(
                    f"    - {g['query']!r} family={g['family']} "
                    f"oracle_rows={g['oracle_hit_count']} expanded={g['expanded_terms'][:6]}"
                )
        print()
    print("Takeaway:")
    for line in doc["takeaway"]:
        print(f"  {line}")


if __name__ == "__main__":
    raise SystemExit(main())
