import threading

import rumps

from .auth import get_token
from .usage import UsageData, fetch_usage

POLL_INTERVAL = 60
ICON_PATH = "/Applications/Claude.app/Contents/Resources/TrayIconTemplate.png"


def _format_reset(minutes: int) -> str:
    if minutes <= 0:
        return "now"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"


class ClaudeMeterApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Claude Meter", title="...", icon=ICON_PATH, template=True, quit_button=None)
        self._token_missing = False
        self._data: UsageData | None = None
        self._lock = threading.Lock()
        self._update_event = threading.Event()
        self._fetching = False

        self._session_item = rumps.MenuItem("5h Session: -")
        self._weekly_item = rumps.MenuItem("7d Weekly: -")
        self._refresh_item = rumps.MenuItem("Refresh", callback=self._on_refresh)
        self._quit_item = rumps.MenuItem("Quit", callback=rumps.quit_application)

        self.menu = [
            self._session_item,
            self._weekly_item,
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
            self._session_item.title = "Claude Code token not found"
            self._weekly_item.title = "Start Claude Code to authenticate"
            return

        self.title = f"{data.session_pct}%" if data else "?"
        if data is None:
            self._session_item.title = "5h Session: unavailable"
            self._weekly_item.title = "7d Weekly: unavailable"
        else:
            self._session_item.title = (
                f"5h Session: {data.session_pct}%"
                f"  (resets in {_format_reset(data.session_reset_minutes)})"
            )
            self._weekly_item.title = (
                f"7d Weekly: {data.weekly_pct}%"
                f"  (resets in {_format_reset(data.weekly_reset_minutes)})"
            )

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
