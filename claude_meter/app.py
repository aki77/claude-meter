import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import rumps

from .auth import Credentials, get_credentials
from .usage import UsageData, UsageError, fetch_usage

POLL_INTERVAL = 180
ICON_PATH = "/Applications/Claude.app/Contents/Resources/TrayIconTemplate.png"
ALERT_THRESHOLDS = (50, 75, 90)
ALERT_MARK = "⚠️"
WEEKLY_WINDOW_MIN = 7 * 24 * 60
PROJECTED_PREFIX = "  Projected: "
PROJECTED_PLACEHOLDER = f"{PROJECTED_PREFIX}-"
STALE_MARK = " (stale)"
EXPIRED_MESSAGE = "Auth expired — click Re-authenticate"
REAUTH_MESSAGE = "Re-authenticating…"
# Minimal prompt whose only purpose is to make the CLI refresh its stored OAuth token.
# --setting-sources "" skips CLAUDE.md/hooks/MCP; --settings keeps --output-format json
# a single object even when the user has verbose enabled in ~/.claude.json.
REAUTH_ARGS = (
    "-p", "hi",
    "--model", "claude-haiku-4-5-20251001",
    "--max-turns", "1",
    "--output-format", "json",
    "--setting-sources", "",
    "--settings", '{"verbose":false}',
)
# launchd gives the agent a minimal PATH that excludes the usual install locations,
# so resolve the binary ourselves instead of relying on PATH lookup.
CLAUDE_BIN_CANDIDATES = (
    Path.home() / ".local/bin/claude",
    Path.home() / ".claude/local/claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
)
REAUTH_TIMEOUT = 90
# Don't re-trigger the CLI on every poll while auth stays broken.
REAUTH_COOLDOWN = 10 * 60
ERROR_MESSAGES = {
    "missing": "Claude Code token not found",
    "expired": EXPIRED_MESSAGE,
    "auth": EXPIRED_MESSAGE,
    "ratelimit": "Rate limited by the API",
    "no_data": "No usage data returned",
    "network": "Network error",
}


def _log(message: str) -> None:
    print(f"[claude-meter] {time.strftime('%Y-%m-%d %H:%M:%S')} {message}", file=sys.stderr, flush=True)


def _claude_bin() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for path in CLAUDE_BIN_CANDIDATES:
        if path.exists():
            return str(path)
    return None


def _format_duration(minutes: int | None) -> str:
    if minutes is None:
        return "unknown"
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


def _format_row(label: str, pct: int, reset_minutes: int | None, suffix: str = "") -> str:
    return f"{label}: {pct}%  (resets in {_format_duration(reset_minutes)}){suffix}"


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
    reset_minutes: int | None = field(default=None, init=False)

    def update(self, pct: int, reset_minutes: int | None) -> int:
        if reset_minutes is not None:
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
        self._error: UsageError | None = None
        self._data: UsageData | None = None
        self._stale = False
        self._lock = threading.Lock()
        self._update_event = threading.Event()
        self._fetching = False
        self._reauthenticating = False
        self._reauth_at = 0.0

        self._session = _Series(label="5h Session", menu_item=rumps.MenuItem("5h Session: -"))
        self._weekly = _Series(label="7d Weekly", menu_item=rumps.MenuItem("7d Weekly: -"))
        self._weekly_projection = rumps.MenuItem(PROJECTED_PLACEHOLDER)
        self._status_item = rumps.MenuItem("")
        self._refresh_item = rumps.MenuItem("Refresh", callback=self._on_refresh)
        self._reauth_item = rumps.MenuItem("Re-authenticate", callback=self._on_reauthenticate)
        self._quit_item = rumps.MenuItem("Quit", callback=rumps.quit_application)

        self.menu = [
            self._session.menu_item,
            self._weekly.menu_item,
            self._weekly_projection,
            self._status_item,
            None,
            self._refresh_item,
            self._reauth_item,
            None,
            self._quit_item,
        ]

    def _set_result(self, result: UsageData | UsageError) -> None:
        with self._lock:
            if isinstance(result, UsageData):
                self._error = None
                self._data = result
                self._stale = False
            else:
                _log(f"{result.kind}: {result.detail}")
                self._error = result
                self._stale = self._data is not None
        self._update_event.set()

    def _set_projection(self, data: UsageData, suffix: str = "") -> None:
        if data.weekly_reset_minutes is None:
            self._weekly_projection.title = PROJECTED_PLACEHOLDER
        else:
            self._weekly_projection.title = (
                _format_projection(data.weekly_pct, data.weekly_reset_minutes) + suffix
            )

    def _show_unavailable(
        self,
        error: UsageError | None,
        data: UsageData | None,
        reauthenticating: bool = False,
        stale: bool = True,
    ) -> None:
        self.title = "?"
        if reauthenticating:
            self._status_item.title = REAUTH_MESSAGE
        elif error is not None:
            self._status_item.title = ERROR_MESSAGES.get(error.kind, error.detail)
        if data is None:
            detail = error.detail if error is not None else "no data"
            self._session.menu_item.title = f"{self._session.label}: unavailable ({detail})"
            self._weekly.menu_item.title = f"{self._weekly.label}: unavailable ({detail})"
            self._weekly_projection.title = PROJECTED_PLACEHOLDER
            return
        suffix = STALE_MARK if stale else ""
        self._session.menu_item.title = _format_row(
            self._session.label, data.session_pct, data.session_reset_minutes, suffix
        )
        self._weekly.menu_item.title = _format_row(
            self._weekly.label, data.weekly_pct, data.weekly_reset_minutes, suffix
        )
        self._set_projection(data, suffix)

    @rumps.timer(1)
    def _apply_state(self, _: rumps.Timer) -> None:
        if not self._update_event.is_set():
            return
        self._update_event.clear()
        with self._lock:
            error = self._error
            data = self._data
            stale = self._stale
            reauthenticating = self._reauthenticating

        # The success path below needs a reading; anything else is an unavailable state.
        # data can be None here even without an error, e.g. a reauth finished but the
        # fetch it unblocked has not returned yet.
        if error is not None or reauthenticating or data is None:
            # Keep showing the last good reading while auth is being sorted out; it is
            # only "stale" once a fetch has actually failed.
            self._show_unavailable(error, data, reauthenticating, stale)
            return

        self._status_item.title = ""
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
            series.menu_item.title = _format_row(series.label, pct, reset_minutes)
            threshold = ALERT_THRESHOLDS[1] if series is self._session else ALERT_THRESHOLDS[2]
            if pct >= threshold:
                warn = True

        self._set_projection(data)
        if data.weekly_reset_minutes is not None:
            if _projected_weekly_pct(data.weekly_pct, data.weekly_reset_minutes) > 100:
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
            credentials: Credentials | None = get_credentials()
            if credentials is None:
                self._set_result(UsageError(kind="missing", detail="no token"))
                return
            if credentials.is_expired():
                # Only the CLI can refresh; nudge it, then re-read the Keychain.
                credentials = self._refreshed_credentials()
                if credentials is None:
                    self._set_result(UsageError(kind="expired", detail="auth expired"))
                    return
            result = fetch_usage(credentials.access_token)
            if isinstance(result, UsageError) and result.kind == "auth":
                refreshed = self._refreshed_credentials()
                if refreshed is not None:
                    result = fetch_usage(refreshed.access_token)
            self._set_result(result)
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

    def _on_reauthenticate(self, _: rumps.MenuItem) -> None:
        threading.Thread(target=self._reauthenticate, daemon=True).start()

    def _reauthenticate(self) -> None:
        if self._run_reauth(force=True):
            self._start_fetch()

    def _refreshed_credentials(self) -> Credentials | None:
        """Trigger a CLI refresh, then re-read credentials. None if still unusable."""
        if not self._run_reauth():
            return None
        credentials = get_credentials()
        if credentials is None or credentials.is_expired():
            return None
        return credentials

    def _run_reauth(self, force: bool = False) -> bool:
        """Make the CLI refresh its own stored token. Returns True if it ran and succeeded.

        The CLI owns the rotating refresh token, the lock files and the Keychain write,
        so we only ever trigger it — we never touch stored credentials ourselves.
        """
        now = time.time()
        with self._lock:
            if self._reauthenticating:
                return False
            if not force and now - self._reauth_at < REAUTH_COOLDOWN:
                return False
            self._reauthenticating = True
            self._reauth_at = now
        self._update_event.set()
        # Resolved inside the cooldown gate so a permanently missing CLI is reported
        # once per cooldown, not on every poll.
        claude_bin = _claude_bin()
        if claude_bin is None:
            _log("re-authenticate failed: claude CLI not found")
            with self._lock:
                self._reauthenticating = False
            return self._reauth_failed("Could not find the claude command")
        try:
            result = subprocess.run(
                (claude_bin, *REAUTH_ARGS),
                capture_output=True,
                text=True,
                timeout=REAUTH_TIMEOUT,
                cwd=str(Path.home()),
            )
            if result.returncode == 0:
                return True
            _log(f"re-authenticate failed: exit {result.returncode}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log(f"re-authenticate failed: {type(exc).__name__}")
        finally:
            with self._lock:
                self._reauthenticating = False
        return self._reauth_failed("Run claude in a terminal to sign in")

    def _reauth_failed(self, subtitle: str) -> bool:
        """Surface a reauth failure. Always returns False so callers can `return` it."""
        # Nothing else follows up on failure, so clear any "Re-authenticating…" status
        # here; the success path is signalled by the fetch that comes next.
        self._update_event.set()
        self._notify(title="Re-authentication failed", subtitle=subtitle)
        return False

    def run(self) -> None:
        self._start_fetch()
        super().run()


def main() -> None:
    ClaudeMeterApp().run()


if __name__ == "__main__":
    main()
