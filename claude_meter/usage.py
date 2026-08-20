import time
from dataclasses import dataclass
from datetime import datetime

import httpx

API_URL = "https://api.anthropic.com/api/oauth/usage"
API_HEADERS = {
    "anthropic-beta": "oauth-2025-04-20",
    "User-Agent": "claude-code/2.1.226",
}

_client = httpx.Client(timeout=20.0)


@dataclass
class UsageData:
    session_pct: int
    session_reset_minutes: int | None
    weekly_pct: int
    weekly_reset_minutes: int | None


@dataclass
class UsageError:
    kind: str
    detail: str


def _pct(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value))


def _reset_minutes(resets_at: object) -> int | None:
    if not isinstance(resets_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(resets_at)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    mins = (parsed.timestamp() - time.time()) / 60.0
    return int(round(mins)) if mins > 0 else 0


def _window(payload: dict, key: str) -> tuple[int, int | None] | None:
    window = payload.get(key)
    if not isinstance(window, dict):
        return None
    pct = _pct(window.get("utilization"))
    if pct is None:
        return None
    return pct, _reset_minutes(window.get("resets_at"))


def fetch_usage(token: str, client: httpx.Client | None = None) -> UsageData | UsageError:
    headers = {**API_HEADERS, "Authorization": f"Bearer {token}"}
    try:
        resp = (client or _client).get(API_URL, headers=headers)
    except Exception as exc:
        return UsageError(kind="network", detail=type(exc).__name__)

    if resp.status_code in (401, 403):
        return UsageError(kind="auth", detail="auth expired")
    if resp.status_code == 429:
        return UsageError(kind="ratelimit", detail="rate limited")
    if resp.status_code != 200:
        return UsageError(kind="no_data", detail=f"HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError:
        return UsageError(kind="no_data", detail="invalid response")
    if not isinstance(payload, dict):
        return UsageError(kind="no_data", detail="invalid response")

    session = _window(payload, "five_hour")
    weekly = _window(payload, "seven_day")
    if session is None or weekly is None:
        return UsageError(kind="no_data", detail="no usage values")

    session_pct, session_reset = session
    weekly_pct, weekly_reset = weekly
    return UsageData(
        session_pct=session_pct,
        session_reset_minutes=session_reset,
        weekly_pct=weekly_pct,
        weekly_reset_minutes=weekly_reset,
    )
