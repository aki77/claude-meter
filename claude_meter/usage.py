import time
from dataclasses import dataclass

import httpx

API_URL = "https://api.anthropic.com/v1/messages"
API_HEADERS = {
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "oauth-2025-04-20",
    "Content-Type": "application/json",
    "User-Agent": "claude-code/2.1.5",
}
API_BODY = {
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1,
    "messages": [{"role": "user", "content": "hi"}],
}

_client = httpx.Client(timeout=20.0)


@dataclass
class UsageData:
    session_pct: int
    session_reset_minutes: int
    weekly_pct: int
    weekly_reset_minutes: int


def _pct(value: str) -> int:
    try:
        return int(round(float(value) * 100))
    except (ValueError, TypeError):
        return 0


def _reset_minutes(reset_ts: str) -> int:
    try:
        mins = (float(reset_ts) - time.time()) / 60.0
        return int(round(mins)) if mins > 0 else 0
    except (ValueError, TypeError):
        return 0


def fetch_usage(token: str) -> UsageData | None:
    headers = {**API_HEADERS, "Authorization": f"Bearer {token}"}
    try:
        resp = _client.post(API_URL, headers=headers, json=API_BODY)
    except Exception:
        return None

    h = resp.headers
    return UsageData(
        session_pct=_pct(h.get("anthropic-ratelimit-unified-5h-utilization", "0")),
        session_reset_minutes=_reset_minutes(h.get("anthropic-ratelimit-unified-5h-reset", "0")),
        weekly_pct=_pct(h.get("anthropic-ratelimit-unified-7d-utilization", "0")),
        weekly_reset_minutes=_reset_minutes(h.get("anthropic-ratelimit-unified-7d-reset", "0")),
    )
