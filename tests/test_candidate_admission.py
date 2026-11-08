"""Stage3 budget, conservative burst grouping, and independent admission lanes."""
from copy import deepcopy

import pytest
from PIL import Image

from engine.operators.stage2_prefilter import need_phash_for_pipeline, read_capture_time
from services.processor.candidate_admission import _groups, select_candidates
from services.processor.pipeline_image_ops import apply_stage3_candidates_gating


def row(i, *, score=90, phash=0, time=None, tags=()):
    return dict(file_name=f'DSC{i:05d}.jpg', tech_score=score, fast_score=80,
                phash=phash, debug_info=dict(capture_time=time, ambiguous_tags=list(tags)))


def test_one_budget_deterministic_no_mutation_and_no_legacy_cap():
    rows = [row(i) for i in range(1000)]
    before = deepcopy(rows)
    config = {'processing': {'stage3_gating': {
        'strategy': 'coverage_budget', 'max_candidates': 1,
        'stage3_threshold': .99, 'top_k_ratio': .01,
        'candidate_selection': {'budget_ratio': .2, 'group_budget_ratio': 0, 'min_candidates': 0}}}}
    a, skipped, diag = apply_stage3_candidates_gating(rows, config=config)
    b, _, _ = apply_stage3_candidates_gating(rows[::-1], config=config)
    assert len(a) == 200 and len(skipped) == 800
    assert a == b and rows == before
    assert diag['candidate_budget'] == 200
    assert sum(diag['lane_admitted'].values()) == 200
    assert not any('score_status' in r for r in skipped)


def test_burst_anchor_prevents_similarity_chaining_and_missing_hash_never_merges():
    rows = [row(1, phash=1, time=1), row(2, phash=3, time=2), row(3, phash=7, time=3)]
    assert _groups(rows, {'burst_hash_distance': 1}) == [[0, 1], [2]]
    assert _groups([row(1), row(2)], {}) == [[0], [1]]
    assert _groups([row(1, phash=1), row(2, phash=1)], {}) == [[0, 1]]
    assert _groups([row(1, phash=1, time=1), row(2, phash=1, time=10)], {}) == [[0], [1]]
    assert _groups([row(1, phash=1, time=1), row(2, phash=1)], {}) == [[0], [1]]
    assert _groups([row(1, phash=1), row(5, phash=1)], {}) == [[0], [1]]


def test_fill_respects_group_cap_until_all_groups_have_representatives():
    rows = [row(i, phash=1, time=i / 10) for i in range(10)]
    rows += [row(100+i, phash=0, time=100+i) for i in range(10)]
    kept, _, diag = select_candidates(rows, settings={'max_candidates': 10, 'budget_ratio': 1})
    assert len(kept) == 10 and diag['burst_cap_relaxed'] == 0
    assert sum(r['file_name'] < 'DSC00100.jpg' for r in kept) <= 2
    kept, _, diag = select_candidates(rows[:10], settings={'budget_ratio': 1})
    assert len(kept) == 10 and diag['burst_cap_relaxed'] == 8


def test_ambiguous_lane_retains_low_technical_special_frame():
    rows = [row(i) for i in range(100)] + [row(999, score=30, tags=['motion_blur'])]
    kept, _, diag = select_candidates(rows, settings={'max_candidates': 10})
    assert 'DSC00999.jpg' in {r['file_name'] for r in kept}
    assert diag['lane_quotas']['ambiguous'] == 1


def test_coverage_lane_covers_time_bins_before_repeating():
    rows = [row(i, time=i) for i in range(100)]
    kept, _, _ = select_candidates(rows, settings={
        'max_candidates': 10, 'coverage_time_bins': 10,
        'lane_weights': {'quality': 0, 'coverage': 1, 'ambiguous': 0, 'exploration': 0}})
    assert {min(9, int(r['debug_info']['capture_time'] / 99 * 10)) for r in kept} == set(range(10))


@pytest.mark.parametrize('budget', [0, 1, 2, 3, 50, 1000])
def test_explicit_cap_fills_exactly_even_when_lanes_empty(budget):
    rows = [row(i) for i in range(70)]
    kept, skipped, diag = select_candidates(rows, settings={'budget_ratio': 1, 'max_candidates': budget})
    assert len(kept) == min(70, budget)
    assert len(kept) + len(skipped) == 70
    assert sum(diag['lane_quotas'].values()) == len(kept)
    assert len({r['file_name'] for r in kept}) == len(kept)


def test_pipeline_mode_ratio_and_score_all_override():
    rows = [row(i) for i in range(100)]
    config = {'processing': {'pipeline_mode': 'fast', 'stage3_gating': {'strategy': 'coverage_budget'}}}
    kept, _, diag = apply_stage3_candidates_gating(rows, config=config)
    assert len(kept) == 10 and diag['pipeline_mode'] == 'fast'
    config['processing']['score_all_images'] = True
    kept, skipped, _ = apply_stage3_candidates_gating(rows, config=config)
    assert len(kept) == 100 and not skipped
    assert need_phash_for_pipeline(config)
    assert select_candidates([], settings={})[:2] == ([], [])


def test_exif_timestamp_and_missing_metadata(tmp_path):
    image = Image.new('RGB', (30, 20))
    path = tmp_path / 'photo.jpg'
    image.save(path)
    assert read_capture_time(str(path)) is None
    exif = Image.Exif()
    exif[34665] = {36867: '2026:11:01 20:30:40', 37521: '125'}
    image.save(path, exif=exif)
    assert read_capture_time(str(path)) == 1793565040.125
    assert read_capture_time(str(tmp_path / 'missing.jpg')) is None
    path.write_bytes(b'broken')
    assert read_capture_time(str(path)) is None
