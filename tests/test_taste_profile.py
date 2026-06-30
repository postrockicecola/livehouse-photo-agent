from services.taste_profile import (
    _dim_vector_from_mapping,
    _mean_vector,
    personalized_sort_metric,
    taste_fit_score,
)


def test_dim_vector_legacy_keys():
    vec = _dim_vector_from_mapping(
        {"subject_clarity": 8, "atmosphere": 7, "lighting_quality": 6, "motion_capture": 9}
    )
    assert vec.get("focus_sharpness") == 8.0
    assert vec.get("atmosphere_impact") == 7.0


def test_personalized_sort_blends_fit():
    profile = {
        "dim_weights": {"moment_peak": 2.0, "atmosphere_impact": 1.0},
        "mean_liked": {},
        "mean_rest": {},
    }
    entry = {
        "overall_score": 50,
        "dimensions": {"moment_peak": 9, "atmosphere_impact": 9},
    }
    s = personalized_sort_metric(entry, profile)
    assert s > 50


def test_mean_vector():
    m = _mean_vector([{"moment_peak": 8.0}, {"moment_peak": 6.0}])
    assert abs(m["moment_peak"] - 7.0) < 0.01
