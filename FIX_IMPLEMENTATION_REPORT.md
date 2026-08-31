# Autotrade 7.0 Fix Implementation Report

## 1. Executive summary

The paper runtime now distinguishes recovery from a new experiment and fails closed when historical
accounting cannot be proven. The existing schema-v2 run is preserved at 292.79394293 USDT and is
currently reported as `ACCOUNTING INVALID` / `STATE_INVALID`; it was not reset to 300 USDT and cannot
be resumed. A separately confirmed `new-paper-run` command prepares a clean schema-v3 experiment only
while the dashboard is stopped.

New runs have run/session/build identity, a validated schema-v3 event ledger, monotonic event sequence,
atomic checkpoints, Decimal reconciliation, deterministic UTC rollover, tick-aware quote construction,
symbol-local quarantine, lifecycle IDs, storage diagnostics, and a graceful Windows shutdown path.
The application remains PAPER-only with Nautilus risk checks enabled and no live-order adapter.

## 2. Root causes

Confirmed root causes:

- The schema-v2 checkpoint contained the persisted balance, but there was no authoritative startup
  reconciliation tying checkpoint, engine, event ledger, fees, positions, risk state, and API output.
- Historical event rows lacked durable run/session/signal/order/fill/position identity, so the legacy
  ledger could not independently prove every accounting field.
- Daily-risk state could be mutated during snapshot publication and a later-day restart could clear a
  `DAILY_LOSS` halt, which mixed reporting with state transition.
- Pessimistic quotes used float formatting rather than outward Decimal quantization to the exact Gate
  contract tick. One symbol exception crossed the global execution boundary.
- The event/checkpoint files lacked a shared sequence boundary and explicit experiment identity.

Rejected hypothesis: the 300 USDT API display was not evidence of profit. It was a restart/accounting
presentation defect. No profitability claim is supported by this implementation.

## 3. Files changed

- `autotrade/integrity.py`: v3 schema validator/writer, ledger scan, lifecycle validation, Decimal
  reconciliation, run metadata, archival copy, and explicit new-run creation.
- `autotrade/paper.py`: startup reconciliation, v3 event/lifecycle emission, atomic v3 checkpoint,
  engine-balance verification, deterministic risk rollover, symbol quarantine, tick-safe quotes,
  diagnostics, recovery identity, and fail-closed persistence.
- `autotrade/__main__.py`: confirmed `new-paper-run` command that refuses an active dashboard.
- `autotrade/dashboard.py`: independent accounting/risk/execution/run/storage API sections, resume gate,
  quarantine publication, and loopback graceful shutdown.
- `dashboard/index.html`, `dashboard/app.js`, `dashboard/styles.css`: prominent accounting banner and
  run/build/checkpoint/storage diagnostics while retaining the existing price ticker and 48-hour trade
  history.
- `health_check.ps1`: accounting and storage checks plus 5 MB timestamped log rotation.
- `stop_bot.bat`: graceful pause-and-shutdown path; it never force-terminates a process.
- `tests/test_integrity.py`, `tests/test_paper.py`, `tests/test_dashboard.py`,
  `tests/e2e/dashboard.spec.js`: accounting, event, recovery, rollover, quote, API, and UI regressions.
- `README.md` and the architecture, trading, risk, dashboard, and Windows operations specifications:
  current recovery/new-run semantics and supported operations.

The pre-existing `AUDIT_REPORT_2026-08-30_2000_WIB.md` was used as historical evidence and was not
rewritten.

## 4. Accounting reconciliation

The authoritative invariant for a closed-position run is:

`checkpoint balance = starting equity + sum(v3 closed-position realized PnL)`

For one open position, its already-charged entry fee is additionally subtracted. Persisted cumulative
realized PnL, accumulated fees, trade count, open-position identity, checkpoint event sequence, and the
Nautilus opening balance must independently agree. Decimal comparisons use a documented 0.00000001
USDT tolerance. An inconsistency sets `accounting.state=INVALID`, disables entries and both resume
paths, gives `STATE_INVALID` risk priority, and is made prominent in API/UI. No mismatch is silently
repaired.

The mandatory 292.99226546 USDT restart fixture now proves that Nautilus and the API initialize from
292.99226546, daily PnL remains -7.00773454 for that risk window, 51 trades remain attributed, and the
sticky daily-loss state cannot auto-resume.

## 5. Event schema

Every new trading event is schema v3 and contains the complete nullable base field set required by the
objective. Applicable identity fields are mandatory UUIDs. Event IDs are deterministic UUIDv5 values
of run ID, sequence, and event type; event sequence is contiguous and monotonic. The scanner rejects
malformed events, duplicate event IDs, sequence gaps/regressions, orphan orders, orphan fills, duplicate
position opens, and orphan closes.

Signal, entry order, entry fill(s), position, exit order, exit fill(s), and close share durable identity.
Position close records include position-open/close timestamps and derived holding time. Legacy rows are
left byte-for-byte untouched and remain readable only as legacy/unattributed history.

## 6. Risk rollover

UTC rollover is a controlled persisted transition, not a snapshot side effect. The previous window is
checkpointed, then the new UTC day, start equity, and start trade count are established and checkpointed.
Daily PnL is always current equity minus current risk-day start equity.

`DAILY_LOSS` remains sticky through ordinary restart. On a valid later-day transition it becomes
`DAILY_LOSS_REVIEW`; fresh data and explicit manual resume are still required. Rollover never clears
`STATE_INVALID`, recovery-required, drawdown, storage, persistence, or execution-integrity failures.

## 7. Quote handling

Raw bid/ask are parsed as finite Decimals and must satisfy `0 < bid < ask`. Adverse slippage is then
applied. Bid rounds outward down and ask rounds outward up to the actual Gate `order_price_round` tick.
If required, one safe tick is added to preserve a pessimistic minimum spread. A still-invalid quote is
quarantined at symbol scope. It cannot stop unrelated valid symbols or corrupt the shared account.

Coverage includes low/high prices, tick boundaries, valid narrow spreads, zero prices, locked/crossed
raw quotes, and a two-symbol case where the valid symbol continues.

## 8. Recovery

Ordinary restart loads the same run and compares its v3 checkpoint to its run ledger before Nautilus
accepts entries. A known open position retains its `position_id` and requires a fresh-quote manual
recovery flatten. Unknown legacy identity is not fabricated and cannot mutate an invalid run.

The explicit new-run command refuses missing confirmation and refuses while port 8767 is active. It
copies prior checkpoint/event evidence into a timestamped legacy archive, creates immutable run
metadata plus an empty v3 ledger, and atomically creates a reconciled checkpoint. It is never invoked
as automatic recovery.

## 9. Market-data architecture

Trading remains on the existing Gate public 5-second REST screening baseline. The isolated Gate public
WebSocket capture/replay foundation already records exchange/local timestamps, book sequence, gaps,
snapshot recovery, hash-chain integrity, and deterministic local-book replay. It remains research-only
and is intentionally not connected to strategy decisions until longer capture/replay acceptance proves
it safe. TradingView remains zero-weight advisory-only.

## 10. Tests

Final verification on 2026-08-31:

- `check.bat`: 74 unit/integration tests passed; Ruff passed; mypy passed.
- `check_ui.bat`: 4 Chromium Playwright tests passed.
- Explicit new run without confirmation: refused.
- Explicit new run while dashboard active: refused.
- `stop_bot.bat`: exit 0, listener count changed from one to zero without force termination.
- `autostart_dashboard.bat --recover`: exit 0, restored exactly one listener.
- Live API after restart: PAPER, data LIVE, equity 292.79394293, accounting INVALID, trading HALTED,
  Resume false.
- Live screenshot verified the accounting failure banner, disabled Resume, run diagnostics, market table,
  and 48-hour trade history.

## 11. Remaining limitations

- Queue position, fill ratio, decision/submit/fill latency, adverse selection, and funding are explicitly
  `NOT_MODELED`; paper fills are not evidence of live execution realism.
- The trading path still uses REST snapshots. WebSocket capture/replay is not strategy input.
- The current historical schema-v2 run cannot be proven and therefore remains invalid. Starting a new
  run requires the operator's explicit command after reviewing the archived evidence.
- JSONL event append and checkpoint replace are individually durable, not one cross-file ACID commit.
  A crash between them is detected as a sequence mismatch and halts rather than being silently repaired.
- `Timestamp.utcnow` deprecation warnings originate inside the installed NautilusTrader dependency;
  they are visible and not suppressed. Upgrade should be evaluated separately against full regression.

## 12. New paper-run readiness

All P0 accounting, checkpoint, recovery, event-integrity, PAPER-only, and full-test gates pass for the
new v3 run mechanism. The existing legacy run is intentionally not resumable. A fresh baseline may be
created only by stopping the dashboard and executing the confirmed `new-paper-run` command; the first
dashboard startup must then show `accounting.state=VALID` before Resume is enabled.

READY_FOR_FRESH_PAPER_BASELINE
