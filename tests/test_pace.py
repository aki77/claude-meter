from claude_meter.app import WEEKLY_WINDOW_MIN, _projected_with_decay


def test_returns_current_when_elapsed_zero():
    assert _projected_with_decay(5, 0) == 5


def test_returns_current_when_elapsed_negative():
    assert _projected_with_decay(50, -100) == 50


def test_zero_usage_returns_zero():
    assert _projected_with_decay(0, 1440) == 0


def test_early_high_pace_is_suppressed():
    # 2 hours after reset with 5% used: naive linear would be 420%, decayed = 50%
    assert _projected_with_decay(5, 120) == 50


def test_twelve_hours_moderate_pace():
    # 12 hours / 20%: naive 280%, decayed 89%
    assert _projected_with_decay(20, 720) == 89


def test_midweek_pace_partially_decayed():
    # 3 days / 35%: naive 82%, decayed 66%
    assert _projected_with_decay(35, 3 * 24 * 60) == 66


def test_late_window_close_to_linear():
    # 5 days / 90%: naive 126%, decayed 120% (close to naive late in the window)
    assert _projected_with_decay(90, 5 * 24 * 60) == 120


def test_window_end_matches_current():
    # At elapsed == WINDOW, confidence = 1 and naive == current, so result == current.
    assert _projected_with_decay(73, WEEKLY_WINDOW_MIN) == 73


def test_already_exceeded_returns_above_100():
    assert _projected_with_decay(100, 5000) >= 100


def test_projection_never_drops_below_current():
    for pct, el in [(1, 60), (10, 100), (40, 2000), (95, 9000)]:
        assert _projected_with_decay(pct, el) >= pct
