from datetime import UTC, datetime, timedelta

import pytest

from app.services import analysis as a

CFG = a.PointConfig(canal_depth_cm=100, sensor_height_cm=120, d0_cm=120)


def test_filter_readings_drops_outliers_and_out_of_range():
    value, kept = a.filter_readings([60, 61, 59, 200, 60.5, 0, 399.9])
    assert 59 <= value <= 61 and kept == 4
    with pytest.raises(ValueError):
        a.filter_readings([0, 0, 500])


def test_height_and_percent():
    assert a.water_height_cm(CFG, 70) == 50
    assert a.water_height_cm(CFG, 130) == 0  # lecture sous le fond : jamais négatif
    assert a.water_level_pct(CFG, 50) == 50


@pytest.mark.parametrize(
    "pct,expected",
    [(10, "vert"), (49.9, "vert"), (50, "orange"), (80, "orange"), (80.1, "rouge"), (120, "rouge")],
)
def test_water_level_thresholds(pct, expected):
    assert a.classify_water_level(CFG, pct, None) == expected


def test_fast_rise_triggers_orange_only_above_noise_floor():
    assert a.classify_water_level(CFG, 30, 3.0) == "orange"
    assert a.classify_water_level(CFG, 5, 3.0) == "vert"  # montée « rapide » sur du bruit : ignorée
    assert a.classify_water_level(CFG, 30, 1.0) == "vert"


@pytest.mark.parametrize(
    "rate,expected",
    [(0, "libre"), (19.9, "libre"), (20, "partielle"), (49.9, "partielle"), (50, "forte"), (80, "forte"), (80.1, "critique")],
)
def test_obstruction_classes(rate, expected):
    assert a.classify_obstruction(CFG, rate) == expected


def test_obstruction_rate_formula():
    assert a.obstruction_rate_pct(CFG, 70) == 50.0  # (120 - 70) / 100
    assert a.obstruction_rate_pct(CFG, 130) == 0.0  # jamais négatif
    assert a.obstruction_rate_pct(CFG, 2) == 100.0  # plafonné


def test_rise_rate_linear_regression():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    hist = [(now - timedelta(minutes=m), 10 + (4 - m) * 2.0) for m in (4, 3, 2, 1)]  # +2 cm/min
    assert a.compute_rise_rate(now, 10 + 4 * 2.0, hist, 5) == pytest.approx(2.0, abs=0.01)
    assert a.compute_rise_rate(now, 10, [], 5) is None  # pas de recul
    assert a.compute_rise_rate(now, 10, [(now - timedelta(minutes=20), 5)], 5) is None  # hors fenêtre


def test_blockage_detection():
    assert a.detect_blockage(CFG, 40, 20) is True
    assert a.detect_blockage(CFG, 40, 35) is False
    assert a.detect_blockage(CFG, 40, None) is False


def test_combined_rule_orange_plus_strong_obstruction_is_red():
    ev = a.evaluate(CFG, distance_cm=50, obstruction_distance_cm=60)  # eau 70 % (orange), obstruction 60 % (forte)
    assert ev.water_level == "orange" and ev.obstruction_class == "forte" and ev.level == "rouge"
    assert any("règle combinée" in r for r in ev.reasons)


def test_partial_obstruction_does_not_escalate():
    ev = a.evaluate(CFG, distance_cm=50, obstruction_distance_cm=90)  # 30 % : partielle
    assert ev.level == "orange"


def test_obstruction_unknown_without_dedicated_reading():
    ev = a.evaluate(CFG, distance_cm=100)
    assert ev.obstruction_pct is None and ev.obstruction_class is None and ev.level == "vert"


def test_blockage_raises_class_to_partielle_at_least():
    ev = a.evaluate(CFG, distance_cm=80, distance_aval_cm=110)  # amont 40 cm, aval 10 cm
    assert ev.blockage_detected and ev.obstruction_class == "partielle"
