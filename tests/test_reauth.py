import time

import pytest

from claude_meter import app as app_module
from claude_meter.auth import Credentials
from claude_meter.usage import UsageData, UsageError

GOOD = UsageData(session_pct=7, session_reset_minutes=60, weekly_pct=7, weekly_reset_minutes=6000)
AUTH_ERROR = UsageError(kind="auth", detail="auth expired")


class _Item:
    """Minimal stand-in for rumps.MenuItem (no AppKit involved)."""

    def __init__(self):
        self.title = ""


class _Run:
    """Stands in for subprocess.run, counting how often the CLI is invoked."""

    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self

    @property
    def stdout(self):
        return ""


@pytest.fixture
def meter(monkeypatch):
    """A ClaudeMeterApp with no AppKit involvement; notifications are captured."""
    app = app_module.ClaudeMeterApp.__new__(app_module.ClaudeMeterApp)
    app._error = None
    app._data = None
    app._stale = False
    app._fetching = False
    app._reauthenticating = False
    app._reauth_at = 0.0
    import threading

    app._lock = threading.Lock()
    app._update_event = threading.Event()
    # Never depend on whether this machine has the claude CLI installed.
    monkeypatch.setattr(app_module.shutil, "which", lambda _: "/fake/claude")
    app.notifications = []
    monkeypatch.setattr(
        app_module.ClaudeMeterApp, "_notify", lambda self, **kw: self.notifications.append(kw)
    )
    app._session = app_module._Series(label="5h Session", menu_item=_Item())
    app._weekly = app_module._Series(label="7d Weekly", menu_item=_Item())
    app._weekly_projection = _Item()
    app._status_item = _Item()
    return app


def _creds(offset):
    return Credentials(access_token="new" if offset > 0 else "old", expires_at=time.time() + offset)


def test_expired_credentials_trigger_one_cli_refresh(meter, monkeypatch):
    run = _Run()
    monkeypatch.setattr(app_module.subprocess, "run", run)
    seq = [_creds(-10), _creds(9999)]
    monkeypatch.setattr(app_module, "get_credentials", lambda: seq.pop(0) if seq else _creds(9999))
    monkeypatch.setattr(app_module, "fetch_usage", lambda tok: GOOD if tok == "new" else AUTH_ERROR)

    meter._fetch()

    assert run.calls == 1
    assert meter._error is None
    assert meter._data == GOOD


def test_401_triggers_refresh_and_one_retry(meter, monkeypatch):
    run = _Run()
    monkeypatch.setattr(app_module.subprocess, "run", run)
    monkeypatch.setattr(app_module, "get_credentials", lambda: _creds(9999))
    fetches = []

    def fetch(tok):
        fetches.append(tok)
        return AUTH_ERROR if len(fetches) == 1 else GOOD

    monkeypatch.setattr(app_module, "fetch_usage", fetch)

    meter._fetch()

    assert run.calls == 1
    assert len(fetches) == 2
    assert meter._data == GOOD


def test_cooldown_stops_repeated_cli_invocations(meter, monkeypatch):
    run = _Run()
    monkeypatch.setattr(app_module.subprocess, "run", run)
    monkeypatch.setattr(app_module, "get_credentials", lambda: _creds(9999))
    monkeypatch.setattr(app_module, "fetch_usage", lambda tok: AUTH_ERROR)

    for _ in range(5):
        meter._fetch()

    assert run.calls == 1
    assert meter._error is not None


def test_failed_refresh_reports_expired(meter, monkeypatch):
    monkeypatch.setattr(app_module.subprocess, "run", _Run(returncode=1))
    monkeypatch.setattr(app_module, "get_credentials", lambda: _creds(-10))
    monkeypatch.setattr(app_module, "fetch_usage", lambda tok: GOOD)

    meter._fetch()

    assert meter._error is not None
    assert meter._error.kind == "expired"


def test_claude_bin_falls_back_when_not_on_path(monkeypatch, tmp_path):
    """launchd's minimal PATH excludes ~/.local/bin, so PATH lookup alone is not enough."""
    monkeypatch.setattr(app_module.shutil, "which", lambda _: None)
    installed = tmp_path / "claude"
    installed.write_text("")
    monkeypatch.setattr(app_module, "CLAUDE_BIN_CANDIDATES", (tmp_path / "missing", installed))

    assert app_module._claude_bin() == str(installed)


def test_claude_bin_prefers_path_when_available(monkeypatch):
    monkeypatch.setattr(app_module.shutil, "which", lambda _: "/usr/local/bin/claude")
    assert app_module._claude_bin() == "/usr/local/bin/claude"


def test_missing_claude_binary_does_not_raise(meter, monkeypatch):
    monkeypatch.setattr(app_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(app_module, "CLAUDE_BIN_CANDIDATES", ())

    def explode(*args, **kwargs):
        raise AssertionError("subprocess.run must not be reached")

    monkeypatch.setattr(app_module.subprocess, "run", explode)
    monkeypatch.setattr(app_module, "get_credentials", lambda: _creds(-10))
    monkeypatch.setattr(app_module, "fetch_usage", lambda tok: GOOD)

    meter._fetch()

    assert meter._error is not None
    assert meter._error.kind == "expired"
    # a silent no-op would leave the user with no idea the click did anything
    assert meter.notifications, "missing CLI must still notify the user"


def test_reauth_resolves_binary_by_absolute_path(meter, monkeypatch):
    """The command must be invoked via the resolved path, never the bare name."""
    monkeypatch.setattr(app_module.shutil, "which", lambda _: "/resolved/claude")
    seen = {}

    class _R:
        returncode = 0

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _R()

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    seq = [_creds(-10), _creds(9999)]
    monkeypatch.setattr(app_module, "get_credentials", lambda: seq.pop(0) if seq else _creds(9999))
    monkeypatch.setattr(app_module, "fetch_usage", lambda tok: GOOD)

    meter._fetch()

    assert seen["cmd"][0] == "/resolved/claude"
    assert tuple(seen["cmd"][1:]) == app_module.REAUTH_ARGS


def test_last_good_reading_survives_reauth_without_stale_mark(meter):
    """A reauth triggered right after a successful fetch must not blank the reading."""
    app = meter
    app._data = GOOD
    app._stale = False
    app._reauthenticating = True

    app._show_unavailable(None, app._data, reauthenticating=True, stale=app._stale)

    assert "unavailable" not in app._session.menu_item.title
    assert "7%" in app._session.menu_item.title
    assert app_module.STALE_MARK not in app._session.menu_item.title
    assert app._status_item.title == app_module.REAUTH_MESSAGE


def test_failed_fetch_marks_reading_stale(meter):
    app = meter
    app._data = GOOD
    app._stale = True

    app._show_unavailable(AUTH_ERROR, app._data, reauthenticating=False, stale=True)

    assert app_module.STALE_MARK in app._session.menu_item.title


def test_reauth_without_prior_data_reports_unavailable(meter):
    app = meter
    app._data = None

    app._show_unavailable(None, None, reauthenticating=True, stale=False)

    assert "unavailable" in app._session.menu_item.title


def test_timer_does_not_crash_when_no_data_yet(meter):
    """A reauth can finish before the fetch it unblocked returns: data is still None."""
    meter._error = None
    meter._data = None
    meter._stale = False
    meter._reauthenticating = False
    meter._update_event.set()

    meter._apply_state(None)  # must not raise AttributeError

    assert "unavailable" in meter._session.menu_item.title


def test_failed_reauth_signals_ui_to_clear_in_flight_status(meter, monkeypatch):
    monkeypatch.setattr(app_module.shutil, "which", lambda _: "/fake/claude")
    monkeypatch.setattr(app_module.subprocess, "run", _Run(returncode=1))

    meter._update_event.clear()
    assert meter._run_reauth(force=True) is False

    assert meter._update_event.is_set()
    assert meter._reauthenticating is False


def test_missing_cli_notifies_once_per_cooldown(meter, monkeypatch):
    """A permanently missing CLI must not notify on every 180s poll."""
    monkeypatch.setattr(app_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(app_module, "CLAUDE_BIN_CANDIDATES", ())

    for _ in range(5):
        meter._run_reauth()

    assert len(meter.notifications) == 1
    assert meter._reauthenticating is False


def test_manual_reauth_bypasses_cooldown(meter, monkeypatch):
    monkeypatch.setattr(app_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(app_module, "CLAUDE_BIN_CANDIDATES", ())

    meter._run_reauth()
    meter._run_reauth(force=True)

    assert len(meter.notifications) == 2
