import pytest

from claude_meter.app import _format_projection, _projected_weekly_pct

DAY = 24 * 60


@pytest.mark.parametrize(
    "pct,reset_min,expected_projected,expected_day",
    [
        (0, 7 * DAY, 0, 1),
        (9, 6 * DAY + 2 * 60, 63, 1),
        (14, 7 * DAY - 60, 98, 1),
        (15, 7 * DAY - 60, 105, 1),
        (57, 4 * DAY, 100, 4),
        (60, 4 * DAY, 105, 4),
        (5, 2 * DAY, 6, 6),
        (90, 0, 90, 7),
        (100, 0, 100, 7),
    ],
)
def test_projected_weekly_pct(pct, reset_min, expected_projected, expected_day):
    assert _projected_weekly_pct(pct, reset_min) == expected_projected


@pytest.mark.parametrize(
    "pct,reset_min,expected_projected,expected_day",
    [
        (0, 7 * DAY, 0, 1),
        (9, 6 * DAY + 2 * 60, 63, 1),
        (57, 4 * DAY, 100, 4),
        (60, 4 * DAY, 105, 4),
        (5, 2 * DAY, 6, 6),
        (90, 0, 90, 7),
    ],
)
def test_format_projection(pct, reset_min, expected_projected, expected_day):
    result = _format_projection(pct, reset_min)
    assert result == f"  Projected: {expected_projected}%  (day {expected_day} of 7)"


@pytest.mark.parametrize(
    "pct,reset_min,overpace",
    [
        (0, 7 * DAY, False),
        (14, 7 * DAY, False),
        (15, 7 * DAY - 60, True),
        (28, int(5.5 * DAY), False),
        (30, int(5.5 * DAY), True),
        (90, int(0.1 * DAY), False),
        (100, 0, False),
    ],
)
def test_overpace(pct, reset_min, overpace):
    assert (_projected_weekly_pct(pct, reset_min) > 100) is overpace
