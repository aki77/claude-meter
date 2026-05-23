import subprocess
import threading
from dataclasses import dataclass, field

import rumps

from .auth import get_token
from .usage import UsageData, fetch_usage

POLL_INTERVAL = 60
ICON_PATH = "/Applications/Claude.app/Contents/Resources/TrayIconTemplate.png"
ALERT_THRESHOLDS = (50, 75, 90)
ALERT_MARK = "⚠️"
WEEKLY_WINDOW_MIN = 7 * 24 * 60
PROJECTED_PREFIX = "  Projected: "
PROJECTED_PLACEHOLDER = f"{PROJECTED_PREFIX}-"


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


def _week_day(weekly_reset_minutes: int) -> int:
    elapsed_min = max(0, WEEKLY_WINDOW_MIN - weekly_reset_minutes)
    return min(elapsed_min // (24 * 60) + 1, 7)


def _projected_weekly_pct(weekly_pct: int, weekly_reset_minutes: int) -> int:
    return round(weekly_pct * 7 / _week_day(weekly_reset_minutes))


def _format_projection(weekly_pct: int, weekly_reset_minutes: int) -> str:
    day = _week_day(weekly_reset_minutes)
    projected = round(weekly_pct * 7 / day)
    return f"{PROJECTED_PREFIX}{projected}%  (day {day} of 7)"


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
        self._weekly_projection = rumps.MenuItem(PROJECTED_PLACEHOLDER)
        self._refresh_item = rumps.MenuItem("Refresh", callback=self._on_refresh)
        self._quit_item = rumps.MenuItem("Quit", callback=rumps.quit_application)

        self.menu = [
            self._session.menu_item,
            self._weekly.menu_item,
            self._weekly_projection,
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
            self._weekly_projection.title = PROJECTED_PLACEHOLDER
            return

        if data is None:
            self.title = "?"
            self._session.menu_item.title = f"{self._session.label}: unavailable"
            self._weekly.menu_item.title = f"{self._weekly.label}: unavailable"
            self._weekly_projection.title = PROJECTED_PLACEHOLDER
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
            threshold = ALERT_THRESHOLDS[1] if series is self._session else ALERT_THRESHOLDS[2]
            if pct >= threshold:
                warn = True

        projected = _projected_weekly_pct(data.weekly_pct, data.weekly_reset_minutes)
        self._weekly_projection.title = _format_projection(data.weekly_pct, data.weekly_reset_minutes)
        if projected > 100:
            warn = True

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
