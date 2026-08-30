# Windows Laptop Operations

## Optional TradingView Observer

`setup_tradingview.bat` installs the exact commit recorded in `vendor/tradingview-mcp.pin` using the
bundled Node runtime. `start_tradingview_debug.bat` uses the upstream launcher with `--no-kill` and
then verifies that CDP port 9222 is loopback-only.

The paper profile enables the research observer with zero execution weight. TradingView health is
separate from bot health; the local 15-minute watchdog may restart only the observer when it is
unavailable and must not restart, pause or resume trading because of that failure.

## Phase 1 Environment

Run locally on a Windows laptop.

The laptop is temporarily treated as a single-node trading/research server.

## Requirements

- disable sleep while bot is active;
- stable wired connection preferred;
- keep charger connected;
- configure sensible Windows power behavior;
- ensure sufficient free disk space;
- system clock synchronization enabled;
- automatic restarts/logging.

## Startup Scripts

Provide minimal scripts such as:

- `start_bot.bat`
- `start_dashboard.bat`
- `autostart_dashboard.bat`
- `install_autostart.bat`
- `health_check.bat`
- `stop_bot.bat`

Prefer one orchestrated launcher once stable.

## Auto-start

After manual startup and browser checks pass, run `install_autostart.bat`. It installs a launcher in
the current user's Windows Startup folder, so no administrator rights are needed. The launcher first
checks `http://127.0.0.1:8767/api/state`, starts the dashboard only when it is unavailable, waits for
PAPER mode, the risk engine and fresh Gate data, starts the singleton local watchdog, and opens the
local dashboard in the default browser.

This is login auto-start, not a background service. Use Task Scheduler later only if startup before
interactive login becomes necessary.

## Watchdog

A watchdog must detect at minimum:

- process death;
- data-feed inactivity;
- stale heartbeat;
- repeated reconnect failure;
- storage exhaustion risk.

It may restart recoverable components.

It must not silently resume trading after an integrity-critical fault.

`health_check.bat` is the independent local watchdog. It scans every 900 seconds, appends results to
`logs/health-check.log`, restarts the dashboard once only when its API is unreachable, and may restart
the optional TradingView observer without touching the engine. After a clean dashboard restart it may
call the loopback PAPER resume endpoint only when the backend explicitly returns
`controls.auto_resume_allowed=true`. Manual pause, stale data, execution errors, recovery positions and
risk halts never expose that permission and remain blocked. Use `health_check.bat --once` to test it
interactively.

## Logs

Separate:

- engine log;
- market-data log;
- strategy log;
- risk events;
- execution simulator events;
- dashboard/API log;
- watchdog events.

Use rotation to prevent disk exhaustion.

## Migration to VPS

Do not optimize for VPS now.

Keep:
- configuration externalized;
- paths portable;
- secrets externalized;
- runtime container-compatible where sensible.

This allows later migration without rewriting strategy code.
