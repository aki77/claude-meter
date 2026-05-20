# claude-meter

macOS menu bar app that shows your Claude API usage.

![claude-meter demo](https://i.gyazo.com/88dcc4b6d93646c17b2bcd392cec879e.png)

## How it works

Sends a minimal request to `api.anthropic.com/v1/messages` and reads usage from the response headers (`anthropic-ratelimit-unified-5h-utilization`, `anthropic-ratelimit-unified-7d-utilization`). Authentication reuses the OAuth token that Claude Code stores in Keychain — no API key setup required.

## Requirements

- macOS
- [Claude Code](https://claude.ai/code) installed and authenticated
- [uv](https://docs.astral.sh/uv/)

## Install (auto-start on login)

```sh
bash install.sh
```

Registers a launchd agent that starts automatically on login and restarts on crash. Logs are written to `/tmp/claude-meter.log`.

To uninstall:

```sh
bash uninstall.sh
```

## Manual usage

```sh
uv run claude-meter
```

## Menu

The menu bar shows `Claude: N%` (5-hour session usage). Click to see details:

```
5h Session: 4%  (resets in 4h 29m)
7d Weekly:  9%  (resets in 3d 2h)
  Projected at reset: 21%

Refresh
Quit
```

The `Projected at reset` line linearly extrapolates the 7-day usage from how much of the current window has elapsed. If the projection exceeds 100%, it appends the time and absolute clock at which the budget would be depleted at the current pace (e.g. `Projected at reset: 130%  (depletes in 5d 9h, 5/26 14:30)`).

Usage is polled every 60 seconds. Click **Refresh** to update immediately.

When the 5-hour or 7-day utilization first crosses 50%, 75%, or 90%, a macOS notification is posted and the menu bar title is prefixed with ⚠️. Counters reset automatically when a new rate-limit window begins.
