from gwlab.swift.stream.anomaly import Ewma, deviation_z


def test_below_min_sample_is_none():
    assert deviation_z(1, 5, 0.02, min_sample=30) is None


def test_zero_deviation_when_observed_matches_expected():
    z = deviation_z(2, 100, 0.02)
    assert z is not None
    assert abs(z) < 1e-9


def test_positive_z_when_worse_than_baseline():
    z = deviation_z(8, 100, 0.02)  # 8% observed vs 2% expected
    assert z is not None
    assert z > 3.0


def test_ewma_tracks_sustained_drift():
    e = Ewma(alpha=0.3)
    for _ in range(20):
        e.update(0.04)
    assert abs(e.value - 0.04) < 0.005


def test_zero_standard_error_returns_zero():
    # expected_p == 0.0 => se == 0.0 (degenerate baseline), scored as no deviation.
    assert deviation_z(0, 100, 0.0) == 0.0


def test_ewma_value_before_first_update_is_zero():
    assert Ewma(alpha=0.3).value == 0.0
