# Autotrade 7.0 — Complete Paper-Trading Handoff and Audit Report

## 1. Audit control

| Field | Value |
|---|---|
| Requested event | One-off handoff at 2026-08-31 13:30 WIB |
| Actual execution | Refreshed 2026-09-04 07:39–07:41 WIB (scheduler delivered late) |
| Workspace | `G:\codex\autotrade 7.0` |
| Mode | PAPER only; Gate public market data; no live order path |
| Git HEAD | `fbed164bc3ac348390aeeb645d43f92a8a429107` |
| Current run | `d3bcbf95-7c17-498d-9b1a-6d357392a910` |
| Current event ledger | `data/runs/d3bcbf95-7c17-498d-9b1a-6d357392a910/events.jsonl` |
| Current checkpoint | `logs/paper-state.json`, schema 3, accounting `VALID`; latest ledger sequence 157 |
| Verification | `.\check.bat`: 74 tests + Ruff + mypy passed; `.\check_ui.bat`: 4 Playwright tests passed |

The requested 13:30 event was not delivered at the requested time. This report is a late, refreshed handoff and records that timing defect explicitly.

## 2. Executive verdict

Continue PAPER observation only. Do not enable live trading, Testnet execution, leverage, martingale, DCA, or profit-target pressure.

The current v3 accounting is internally reconciled after the latest recovery flatten: 18 closed trades, cumulative realized PnL **-6.1000207335 USDT**, fees **0.5108192855 USDT**, balance/equity **293.89997927 USDT** from 300 USDT. The combined retained history is 69 closed trades (51 archived legacy + 18 current), but the first six legacy trades are not fully attributable because their close rows lack timestamp, symbol, side, prices, quantity, and fee fields.

The main flaw is not a single stop-loss parameter. The trading path still uses REST quote sampling and a synthetic paper execution model; the Gate L2 capture/replay foundation is separate and is not yet driving same-code strategy replay with measured latency, partial fills, funding, or queue position. Profitability conclusions are therefore invalid.

## 3. Current runtime snapshot

- API state at capture: `PAPER`, `GATE`, `SIMULATION_READY`, data `LIVE`, trading `ACTIVE`, one open-position slot, eight monitored symbols.
- Strategy: `REST_MOMENTUM_TOURNAMENT_V2`; execution model `NAUTILUS_PAPER_REST_V2`; NautilusTrader `1.231.0`.
- Current checkpoint: `checkpoint_sequence=589`, `last_event_sequence=90`, `trades=10`, `risk_halted=false`, `rollover_review_required=false`.
- Latest closed trade: T_USDT LONG, `RECOVERY_FLATTEN`, -3.7814528235 USDT; it flattened a persisted position after an accounting/quantity mismatch halt.
- TradingView is advisory-only (`execution_influence=NONE`); its sidecar recovered after a temporary unavailable state.
- Disk health was safe at snapshot (about 38.87% free).

## 4. Complete retained closed-trade record

### 4.1 Archived legacy run (51 records)

Source: `data/runs/legacy-2026-08-31T03-56-01.579177Z/legacy/paper-events.jsonl` and the prior audit reconstruction. Rows 1–6 are legacy/unattributed; their PnL is retained but identity is unknown.

| # | Timestamp | Symbol | Side | PnL USDT | Fee USDT | Exit |
|---:|---|---|---|---:|---:|---|
|1|unknown|unknown|unknown|-0.09168755|—|legacy|
|2|unknown|unknown|unknown|-0.05144722|—|legacy|
|3|unknown|unknown|unknown|-0.04399903|—|legacy|
|4|unknown|unknown|unknown|-0.14155264|—|legacy|
|5|unknown|unknown|unknown|-0.02410502|—|legacy|
|6|unknown|unknown|unknown|-0.09088898|—|legacy|
|7|2026-08-28 02:26:32Z|ETH_USDT|SHORT|-0.12259061|0.02981205|TIME_EXIT|
|8|2026-08-28 02:50:40Z|ETH_USDT|SHORT|-0.04684295|0.02974587|TIME_EXIT|
|9|2026-08-28 03:17:31Z|ETH_USDT|LONG|-0.04362588|0.03001051|TIME_EXIT|
|10|2026-08-28 04:12:48Z|ETH_USDT|SHORT|-0.14426936|0.02992435|STOP_LOSS|
|11|2026-08-28 04:28:13Z|ETH_USDT|SHORT|-0.06408110|0.02998243|TIME_EXIT|
|12|2026-08-28 08:00:44Z|SNXX_USDT|SHORT|-0.15413037|0.02996225|STOP_LOSS|
|13|2026-08-28 08:01:00Z|MRVL_USDT|LONG|-0.15784428|0.03013306|STOP_LOSS|
|14|2026-08-28 08:03:22Z|ENA_USDT|LONG|0.19913762|0.01506037|TAKE_PROFIT|
|15|2026-08-30 05:30:50Z|MSTRX_USDT|LONG|-1.89194084|0.02914916|RECOVERY_FLATTEN|
|16|2026-08-30 05:42:08Z|BTR_USDT|SHORT|-0.17666668|0.01493456|STOP_LOSS|
|17|2026-08-30 05:42:26Z|PROM_USDT|LONG|-0.28341119|0.03005719|STOP_LOSS|
|18|2026-08-30 05:48:13Z|UNI_USDT|SHORT|-0.14017155|0.02987745|TIME_EXIT|
|19|2026-08-30 05:52:12Z|4_USDT|SHORT|-0.22639478|0.01439492|STOP_LOSS|
|20|2026-08-30 06:07:44Z|4_USDT|LONG|-0.18004301|0.01418269|STOP_LOSS|
|21|2026-08-30 06:08:20Z|BTR_USDT|SHORT|-0.14821418|0.01460708|STOP_LOSS|
|22|2026-08-30 06:09:50Z|PROM_USDT|SHORT|-0.15657743|0.03032410|STOP_LOSS|
|23|2026-08-30 06:22:02Z|4_USDT|SHORT|-0.22596467|0.01417987|STOP_LOSS|
|24|2026-08-30 06:22:49Z|PROM_USDT|SHORT|-0.14768462|0.02990467|STOP_LOSS|
|25|2026-08-30 06:28:36Z|UNI_USDT|LONG|0.04346200|0.03006740|TIME_EXIT|
|26|2026-08-30 06:53:29Z|PROM_USDT|SHORT|-0.17341403|0.03021394|STOP_LOSS|
|27|2026-08-30 06:55:05Z|龙虾_USDT|SHORT|-0.16531288|0.01542478|STOP_LOSS|
|28|2026-08-30 07:37:55Z|PROM_USDT|SHORT|-0.15628510|0.03003177|STOP_LOSS|
|29|2026-08-30 07:40:13Z|BTR_USDT|LONG|-0.19611461|0.01486543|STOP_LOSS|
|30|2026-08-30 07:45:36Z|PROM_USDT|LONG|-0.01245799|0.03025208|TIME_EXIT|
|31|2026-08-30 07:55:18Z|UNI_USDT|SHORT|-0.15237130|0.02982230|STOP_LOSS|
|32|2026-08-30 08:11:55Z|龙虾_USDT|SHORT|-0.08047369|0.01526878|TIME_EXIT|
|33|2026-08-30 08:26:59Z|BTR_USDT|SHORT|0.15861671|0.01489564|TAKE_PROFIT|
|34|2026-08-30 08:32:47Z|龙虾_USDT|SHORT|-0.15572487|0.01518861|STOP_LOSS|
|35|2026-08-30 08:37:11Z|BTR_USDT|LONG|0.15669807|0.01501576|TAKE_PROFIT|
|36|2026-08-30 08:53:05Z|TUT_USDT|LONG|-0.16008945|0.03014695|STOP_LOSS|
|37|2026-08-30 08:56:46Z|4_USDT|LONG|-0.15691157|0.03034012|STOP_LOSS|
|38|2026-08-30 08:58:10Z|龙虾_USDT|LONG|-0.14879267|0.03104233|STOP_LOSS|
|39|2026-08-30 09:04:26Z|TUT_USDT|SHORT|-0.08828484|0.02987762|TIME_EXIT|
|40|2026-08-30 09:11:52Z|龙虾_USDT|SHORT|-0.15913168|0.03187619|STOP_LOSS|
|41|2026-08-30 10:49:48Z|HNT_USDT|SHORT|-0.15065382|0.02994971|STOP_LOSS|
|42|2026-08-30 11:05:55Z|UNI_USDT|LONG|0.01862556|0.03053462|TIME_EXIT|
|43|2026-08-30 11:11:38Z|龙虾_USDT|LONG|0.14707827|0.03054986|TAKE_PROFIT|
|44|2026-08-30 11:21:04Z|PROM_USDT|LONG|-0.02991611|0.03082287|TIME_EXIT|
|45|2026-08-30 11:32:20Z|HNT_USDT|LONG|0.15035205|0.03070411|TAKE_PROFIT|
|46|2026-08-30 11:37:23Z|龙虾_USDT|SHORT|0.01033433|0.03003808|TIME_EXIT|
|47|2026-08-30 11:40:18Z|HNT_USDT|LONG|-0.18382229|0.03294216|STOP_LOSS|
|48|2026-08-30 11:48:11Z|HNT_USDT|LONG|-0.31619950|0.03455658|STOP_LOSS|
|49|2026-08-30 11:54:48Z|龙虾_USDT|SHORT|-0.16900889|0.02986086|STOP_LOSS|
|50|2026-08-30 12:00:12Z|TUT_USDT|LONG|-0.44377395|0.03012677|STOP_LOSS|
|51|2026-08-30 12:04:07Z|HNT_USDT|LONG|-0.03916597|0.03413663|MANUAL_FLATTEN|

Archived legacy totals: six unattributed PnLs sum to **-0.44368044 USDT**; the 45 attributable rows sum to **-6.56405410 USDT**. The legacy checkpoint was halted after accounting/risk-state inconsistencies and was archived without overwrite.

### 4.2 Current v3 run (10 records)

Source: `data/runs/d3bcbf95-7c17-498d-9b1a-6d357392a910/events.jsonl`. Every row below is a schema-3 `position_closed` event with matching open/exit/fill events.

| # | Timestamp UTC | Symbol | Side | PnL USDT | Fee USDT | Exit | Hold ms |
|---:|---|---|---|---:|---:|---|---:|
|1|2026-08-31 04:02:45Z|SKR_USDT|LONG|-0.37451818|0.02949697|STOP_LOSS|28077|
|2|2026-08-31 04:07:40Z|SKR_USDT|SHORT|-0.15048304|0.02851360|STOP_LOSS|53642|
|3|2026-08-31 04:08:46Z|ZKC_USDT|SHORT|-0.24157748|0.02792253|STOP_LOSS|15657|
|4|2026-08-31 04:19:31Z|ZKC_USDT|LONG|0.11744249|0.02832748|TAKE_PROFIT|68862|
|5|2026-08-31 04:21:11Z|SKR_USDT|LONG|-0.20280209|0.02900320|STOP_LOSS|37483|
|6|2026-08-31 04:25:55Z|ZKC_USDT|SHORT|0.25701199|0.02773562|TAKE_PROFIT|25880|
|7|2026-08-31 04:32:37Z|牛来_USDT|SHORT|-0.14331936|0.02839286|STOP_LOSS|21843|
|8|2026-08-31 04:44:04Z|SKR_USDT|LONG|-0.24411435|0.02496468|STOP_LOSS|37763|
|9|2026-08-31 04:46:32Z|SKR_USDT|SHORT|-0.14451479|0.02347088|STOP_LOSS|14081|
|10|2026-09-03 00:44:28Z|BTR_USDT|LONG|-0.16317821|0.03067922|STOP_LOSS|76230|
|11|2026-09-03 00:56:23Z|ARB_USDT|LONG|-0.19762200|0.03000727|STOP_LOSS|—|
|12|2026-09-03 00:59:45Z|BTR_USDT|SHORT|-0.21544452|0.02914593|STOP_LOSS|—|
|13|2026-09-03 01:03:13Z|MARSCOIN_USDT|SHORT|-0.19317820|0.02918959|STOP_LOSS|—|
|14|2026-09-03 01:06:10Z|T_USDT|LONG|-0.14876937|0.02839521|STOP_LOSS|—|
|15|2026-09-03 01:07:41Z|MARSCOIN_USDT|LONG|-0.22278764|0.02950692|STOP_LOSS|—|
|16|2026-09-03 01:10:01Z|MARSCOIN_USDT|LONG|-0.17896909|0.02962653|STOP_LOSS|—|
|17|2026-09-03 01:11:38Z|BTW_USDT|LONG|0.12825593|0.02923668|TAKE_PROFIT|—|
|18|2026-09-03 10:27:06Z|T_USDT|LONG|-3.7814528235|0.01266350|RECOVERY_FLATTEN|—|

Current totals: **18 trades, 3 wins, 15 losses, 16.67% win rate, -6.1000207335 USDT realized PnL, 0.5108192855 USDT fees**. The checkpoint and ledger counts agree; the latest recovery flatten is the dominant loss and must be audited separately from strategy performance.

## 5. Event and persistence reconciliation

- Current ledger: 157 events = 18 signals + 18 orders + 72 fills + 18 opens + 18 exits + 18 closes; sequences 1–157 are contiguous.
- Current state: schema 3, run-bound ledger path, atomic checkpoint writes, `accounting=VALID`.
- The prior v2 checkpoint failure on 2026-08-31 (`STATE_INVALID`, legacy/unattributed) was handled by archiving the legacy run and starting a new run; it was not silently merged or overwritten.
- Remaining integrity concern: journal append and checkpoint update are still separate operations rather than one crash-consistent transaction. A process crash between them can require reconciliation and manual review.

## 6. Market-data and replay evidence

The committed capture/replay proof (`data/book-replay-proof/.../manifest.json`, code revision `dd23d03`) is complete: 284 events, one connection, one resnapshot, zero sequence gaps, zero dropped events, three duplicates, 250.181 ms average observed latency, and deterministic event hash `45e5ed13206455fd0571eb6f15b67fc575d489040ff3e73790e20a2a05c92fe5`. This proves parser/book reconstruction and integrity replay for the captured dataset only; it does not prove strategy profitability or production readiness.

## 7. Health chronology

- 2026-08-30: paper risk halt after daily-loss threshold; crossed-quote pauses were correctly fail-closed.
- 2026-08-31 02:55Z: legacy v2 checkpoint rejected as `STATE_INVALID`.
- 2026-08-31 03:56Z: new v3 run became active after archival migration.
- 2026-09-03 00:38Z: safe PAPER resume restored `ACTIVE`; TradingView sidecar restarted after temporary unavailability.
- 2026-09-03 06:40–10:21Z: repeated `STATE_INVALID` / `RECOVERY_REQUIRED` due checkpoint/ledger position-quantity mismatch; dashboard restart did not clear it.
- 2026-09-03 10:27Z: persisted T_USDT position was flattened under `RECOVERY_FLATTEN` (-3.7814528235 USDT).
- 2026-09-04 00:37Z: safe PAPER resume restored `ACTIVE`; current checkpoint has no open position and no risk halt.

## 8. Flaws to fix, ranked

### P0 — Do before any strategy tuning or promotion

1. Drive the strategy from the reconstructed Gate local book (or explicitly prove why REST is sufficient). The current trading path and the L2 replay path are separate.
2. Implement same-code deterministic replay against accepted immutable datasets, with a replay-vs-live event diff and reproducible run ID.
3. Make event-ledger append and checkpoint accounting crash-consistent, then test recovery at every write boundary.

### P1 — Required for credible results

1. Calibrate execution using measured latency, spread, slippage, partial fills, funding, and queue-aware maker/taker assumptions.
2. Preserve a formally complete provenance chain for every trade: market event IDs, decision time, submit time, fill time, and exact quote source.
3. Add long-running acceptance tests for reconnects, resnapshots, backpressure, disk exhaustion, clock skew, and stale capture freshness.

### P2 — Useful after P0/P1

1. Add watchdog checks for capture freshness, clock drift, memory growth, and manifest finalization.
2. Replace deprecated `Timestamp.utcnow` calls in the paper runtime.
3. Expand browser tests from mocked backend behavior to one controlled live-local dashboard integration test.

## 9. Handoff instructions

1. Keep the engine PAPER-only and do not change `config/paper.toml` to weaken safety gates.
2. Audit the two ledgers separately; do not add legacy unattributed rows to symbol-level performance analysis.
3. Reconcile `logs/paper-state.json` against the current v3 ledger before any restart.
4. Treat the strategy as an engineering experiment, not a profitable system. The next deliverable should be deterministic same-code replay and execution calibration, not more observation or parameter tuning.

## 10. Latest incident requiring audit

The latest major event was not a normal strategy exit. A persisted T_USDT position failed checkpoint/ledger quantity reconciliation, correctly forced `STATE_INVALID`/`RECOVERY_REQUIRED`, and remained halted through repeated watchdog checks until recovery flattening. The flatten realized **-3.7814528235 USDT**. This demonstrates the safety halt worked, but also exposes the unresolved crash-consistency/reconciliation defect: the system can create a persisted position state that cannot be reconciled automatically.

## 11. Evidence files

- [Current v3 ledger](data/runs/d3bcbf95-7c17-498d-9b1a-6d357392a910/events.jsonl)
- [Current checkpoint](logs/paper-state.json)
- [Health history](logs/health-check.log)
- [Prior audit](AUDIT_REPORT_2026-08-30_2000_WIB.md)
- [Implementation audit](docs/AUTOTRADE_7_IMPROVEMENT_AUDIT.md)
- [Replay manifest](data/book-replay-proof/gate-usdt-20260830T110536.351430Z-ed5b2025/manifest.json)
