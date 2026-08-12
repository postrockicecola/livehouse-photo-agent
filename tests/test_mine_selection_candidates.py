from __future__ import annotations

import json
from pathlib import Path

from scripts.eval.mine_selection_candidates import (
    Candidate,
    category_scores,
    load_session_candidates,
    parse_quotas,
    select_candidates,
)
from scripts.eval.prepare_candidate_relabel import prepare_relabel_inputs
from scripts.eval.triage_candidate_suggestions import provisional_category


def _candidate(
    name: str,
    *,
    session: str = "2026-05-01",
    score: float | None = None,
    fast: float | None = None,
    tech: float | None = None,
    debug: dict | None = None,
    dimensions: dict[str, float] | None = None,
    tags: list[str] | None = None,
    phash: int = 0,
) -> Candidate:
    return Candidate(
        session=session,
        session_tag=session.replace("-", ""),
        name=name,
        source_path=Path("/tmp") / name,
        historical_score=score,
        fast_score=fast,
        tech_score=tech,
        dimensions=dimensions or {},
        debug_info=debug or {},
        tags=tags or [],
        text="",
        phash=phash,
    )


def test_category_scores_separate_sampling_strata() -> None:
    technical = _candidate("blur.jpg", debug={"laplacian_var": 4})
    semantic = _candidate(
        "semantic.jpg",
        debug={"face_count": 1, "face_area_ratio": 0.02, "composition_score": 30},
        tags=["bad_expression"],
    )
    ordinary = _candidate("ordinary.jpg", score=63, fast=60, tech=90)
    highlight = _candidate(
        "highlight.jpg",
        score=88,
        fast=82,
        dimensions={"moment_peak": 8.5, "atmosphere_impact": 8.0},
    )

    assert category_scores(technical)["technical_hard"][0] > 40
    assert category_scores(semantic)["semantic_defect"][0] > 40
    assert category_scores(ordinary)["ordinary"][0] > 40
    assert category_scores(highlight)["highlight"][0] > 30


def test_semantic_development_ceiling_widens_candidate_recall() -> None:
    borderline = _candidate(
        "borderline.jpg",
        dimensions={
            "deliverable_subject": 5.8,
            "composition_framing": 6.2,
            "moment_peak": 5.5,
        },
    )

    assert category_scores(borderline)["semantic_defect"][0] == 0
    assert (
        category_scores(borderline, semantic_dim_ceiling=6.5)["semantic_defect"][0]
        > 0
    )


def test_select_candidates_excludes_existing_and_dedupes_phash() -> None:
    candidates = [
        _candidate("existing.jpg", score=90, phash=1),
        _candidate("first.jpg", score=89, phash=3),
        _candidate("duplicate.jpg", score=88, phash=3),
        _candidate("other.jpg", session="2026-05-02", score=87, phash=3),
    ]
    quotas = {
        "technical_hard": 0,
        "semantic_defect": 0,
        "ordinary": 0,
        "highlight": 3,
    }

    selected = select_candidates(
        candidates,
        quotas=quotas,
        excluded={"20260501__existing"},
        seed=1,
        max_per_session=3,
        max_hamming=0,
        min_file_number_gap=0,
    )

    assert [row["file"] for row in selected] == [
        "20260501__first.jpg",
        "20260502__other.jpg",
    ]


def test_select_candidates_suppresses_nearby_burst_filenames() -> None:
    candidates = [
        _candidate("DSC01000.jpg", score=90, phash=1),
        _candidate("DSC01007.jpg", score=89, phash=16),
        _candidate("DSC01030.jpg", score=88, phash=256),
    ]
    quotas = {
        "technical_hard": 0,
        "semantic_defect": 0,
        "ordinary": 0,
        "highlight": 3,
    }

    selected = select_candidates(
        candidates,
        quotas=quotas,
        excluded=set(),
        seed=1,
        max_per_session=3,
        max_hamming=0,
        min_file_number_gap=25,
    )

    assert [row["file"] for row in selected] == [
        "20260501__DSC01000.jpg",
        "20260501__DSC01030.jpg",
    ]


def test_load_session_candidates_joins_stage2_metrics(tmp_path) -> None:
    session = tmp_path / "2026-06-01"
    previews = session / "Previews"
    staged = previews / ".luma_pipeline_staged"
    staged.mkdir(parents=True)
    (previews / "DSC0001.jpg").write_bytes(b"jpeg")
    (previews / "analysis_results.json").write_text(
        json.dumps(
            [
                {
                    "file": "DSC0001.jpg",
                    "overall_score": 72,
                    "scores": {"laplacian": 12},
                    "tags": ["stage2_prefilter"],
                    "phash": 123,
                }
            ]
        ),
        encoding="utf-8",
    )
    (staged / "eligible_after_stage2.jsonl").write_text(
        json.dumps(
            {
                "file_name": "DSC0001.jpg",
                "tech_score": 80,
                "fast_score": 65,
                "debug_info": {"face_count": 0},
            }
        ),
        encoding="utf-8",
    )

    candidates = load_session_candidates(session)

    assert len(candidates) == 1
    assert candidates[0].tech_score == 80
    assert candidates[0].debug_info["laplacian_var"] == 12
    assert candidates[0].phash == 123


def test_parse_quotas_applies_overrides() -> None:
    quotas = parse_quotas(["technical_hard=45", "highlight=55"])

    assert quotas["technical_hard"] == 45
    assert quotas["semantic_defect"] == 80
    assert quotas["highlight"] == 55


def test_prepare_relabel_inputs_preserves_prefixed_filename(tmp_path) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"jpeg")
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        json.dumps(
            {
                "file": "20260501__DSC0001.jpg",
                "source_path": str(source),
                "session": "2026-05-01",
                "target_category": "technical_hard",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    images_dir = tmp_path / "relabel_images"
    manifest = tmp_path / "manifest.json"

    count = prepare_relabel_inputs(candidates, images_dir, manifest)

    link = images_dir / "20260501__DSC0001.jpg"
    assert count == 1
    assert link.is_symlink()
    assert link.resolve() == source.resolve()
    assert json.loads(manifest.read_text())["items"][0]["file"] == link.name


def test_provisional_category_uses_strict_mutually_exclusive_rules() -> None:
    base_dims = {
        "focus_sharpness": 6,
        "exposure_control": 6,
        "noise_cleanliness": 6,
        "composition_framing": 6,
        "light_color_character": 6,
        "moment_peak": 6,
        "atmosphere_impact": 6,
        "deliverable_subject": 6,
    }
    technical = {**base_dims, "focus_sharpness": 2}
    semantic = {**base_dims, "deliverable_subject": 2}

    assert provisional_category({"overall": 40, "dims": technical})[0] == "technical_hard"
    assert provisional_category({"overall": 40, "dims": semantic})[0] == "semantic_defect"
    assert (
        provisional_category(
            {"overall": 40, "dims": semantic, "reason": "城市夜景，无演出主体"}
        )[0]
        == "out_of_domain"
    )
    assert (
        provisional_category(
            {
                "overall": 65,
                "dims": base_dims,
                "semantic_defect": {
                    "is_present": True,
                    "types": ["closed_eyes"],
                    "severity": "major",
                },
            }
        )[0]
        == "semantic_defect"
    )
    assert provisional_category({"overall": 80, "dims": base_dims})[0] == "highlight"
    assert provisional_category({"overall": 65, "dims": base_dims})[0] == "ordinary"
