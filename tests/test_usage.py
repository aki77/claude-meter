import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from claude_meter.usage import API_URL, UsageData, UsageError, _reset_minutes, fetch_usage


def _iso(minutes_from_now: float, microseconds: int = 0) -> str:
    stamp = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
    return stamp.replace(microsecond=microseconds).isoformat()


def _payload(five_hour=None, seven_day=None):
    return {
        "five_hour": five_hour,
        "seven_day": seven_day,
        "seven_day_opus": None,
        "seven_day_sonnet": None,
        "limits": [],
    }


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _responder(status_code, json_body=None, text=None):
    def handler(request):
        assert str(request.url) == API_URL
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.headers["anthropic-beta"] == "oauth-2025-04-20"
        assert request.headers["User-Agent"].startswith("claude-code/")
        if text is not None:
            return httpx.Response(status_code, text=text)
        return httpx.Response(status_code, json=json_body)

    return handler


MEASURED = _payload(
    five_hour={
        "utilization": 22.0,
        "resets_at": "2026-08-20T09:39:59.874301+00:00",
        "limit_dollars": None,
        "used_dollars": None,
        "remaining_dollars": None,
    },
    seven_day={
        "utilization": 21.0,
        "resets_at": "2026-08-26T00:59:59.874333+00:00",
        "limit_dollars": None,
    },
)


def test_measured_payload_units_not_multiplied():
    result = fetch_usage("tok", client=_client(_responder(200, MEASURED)))
    assert isinstance(result, UsageData)
    assert result.session_pct == 22
    assert result.weekly_pct == 21


def test_genuine_zero_is_zero():
    payload = _payload(
        five_hour={"utilization": 0.0, "resets_at": _iso(60)},
        seven_day={"utilization": 0, "resets_at": _iso(3 * 24 * 60)},
    )
    result = fetch_usage("tok", client=_client(_responder(200, payload)))
    assert isinstance(result, UsageData)
    assert result.session_pct == 0
    assert result.weekly_pct == 0
    assert result.session_reset_minutes == 60


@pytest.mark.parametrize(
    "status,kind",
    [(401, "auth"), (403, "auth"), (429, "ratelimit"), (500, "no_data"), (404, "no_data")],
)
def test_status_codes(status, kind):
    result = fetch_usage("tok", client=_client(_responder(status, {"error": "x"})))
    assert isinstance(result, UsageError)
    assert result.kind == kind


@pytest.mark.parametrize(
    "payload",
    [
        _payload(),
        _payload(five_hour={"utilization": 22.0, "resets_at": _iso(10)}, seven_day=None),
        _payload(five_hour={"resets_at": _iso(10)}, seven_day={"utilization": 21.0}),
        _payload(
            five_hour={"utilization": None, "resets_at": _iso(10)},
            seven_day={"utilization": 21.0, "resets_at": _iso(10)},
        ),
        _payload(
            five_hour={"utilization": "22", "resets_at": _iso(10)},
            seven_day={"utilization": 21.0, "resets_at": _iso(10)},
        ),
        {},
    ],
)
def test_missing_values_are_no_data(payload):
    result = fetch_usage("tok", client=_client(_responder(200, payload)))
    assert isinstance(result, UsageError)
    assert result.kind == "no_data"


def test_non_json_200_is_no_data():
    result = fetch_usage("tok", client=_client(_responder(200, text="<html>")))
    assert isinstance(result, UsageError)
    assert result.kind == "no_data"


def test_network_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    result = fetch_usage("tok", client=_client(handler))
    assert isinstance(result, UsageError)
    assert result.kind == "network"


def test_null_resets_at_is_unknown_not_zero():
    payload = _payload(
        five_hour={"utilization": 22.0, "resets_at": None},
        seven_day={"utilization": 21.0, "resets_at": "not a timestamp"},
    )
    result = fetch_usage("tok", client=_client(_responder(200, payload)))
    assert isinstance(result, UsageData)
    assert result.session_reset_minutes is None
    assert result.weekly_reset_minutes is None


def test_reset_minutes_iso_parsing():
    assert _reset_minutes(_iso(90)) == 90
    # microsecond precision and a "+00:00" offset, as the live endpoint returns
    assert _reset_minutes(_iso(120, microseconds=874301)) == pytest.approx(120, abs=1)


def test_reset_minutes_past_is_zero():
    assert _reset_minutes(_iso(-30)) == 0


@pytest.mark.parametrize("value", [None, "", "garbage", 1787218800, "2026-08-20T09:39:59"])
def test_reset_minutes_unknown(value):
    assert _reset_minutes(value) is None


def test_rounding():
    payload = _payload(
        five_hour={"utilization": 22.6, "resets_at": _iso(10)},
        seven_day={"utilization": 21.4, "resets_at": _iso(10)},
    )
    result = fetch_usage("tok", client=_client(_responder(200, payload)))
    assert isinstance(result, UsageData)
    assert result.session_pct == 23
    assert result.weekly_pct == 21
