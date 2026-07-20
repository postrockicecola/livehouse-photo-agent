from services.gallery_dedupe import dedupe_row_indices


def test_dedupe_keeps_up_to_keep_per_cluster():
    rows = [{"overall_score": 100 - i, "phash": 0xAAAA} for i in range(8)]
    # same phash cluster
    rows[5]["phash"] = 0xBBBB
    indices = list(range(8))
    kept, hidden = dedupe_row_indices(
        rows,
        indices,
        max_hamming=0,
        keep_per_cluster=1,
    )
    assert len(kept) == 2  # 1 from cluster A + 1 from cluster B
    assert hidden == 6


def test_dedupe_treats_distant_phash_as_separate_clusters():
    rows = [{"overall_score": 90}, {"overall_score": 80}]
    rows[0]["phash"] = 0
    rows[1]["phash"] = 0xFFFF_FFFF_FFFF_FFFF
    kept, hidden = dedupe_row_indices(rows, [0, 1], max_hamming=8, keep_per_cluster=1)
    assert len(kept) == 2
    assert hidden == 0
