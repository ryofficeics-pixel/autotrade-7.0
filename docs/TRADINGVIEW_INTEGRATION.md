# TradingView MCP Integration

## Verdict

TradingView MCP is installed as an optional, read-only research and health sidecar. It is enabled in
the local paper profile and has **no execution influence**.

The upstream project's own disclaimer says its extracted data must not be used for automated trading
or algorithmic decisions. Autotrade therefore reads a bounded quote and current indicator values for
an on-screen second opinion only. They are not persisted or passed into signal, risk, sizing or
execution. The configured execution weight is zero and no fusion calculation is performed.

Upstream references:

- repository and disclaimer: https://github.com/tradesdontlie/tradingview-mcp
- security policy: https://github.com/tradesdontlie/tradingview-mcp/blob/main/SECURITY.md

## Architecture

```text
Gate public data -> Nautilus strategy -> Nautilus risk -> paper execution
         |                                      |
         +---------------- authoritative -------+

TradingView Desktop -> local CDP -> pinned tv status/quote/values -> research monitor
                                                                  |
                                                                  +-> dashboard card only
```

`TradingViewMonitor` has no strategy, order, position, portfolio or risk-engine reference. It runs on
a daemon thread, and the dashboard reads its last snapshot. Disabling or losing TradingView does not
change bot health, Gate freshness, Nautilus state or paper controls.

## Implemented Scope

- pinned external checkout and reproducible installer;
- centralized Gate/Nautilus/TradingView perpetual symbol mapping;
- argument-list subprocess execution with `shell=False`;
- forced `TV_CDP_HOST=127.0.0.1` and port 9222 for every adapter call;
- 2.5 second configurable timeout, one retry, strict JSON/schema validation and 64 KiB output cap;
- health TTL cache, stale detection and three-failure/60-second circuit breaker;
- separate `CONNECTED`, `DEGRADED`, `STALE`, `DISCONNECTED`, `UNAVAILABLE`, and `DISABLED` states;
- symbol/timeframe mismatch detection without changing the user's chart;
- bounded quote and current indicator-value reads for dashboard research;
- display-only Gate price divergence and Nautilus direction agreement;
- secondary dashboard card and failure-state Playwright coverage;
- no raw CLI stdout/stderr logging and no browser/CDP endpoint disclosure.

Not implemented by design: OHLCV/Pine/depth extraction, screenshots, signal fusion, automated
decision-making, chart mutation, persistence, or any TradingView-triggered order action.

## Pin and Installation

Tested upstream checkout:

```text
repository: https://github.com/tradesdontlie/tradingview-mcp.git
commit: c05b8f5755ed8e64ea242de88ddbf46aa24d56a4
package version: 1.0.0
Node: 24.19.0
npm: 11.6.1
```

The checkout lives at `vendor/tradingview-mcp` and is intentionally ignored by the parent repository.
`vendor/tradingview-mcp.pin` is the reviewable source of truth.

Install or verify without changing the pin:

```bat
setup_tradingview.bat
```

The installer uses the existing Codex Node runtime and `npm ci --ignore-scripts`. It does not create a
global `tv` command and does not enable the feature.

## Optional Startup

TradingView Desktop and a valid subscription are upstream prerequisites. Autotrade does not install
the Desktop application or log in for the user.

After TradingView Desktop is installed:

```bat
start_tradingview_debug.bat
```

The script reuses an existing loopback-only CDP listener or launches the verified per-user Desktop
copy with remote debugging enabled. It fails unless port 9222 is listening only on `127.0.0.1` or
`::1`. `autostart_dashboard.bat` calls it before starting the dashboard; a sidecar failure remains
non-fatal to PAPER startup and is reported as unavailable in the dashboard.

Enable only for local research in the dashboard process:

```powershell
$env:TRADINGVIEW_ENABLED = "true"
.\start_dashboard.bat
```

The default remains `false`. If the dashboard is already running, restart it after changing process
environment. Do not expose the dashboard or CDP through a reverse proxy, router or Docker mapping.

## Feature Flags

| Flag | Default | Enforcement |
| --- | ---: | --- |
| `TRADINGVIEW_ENABLED` | `true` | Local paper-profile research sidecar |
| `TRADINGVIEW_CONFIRMATION_MODE` | `async` | Background observation only |
| `TRADINGVIEW_WEIGHT` | `0.00` | No execution or signal influence |
| `TRADINGVIEW_TIMEOUT_MS` | `2500` | Validated at 250-5000 ms |
| `TRADINGVIEW_CACHE_ENABLED` | `true` | Research snapshot cache |
| `TRADINGVIEW_PINE_ENABLED` | `true` | Research capability metadata only |
| `TRADINGVIEW_SCREENSHOT_ENABLED` | `false` | Startup rejects `true` in Phase 1 |

Additional TOML settings control health cache TTL (15 s), polling (15 s), staleness (45 s), and the
expected chart timeframe (`5`).

## Failure Isolation

- missing Node/CLI: `UNAVAILABLE`;
- Desktop or CDP absent: `DISCONNECTED`, or `UNAVAILABLE` if the strict timeout fires first;
- malformed/oversized/changed JSON: `UNAVAILABLE`;
- chart API unavailable, wrong symbol or wrong timeframe: `DEGRADED`;
- old successful observation: `STALE`;
- repeated failures: circuit opens for 60 seconds;
- all states: Gate/Nautilus paper operation continues unchanged.

Raw subprocess output is discarded on failures because it could contain session-related data.

## Verification Snapshot - 2026-08-28

- Autotrade Python unit/integration tests: 28 passed;
- Ruff: passed;
- mypy: passed;
- installer: passed and exact commit verified;
- upstream test files: 112/125 passed; 13 upstream Windows-path tests failed because the harness
  mishandles the workspace path containing a space/URL encoding;
- upstream CLI with TradingView absent: exit code 2 as expected, measured 15,764 ms;
- Autotrade adapter timeout: 2,500 ms per attempt, isolated from Nautilus;
- enabled failure injection on an isolated dashboard: Gate `LIVE`, Nautilus `SIMULATION_READY`,
  TradingView `UNAVAILABLE` after timeout, zero orders/positions, exactly one listener;
- Playwright: 3/3 passed with connected, disconnected and 390 px mobile screenshots, no console/API
  errors and no page-level horizontal overflow;
- production dependency audit: 5 advisories (1 low, 2 moderate, 2 high); full install reports 7
  advisories including development dependencies;
- official TradingView Desktop 3.3.0.7992 package signature verified before installation;
- live CDP verified loopback-only with chart `GATE:ETHUSDT.P` at five minutes;
- live bounded quote verified in the dashboard with Gate divergence and zero execution influence;
- Autotrade tests, Ruff, mypy, and Playwright rerun after the live symbol normalization: all passed.

Do not run `npm audit fix` blindly: it would change the pinned upstream dependency graph. Review a new
upstream commit and rerun all checks instead.

## Recommendation

Keep the sidecar at zero execution weight and use it only as local, non-persisted research visibility.
Disable it if upstream terms, dependency advisories, or Desktop/CDP reliability become unacceptable.
