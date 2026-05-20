# claude-meter

macOS menu bar app that shows your Claude API usage.

![claude-meter demo](https://i.gyazo.com/c909657d09e8b9f80073a5469c1c9807.png)

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

The menu bar title is prefixed with ⚠️ when your weekly usage exceeds the day-rounded pace (more than 1 day's worth used within the first 24h, more than 2 days' worth within 48h, and so on) or when 5-hour / 7-day utilization first crosses 50%, 75%, or 90%. A macOS notification is also posted on those 50/75/90% thresholds. Counters reset automatically when a new rate-limit window begins.
