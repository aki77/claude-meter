import math
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import rumps

from .auth import get_token
from .usage import UsageData, fetch_usage

POLL_INTERVAL = 60
ICON_PATH = "/Applications/Claude.app/Contents/Resources/TrayIconTemplate.png"
ALERT_THRESHOLDS = (50, 75, 90)
ALERT_MARK = "⚠️"
WEEKLY_WINDOW_MIN = 7 * 24 * 60
PACE_PREFIX = "  Projected at reset: "
PACE_PLACEHOLDER = f"{PACE_PREFIX}-"


def _format_duration(minutes: int) -> str:
    if minutes <= 0:
        return "now"
    days, rem = divmod(minutes, 24 * 60)
    if days:
        hours = rem // 60
        return f"{days}d {hours}h" if hours else f"{days}d"
    hours, mins = divmod(rem, 60)
    if hours:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    return f"{mins}m"


def _projected_with_decay(weekly_pct: int, elapsed_minutes: int) -> int:
    if elapsed_minutes <= 0:
        return weekly_pct
    naive = weekly_pct * WEEKLY_WINDOW_MIN / elapsed_minutes
    confidence = math.sqrt(elapsed_minutes / WEEKLY_WINDOW_MIN)
    return round(weekly_pct + (naive - weekly_pct) * confidence)


def _format_pace(weekly_pct: int, weekly_reset_minutes: int) -> str:
    elapsed = WEEKLY_WINDOW_MIN - weekly_reset_minutes
    if elapsed <= 0:
        return f"{PACE_PREFIX}--"
    projected = _projected_with_decay(weekly_pct, elapsed)
    if projected <= 100:
        return f"{PACE_PREFIX}{projected}%"
    if projected == weekly_pct:
        return f"{PACE_PREFIX}{projected}%"
    minutes_to_deplete = round(
        weekly_reset_minutes * (100 - weekly_pct) / (projected - weekly_pct)
    )
    deplete_at = datetime.now() + timedelta(minutes=minutes_to_deplete)
    return (
        f"{PACE_PREFIX}{projected}%  "
        f"(depletes in {_format_duration(minutes_to_deplete)}, "
        f"{deplete_at.strftime('%-m/%-d %H:%M')})"
    )


def _crossed_threshold(pct: int, last_notified: int) -> int:
    return max(
        (t for t in ALERT_THRESHOLDS if pct >= t > last_notified),
        default=0,
    )


@dataclass
class _Series:
    label: str
    menu_item: rumps.MenuItem
    notified_at: int = 0
    prev_reset: int = 0
    pct: int = field(default=0, init=False)
    reset_minutes: int = field(default=0, init=False)

    def update(self, pct: int, reset_minutes: int) -> int:
        if reset_minutes > self.prev_reset:
            self.notified_at = 0
        self.prev_reset = reset_minutes
        self.pct = pct
        self.reset_minutes = reset_minutes
        crossed = _crossed_threshold(pct, self.notified_at)
        if crossed:
            self.notified_at = crossed
        return crossed


class ClaudeMeterApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Claude Meter", title="...", icon=ICON_PATH, template=True, quit_button=None)
        self._token_missing = False
        self._data: UsageData | None = None
        self._lock = threading.Lock()
        self._update_event = threading.Event()
        self._fetching = False

        self._session = _Series(label="5h Session", menu_item=rumps.MenuItem("5h Session: -"))
        self._weekly = _Series(label="7d Weekly", menu_item=rumps.MenuItem("7d Weekly: -"))
        self._weekly_pace = rumps.MenuItem(PACE_PLACEHOLDER)
        self._refresh_item = rumps.MenuItem("Refresh", callback=self._on_refresh)
        self._quit_item = rumps.MenuItem("Quit", callback=rumps.quit_application)

        self.menu = [
            self._session.menu_item,
            self._weekly.menu_item,
            self._weekly_pace,
            None,
            self._refresh_item,
            None,
            self._quit_item,
        ]

    def _set_state(self, token_missing: bool, data: UsageData | None) -> None:
        with self._lock:
            self._token_missing = token_missing
            self._data = data
        self._update_event.set()

    @rumps.timer(1)
    def _apply_state(self, _: rumps.Timer) -> None:
        if not self._update_event.is_set():
            return
        self._update_event.clear()
        with self._lock:
            token_missing = self._token_missing
            data = self._data

        if token_missing:
            self.title = "?"
            self._session.menu_item.title = "Claude Code token not found"
            self._weekly.menu_item.title = "Start Claude Code to authenticate"
            self._weekly_pace.title = PACE_PLACEHOLDER
            return

        if data is None:
            self.title = "?"
            self._session.menu_item.title = f"{self._session.label}: unavailable"
            self._weekly.menu_item.title = f"{self._weekly.label}: unavailable"
            self._weekly_pace.title = PACE_PLACEHOLDER
            return

        pairs = (
            (self._session, data.session_pct, data.session_reset_minutes),
            (self._weekly, data.weekly_pct, data.weekly_reset_minutes),
        )
        warn = False
        for series, pct, reset_minutes in pairs:
            crossed = series.update(pct, reset_minutes)
            if crossed:
                self._notify(
                    title=f"Claude {series.label} at {pct}%",
                    subtitle=f"Resets in {_format_duration(reset_minutes)}",
                )
            series.menu_item.title = (
                f"{series.label}: {pct}%  (resets in {_format_duration(reset_minutes)})"
            )
            if pct >= ALERT_THRESHOLDS[0]:
                warn = True

        self._weekly_pace.title = _format_pace(
            data.weekly_pct, data.weekly_reset_minutes
        )

        prefix = f"{ALERT_MARK} " if warn else ""
        self.title = f"{prefix}{data.session_pct}%"

    @staticmethod
    def _notify(title: str, subtitle: str) -> None:
        script = (
            'on run argv\n'
            '  display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"\n'
            'end run'
        )
        try:
            subprocess.Popen(
                ["osascript", "-e", script, title, subtitle],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def _fetch(self) -> None:
        with self._lock:
            if self._fetching:
                return
            self._fetching = True
        try:
            token = get_token()
            if not token:
                self._set_state(token_missing=True, data=None)
                return
            self._set_state(token_missing=False, data=fetch_usage(token))
        finally:
            with self._lock:
                self._fetching = False

    def _start_fetch(self) -> None:
        threading.Thread(target=self._fetch, daemon=True).start()

    @rumps.timer(POLL_INTERVAL)
    def _poll(self, _: rumps.Timer) -> None:
        self._start_fetch()

    def _on_refresh(self, _: rumps.MenuItem) -> None:
        self._start_fetch()

    def run(self) -> None:
        self._start_fetch()
        super().run()


def main() -> None:
    ClaudeMeterApp().run()


if __name__ == "__main__":
    main()
