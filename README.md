# claude-meter

macOS menu bar app that shows your Claude API usage.

![claude-meter demo](https://i.gyazo.com/c909657d09e8b9f80073a5469c1c9807.png)

## How it works

Polls `api.anthropic.com/api/oauth/usage` and reads the `five_hour` / `seven_day` utilization it returns. This endpoint consumes no inference quota. Authentication reuses the OAuth token that Claude Code stores in Keychain — no API key setup required.

When usage cannot be read the title shows `?` with the reason (expired auth, rate limit, network error) rather than `0%`, and the last known reading is kept and marked `(stale)`.

### Keeping the token fresh

The OAuth token Claude Code stores in Keychain expires after a few hours, and it is only refreshed when the CLI itself talks to the API. If you mostly use the Claude desktop app, nothing refreshes it — which used to leave the meter stuck at `0%`.

On detecting an expired token (or a 401), claude-meter runs a minimal `claude -p` in the background. That makes Claude Code refresh and re-store its own token, after which usage is re-read. Credentials are never written by claude-meter — the CLI owns the rotating refresh token, its lock files, and the Keychain write.

The nudge is a real (if tiny) inference call, so it does draw on your subscription quota: it is pinned to Haiku with `--max-turns 1` to keep that to a rounding error, and is rate-limited to once every 10 minutes so a persistent auth failure can't spawn a process on every poll. Polling usage itself consumes nothing. The **Re-authenticate** menu item does the same thing on demand, bypassing the cooldown.

## Requirements

- macOS
- [Claude Code](https://claude.ai/code) installed and authenticated
- [uv](https://docs.astral.sh/uv/)

## Install (auto-start on login)

```sh
bash install.sh
```

Registers a launchd agent that starts automatically on login. Logs are written to `/tmp/claude-meter.log`.

### Stop / Start manually

Quit from the menu bar icon to stop the app. It will not auto-restart until you log in again.

To start it again without rebooting:

```sh
launchctl unload ~/Library/LaunchAgents/com.github.aki77.claude-meter.plist
launchctl load ~/Library/LaunchAgents/com.github.aki77.claude-meter.plist
```

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
7d Weekly:  9%  (resets in 6d 2h)
  Projected: 63%  (day 1 of 7)

Refresh
Quit
```

The `Projected` line shows the estimated total usage at the end of the 7-day window if you continue at your current pace. It uses discrete day buckets (day 1 = the first 24 hours, day 2 = the next 24 hours, etc.), so the value is stable throughout each day and doesn't spike right after a reset. If the projection exceeds 100%, the ⚠️ warning is shown in the menu bar title.

Usage is polled every 60 seconds. Click **Refresh** to update immediately.

The menu bar title is prefixed with ⚠️ when any of the following conditions are met: 5-hour session usage reaches 75%, 7-day weekly usage reaches 90%, or the projected weekly usage exceeds 100%. A macOS notification is also posted when 5-hour / 7-day utilization first crosses 50%, 75%, or 90%. Counters reset automatically when a new rate-limit window begins.
