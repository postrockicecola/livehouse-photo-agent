"""Regression coverage for score provenance, exact-content caching and CV signals."""
from __future__ import annotations

import json
from concurrent.futures import Future
from pathlib import Path
from threading import Lock

import cv2
import numpy as np
import pytest
from PIL import Image

from engine.operators.image_processor import ImageProcessor as Processor
from engine.operators.stage2_prefilter import (
    assess_stage2_per_image, dedupe_eligible_rows_by_phash, image_phash_int,
)
from services.cache.stage3_cache import Stage3PHashCache, stage3_content_key
from services.processor.pipeline_image_ops import (
    append_aesthetic_audit_line, apply_stage3_candidates_gating,
    fake_result_stage1_reject, fake_result_stage2_reject,
    fake_result_stage3_gated_skip, finalize_stage3_dual_result,
)
from services.processor.reporting.gallery_integration import build_analysis_results
from services.processor.stages.deep_analysis import (
    Stage3FastFirstHooks, _stage3_cache_key, analyze_with_dimensions,
    run_stage3_fast_first, stage3_strategy_settings,
)
from services.processor.stages.stage3_postprocess import calibrate_dimension_scores
from services.result_service import _read_live_audit_rows_cached, _sort_metric, normalize_scores
from utils.stage3_dimensions import STAGE3_DIM_KEYS


def config():
    return {
        "evaluation": {"technical_weight": 0.2, "ai_weight": 0.8},
        "stage3": {"strategy": "fast_first", "full_analysis_top_k": 5},
        "stage4_editing": {"enabled": False},
        "processing": {},
    }


class Client:
    def __init__(self, full_text=None):
        self.full_text = full_text if full_text is not None else json.dumps({k: 6 for k in STAGE3_DIM_KEYS})
        self.fast_calls = self.full_calls = 0

    def infer_fast_future(self, *args, **kwargs):
        self.fast_calls += 1
        future = Future()
        future.set_result({"status": "success", "text": '{"score":90,"verdict":"great","tags":[]}'})
        return future

    def infer_full(self, *args, **kwargs):
        self.full_calls += 1
        return {"status": "success", "text": self.full_text}

    def infer_full_future(self, *args, **kwargs):
        future = Future()
        future.set_result(self.infer_full())
        return future


def run(client, cfg, image, cache=None):
    rows = []
    stats = {"processed": 0, "vlm_fallback": 0}
    hooks = Stage3FastFirstHooks(
        append_audit_line=lambda _, row: rows.append(row), progress_lock=Lock(), stats=stats,
        trace_id=None, job_id=None, session_id=None, photo_id=None, worker_id=None,
        model_provider="mock", model_name="mock",
    )
    tasks = [(1, 1, image.name, str(image), 100.0, {"blur_type": None}, 0, cache)]
    run_stage3_fast_first(client, cfg, tasks, 1, stage3_strategy_settings(cfg), hooks)
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize("full_text", [None, "invalid JSON"])
def test_full_followup_never_changes_fast_ranking(tmp_path, full_text):
    image = tmp_path / "a.jpg"
    image.write_bytes(b"mock image")
    client = Client(full_text)
    out = run(client, config(), image)
    assert out["score"] == out["stage3_result"]["score"] == 92.0
    assert out["score_source"] == "vlm_fast"
    assert out["score_status"] == "scored"
    if full_text is None:
        assert out["full_analysis"]["ai_score"] == pytest.approx(60)
        assert out["stage3_result"]["confidence"] == 0.7
    else:
        assert client.full_calls == 2  # Strict parse retry also failed.
        assert out["stage3_meta"]["full_analysis_status"] == "unavailable"
        assert out["stage3_meta"]["full_stage3_meta"]["used_fallback_defaults"]


def test_invalid_full_is_error_and_both_invalid_are_unscored():
    failed = analyze_with_dimensions(Client("invalid"), config(), "a.jpg", max_retries=0)
    assert failed["error"] is True
    assert failed["stage3_result"]["confidence"] == 0
    out = finalize_stage3_dual_result(
        config=config(), tech_score=100, debug_info={}, fast_inner=failed, full_inner=failed,
    )
    assert out["score_status"] == "unscored"
    assert out["score"] == 0


def test_full_only_keeps_dimensional_score():
    full = analyze_with_dimensions(Client(), config(), "a.jpg", max_retries=0)
    out = finalize_stage3_dual_result(
        config=config(), tech_score=100, debug_info={}, fast_inner=None, full_inner=full,
    )
    assert out["score"] == 68
    assert out["score_source"] == "vlm_full"


def test_fast_first_cache_roundtrip_blends_once(tmp_path):
    image = tmp_path / "a.jpg"
    image.write_bytes(b"mock image")
    cfg = config()
    cache = Stage3PHashCache()
    client = Client()
    first = run(client, cfg, image, cache)
    second = run(client, cfg, image, cache)
    assert client.fast_calls == client.full_calls == 1
    assert second["score"] == first["score"] == 92
    assert second["stage3_result"] == first["stage3_result"]
    assert second["stage3_meta"]["outcome"] == "cache_hit"
    cfg["model"] = {"model_name": "different-model"}
    run(client, cfg, image, cache)
    assert client.fast_calls == client.full_calls == 2


def test_phash_collision_never_reuses_score(tmp_path):
    gray = np.tile(np.arange(64, dtype=np.uint8), (64, 1)) + 50
    images = [cv2.cvtColor(gray + lift, cv2.COLOR_GRAY2BGR) for lift in (0, 30)]
    assert image_phash_int(images[0]) == image_phash_int(images[1])
    paths = [tmp_path / "a.png", tmp_path / "b.png"]
    for path, image in zip(paths, images):
        Image.fromarray(image).save(path)
    keys = [stage3_content_key(str(p), {"model": "one"}) for p in paths]
    assert keys[0] != keys[1]
    cache = Stage3PHashCache(max_hamming=64)
    cache.store_result(keys[0], {"score": 90})
    assert cache.get_cached_result(keys[1]) is None
    renamed = tmp_path / "renamed.png"
    renamed.write_bytes(paths[0].read_bytes())
    assert stage3_content_key(str(renamed), {"model": "one"}) == keys[0]
    assert stage3_content_key(str(paths[0]), {"model": "two"}) != keys[0]
    hit = cache.get_cached_result(keys[0])
    hit["score"] = 1
    assert cache.get_cached_result(keys[0])["score"] == 90


@pytest.mark.parametrize("section", ["model", "stage3", "evaluation", "stage4_editing"])
def test_scoring_config_and_mode_invalidate_cache(tmp_path, section):
    image = tmp_path / "a.jpg"
    image.write_bytes(b"image")
    cfg = config()
    key = _stage3_cache_key(cfg, str(image), None, {}, mode="fast_first")
    cfg.setdefault(section, {})["test_change"] = True
    assert key != _stage3_cache_key(cfg, str(image), None, {}, mode="fast_first")
    assert key != _stage3_cache_key(config(), str(image), None, {}, mode="full")
    assert key != _stage3_cache_key(config(), str(image), None, {"noise_sigma": 20}, mode="fast_first")


def test_cache_persistence_lru_and_invalid_results(tmp_path):
    keys = [f"{i:064x}" for i in range(4)]
    cache = Stage3PHashCache(max_entries=2)
    for key in keys[:2]:
        cache.store_result(key, {"score": 50})
    cache.get_cached_result(keys[0])  # Make key 1 the oldest.
    cache.store_result(keys[2], {"score": 60})
    assert cache.get_cached_result(keys[1]) is None
    cache.store_result(keys[3], {"score": 0, "stage3_meta": {"used_fallback_defaults": True}})
    assert cache.get_cached_result(keys[3]) is None
    path = tmp_path / "cache.json"
    cache.save_to_json(path)
    loaded = Stage3PHashCache()
    assert loaded.load_from_json(path) == 2
    assert loaded.get_cached_result(keys[0])["score"] == 50
    path.write_text(json.dumps({"version": 2, "hash_algo": "phash", "entries": {"123": {"score": 90}}}))
    assert Stage3PHashCache().load_from_json(path) == 0


def test_unscored_status_survives_audit_json_and_live_gallery(tmp_path):
    image = tmp_path / "a.jpg"
    Image.new("RGB", (32, 24), "gray").save(image)
    folders = {k: tmp_path / k for k in ("best", "keep", "trash")}
    for folder in folders.values():
        folder.mkdir()
    audit = tmp_path / "aesthetic_audit.jsonl"
    skipped = fake_result_stage3_gated_skip(tech_score=100, fast_score=90, debug_info={})
    assert skipped["candidate_priority"] == 96
    append_aesthetic_audit_line(
        config=config(), folders=folders, log_paths={"log_file": audit}, file_lock=None,
        image_path=str(image), ai_data=skipped,
    )
    assert (folders["keep"] / image.name).exists()
    assert not (folders["best"] / image.name).exists()
    row = build_analysis_results(tmp_path, folders, audit)[0]
    live = _read_live_audit_rows_cached(str(tmp_path))[0]
    for entry in (row, live):
        assert entry["score_status"] == "unscored"
        assert entry["category"] == "unscored"
        assert entry["overall_score"] == 0
        assert _sort_metric(entry, "overall") < _sort_metric({"overall_score": 0}, "overall")
    legacy = normalize_scores({"overall_score": 96, "stage3_meta": {"outcome": "skipped_stage3_gating"}})
    assert legacy["score_status"] == "unscored" and legacy["overall_score"] == 0


def test_exposure_mass_and_clipping_are_separate():
    dark = np.tile(np.arange(5, 30, dtype=np.uint8), (32, 1))
    bright = np.tile(np.arange(231, 255, dtype=np.uint8), (32, 1))
    low = Processor._exposure_percentile_scores(dark, {})
    high = Processor._exposure_percentile_scores(bright, {})
    assert low["shadow_frac"] == high["highlight_frac"] == 1
    assert low["black_clip_frac"] == 0
    assert high["white_clip_frac"] == pytest.approx(1 / 24)


@pytest.mark.parametrize("stage", [1, 2])
def test_rejected_frame_cannot_enter_best_with_high_heuristic_score(tmp_path, stage):
    image = tmp_path / "rejected.jpg"
    Image.new("RGB", (32, 24), "gray").save(image)
    folders = {k: tmp_path / k for k in ("best", "keep", "trash")}
    for folder in folders.values():
        folder.mkdir()
    audit = tmp_path / "aesthetic_audit.jsonl"
    rejected = (
        fake_result_stage1_reject(tech_score=100, reason="blank", debug_info={})
        if stage == 1 else fake_result_stage2_reject(
            tech_score=100, fast_score=100, debug_info={}, reject_detail="stage2_hard_blur",
        )
    )
    append_aesthetic_audit_line(
        config=config(), folders=folders, log_paths={"log_file": audit}, file_lock=None,
        image_path=str(image), ai_data=rejected,
    )
    assert (folders["trash"] / image.name).exists()
    assert not (folders["best"] / image.name).exists()
    row = build_analysis_results(tmp_path, folders, audit)[0]
    live = _read_live_audit_rows_cached(str(tmp_path))[0]
    for entry in (row, live):
        assert entry["score_status"] == "rejected"
        assert entry["overall_score"] == 0
        assert entry["candidate_priority"] == 100
        assert _sort_metric(entry, "overall") < 0


def test_resolution_consistent_and_stage2_uses_stage1_metrics(tmp_path):
    gray = np.full((256, 384), 35, np.uint8)
    cv2.rectangle(gray, (40, 30), (160, 225), 180, -1)
    cv2.circle(gray, (270, 130), 60, 230, -1)
    image = cv2.cvtColor(cv2.GaussianBlur(gray, (0, 0), 1.5), cv2.COLOR_GRAY2BGR)
    results = []
    for factor in (1, 2, 4):
        pixels = cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
        path = tmp_path / f"{factor}.png"
        Image.fromarray(pixels).save(path)
        ok, reason, tech, dbg = Processor.assess_image_quality(str(path), {}, img_bgr=pixels)
        assert ok, reason
        cfg = {"processing": {"stage2_prefilter": {"enabled": True, "face_soft_gate": False}}}
        stage2 = assess_stage2_per_image(str(path), cfg, tech_score=tech, debug_info=dbg, img_bgr=pixels)
        assert stage2.prefilter_ok, stage2.prefilter_reason
        assert stage2.debug_extra["stage2_laplacian_var"] == dbg["laplacian_var"]
        results.append((tech, dbg))
    assert len({t for t, _ in results}) == 1
    assert len({d["blur_type"] for _, d in results}) == 1
    laps = [d["laplacian_var"] for _, d in results]
    assert max(laps) / min(laps) < 1.15


def test_noise_does_not_inflate_focus_and_haze_is_not_noise(tmp_path):
    rng = np.random.default_rng(3)
    gray = np.clip(100 + rng.normal(0, 15, (1024, 1024)), 0, 255).astype(np.uint8)
    image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    path = tmp_path / "noise.png"
    Image.fromarray(image).save(path)
    _, _, _, dbg = Processor.assess_image_quality(str(path), {}, img_bgr=image)
    assert dbg["noise_sigma"] == pytest.approx(15, abs=1)
    assert dbg["laplacian_var"] < dbg["laplacian_var_raw"] * 0.1
    raw = {k: 9 for k in STAGE3_DIM_KEYS}
    noisy, _ = calibrate_dimension_scores(raw, dbg, None)
    clean, _ = calibrate_dimension_scores(raw, {"contrast": 5, "edge_ratio": 0.001, "noise_sigma": 0}, None)
    assert noisy["noise_cleanliness"] < 7.2
    assert clean["noise_cleanliness"] == 9


def test_candidate_priority_has_no_saturation_or_bright_centroid_bonus():
    gray = np.tile(np.arange(256, dtype=np.uint8), (128, 1))
    mono = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    color = mono.copy()
    color[:, :, 0] = 255
    assert Processor.fast_aesthetic_assessment("", img_bgr=mono, gray=gray) == Processor.fast_aesthetic_assessment("", img_bgr=color, gray=gray)
    assert Processor.fast_aesthetic_assessment("", img_bgr=mono) == Processor.fast_aesthetic_assessment("", img_bgr=np.roll(mono, 60, axis=1))


def row(name, score, digest=None, **signals):
    return {"file_name": name, "tech_score": score, "fast_score": score, "phash": 123,
            "debug_info": {"content_digest": digest, **signals}}


def test_early_dedup_only_removes_identical_content():
    rows = [row("a", 90, "one"), row("b", 80, "two"), row("c", 75, "one"), row("d", 70)]
    cfg = {"processing": {"stage2_prefilter": {"phash_near_dup": {"enabled": True, "keep_per_cluster": 1}}}}
    kept, removed, _ = dedupe_eligible_rows_by_phash(rows, cfg)
    assert {r["file_name"] for r in kept} == {"a", "b", "d"}
    assert [r["file_name"] for r in removed] == ["c"]


def test_candidate_diversity_reserve_budget_and_determinism():
    rows = [row(f"{i:03d}", 95 - i, brightness=180, saturation=200) for i in range(16)]
    rows += [row(f"{i:03d}", 40, brightness=10, saturation=0) for i in range(16, 24)]
    cfg = {"processing": {"stage3_gating": {
        "stage3_threshold": 0.6, "top_k_ratio": 1.0, "max_candidates": 6,
        "dynamic_batch_gating": False, "diversity_reserve": 2,
        "candidate_budget_per_1000": 6, "max_candidates_limit": 12,
    }}}
    kept, skipped, diag = apply_stage3_candidates_gating(rows, config=cfg)
    assert len(kept) == 6 and len(skipped) == 18
    assert any(r["debug_info"]["saturation"] == 0 for r in kept)
    assert diag["diversity_admitted"] == 2
    shuffled = list(reversed(rows))
    assert [r["file_name"] for r in kept] == [r["file_name"] for r in apply_stage3_candidates_gating(shuffled, config=cfg)[0]]
    assert len(apply_stage3_candidates_gating(rows, config=cfg, batch_input_scale_n=4000)[0]) == 12
    cfg["processing"]["stage3_gating"]["max_candidates"] = 0
    assert apply_stage3_candidates_gating(rows, config=cfg)[0] == []
    cfg["processing"]["score_all_images"] = True
    assert len(apply_stage3_candidates_gating(rows, config=cfg)[0]) == len(rows)
