# 1. Executive Verdict

Autotrade 7.0 is a functional **PAPER-only operations and forward-observation MVP**. It has a real
NautilusTrader backtest engine and RiskEngine, a local dashboard, a watchdog, public Gate REST data,
an eight-symbol momentum tournament, persistent paper accounting, and a new isolated Gate WebSocket
capture/replay-integrity foundation.

It is **not a validated scalping system**, a realistic exchange simulator, a complete deterministic
strategy-replay system, or evidence of profitable alpha. No real-order path exists, which is correct.

The main bottleneck is no longer UI or trade frequency. It is the chain:

```text
trustworthy local order book
→ accepted immutable datasets
→ same-code deterministic strategy replay
→ calibrated execution simulation
→ out-of-sample validation
```

The present REST momentum PnL mostly measures a mixture of a heuristic signal, five-second sampling,
synthetic top-of-book fills, fixed assumptions, and market noise. It does not measure executable
short-horizon alpha with enough validity to tune or promote.

Direct implementation completed during this audit:

- corrected lifetime PnL/trade count being mislabeled and enforced as the current UTC day's risk;
- persisted daily-risk window, peak equity, halt state/reason, and a state schema across restarts;
- made execution-integrity faults sticky across later good REST polls;
- prevented recovery flattening from using a stale cached quote;
- added finite/range validation and `fsync` before atomic paper-state replacement;
- added public Gate WebSocket capture with REST snapshots, bounded buffering/backpressure, sequence
  checks, reconnect, resnapshot, exchange/local timestamps, hash-chained JSONL sessions, manifests,
  and deterministic integrity replay;
- kept all new data capture outside the trading hot path and PAPER-only.

A 10-second proof from committed book-replay revision `dd23d03` captured ETH/USDT in one connection
with 284 events, one synchronized snapshot, zero dropped events and zero sequence gaps. Replay
reproduced event hash `45e5ed13206455fd0571eb6f15b67fc575d489040ff3e73790e20a2a05c92fe5`
and deterministic final-book digest
`dead2047ce5033dcca505ae5fdda5026070f37d194aef06faa5d7e17f6fb577c`. That proves the capture/book
vertical slice works; it does not prove long-run reliability or trading edge.

# 2. Verified Current Architecture

```text
Gate public REST futures tickers (5 s)
                |
                v
rank/filter 5–10 pairs by 24 h move, turnover, spread
                |
                v
DashboardState + PaperTrader (one long-running Python process)
                |
                v
Nautilus BacktestEngine + active RiskEngine
                |
                v
8 RestMomentumStrategy observers → one portfolio execution slot
                |
                v
synthetic pessimistic QuoteTick → simulated market order/fill/PnL
                |
        +-------+--------+
        |                |
paper-state.json   paper-events.jsonl
        |
localhost HTTP API → native HTML/CSS/JS dashboard

Separate research path, no execution influence:
Gate public WS + REST snapshots → capture.py → immutable dataset + replay verification

Separate optional observer, no execution influence:
TradingView Desktop → localhost CDP → pinned CLI → dashboard card

Separate operations path:
Windows Startup launcher → dashboard → 15-minute singleton watchdog
```

Source evidence:

- `autotrade/dashboard.py`: REST ranking, five-second poller, authoritative API state and controls;
- `autotrade/paper.py`: strategy, Nautilus engine, single-slot coordination, simulated fills, risk,
  paper state and trade audit;
- `autotrade/runtime.py`: one-shot Nautilus PAPER smoke with `RiskEngineConfig(bypass=False)`;
- `autotrade/market_data.py`: public Gate channel validation and normalization;
- `autotrade/capture.py`: live public capture, snapshots, integrity manifest and hash replay;
- `autotrade/tradingview.py`: isolated read-only sidecar with no paper-engine reference;
- `health_check.ps1`: independent process/data/listener checks and tightly gated recovery;
- `config/paper.toml` and `autotrade/config.py`: PAPER-only, 1x, loopback, risk and strategy limits.

The dashboard is a control/read plane. The browser is not the trading engine. Closing it does not
stop the backend strategy process.

# 3. Handoff Accuracy Audit

| Handoff claim | Verified | Partially verified | Incorrect | Evidence |
|---|:---:|:---:|:---:|---|
| NautilusTrader owns PAPER execution | Yes |  |  | `PaperTrader._initialize` creates `BacktestEngine`; orders flow through strategies. |
| Nautilus RiskEngine is active | Yes |  |  | `RiskEngineConfig(bypass=False)` in paper and smoke paths; runtime API reports enabled. |
| Gate public REST ticker polling works | Yes |  |  | `fetch_gate_tickers` and `GatePoller` use the public futures ticker endpoint every five seconds. |
| Eight-symbol momentum tournament | Yes |  |  | Eight strategy observers are created from the first screened universe. |
| Highest-confidence signal gets one portfolio slot | Yes |  |  | Coordinator ranks eligible candidates and enforces at most one open position. |
| Spread, fees and configured slippage are modeled | Yes |  |  | Quote bid/ask are widened by slippage; strategy cost sees the widened spread plus 10 bp taker fees and buffer. |
| Stops, target and five-minute time exit work |  | Yes |  | They run on five-second quote events and synthetic mid/fills, not trade-by-trade executable prices. |
| Split-fill dashboard accounting is corrected | Yes |  |  | Entry/exit quantities, weighted prices and all fees are aggregated. |
| Partial fills are realistic |  |  | Yes | Splits arise from a fixed synthetic 100-base-unit top size, not observed Gate depth or queue behavior. |
| Paper persistence is atomic | Yes |  |  | State now validates schema/values, flushes and `fsync`s a temp file, then replaces it. |
| Persistence fully reconciles crashes |  | Yes |  | No write-ahead transaction joins Nautilus fill events to state; a crash between audit append and checkpoint remains possible. |
| Recovery flatten requires a fresh quote | Yes |  |  | Recovery now rejects a cached quote older than `market_stale_after_seconds`. |
| Daily PnL and trades are daily | Yes |  |  | Fixed during this audit; legacy state is migrated from timestamped trades into the UTC-day window. |
| Risk halts survive restart | Yes |  |  | State schema v2 persists the halt reason, day baseline and peak equity. |
| Critical execution faults cannot auto-resume | Yes |  |  | Fixed during this audit; the execution error remains sticky until restart/recovery. |
| Local dashboard and 48-hour trade audit work | Yes |  |  | Backend state is authoritative; UI shows open-trade ticker and 48-hour closed history. |
| Watchdog is independent and singleton |  | Yes |  | It verifies service/data/listener ownership, but lacks disk, clock, memory and capture-session checks. |
| TradingView is isolated from execution | Yes |  |  | `TradingViewMonitor` has no strategy/risk/order reference; weight is zero and UI-only. |
| Gate WebSocket/L2/trade capture is absent |  |  | Yes, now outdated | A live public capture foundation now exists, but it is intentionally not an execution feed. |
| Complete local book reconstruction exists |  | Yes |  | Deterministic replay now reconstructs and validates final depth plus spread/depth/imbalance/microprice; no book stream feeds strategies yet. |
| Deterministic replay exists |  | Yes |  | Hash-chain replay is deterministic; same-code strategy replay and simulator output are pending. |
| Measured latency and realistic execution exist |  |  | Yes | Capture measures feed latency; the simulator does not yet use submission/ack/cancel/fill latency. |
| Funding is accounted in PnL |  |  | Yes | Funding is displayed from REST/raw WS but never debited or credited to positions. |
| Holdout/walk-forward validation exists |  |  | Yes | No accepted dataset, split registry, walk-forward report or promotion result exists. |
| Git has no commits |  |  | Yes, now outdated | Baseline commit `e0402b7` and data-foundation commit `4991eca` now exist. |
| Current paper result proves strategy quality |  |  | Yes | Forty historical trades and synthetic execution are insufficient and contaminated by model/recovery effects. |

# 4. Critical Problems

## P0

1. **No authoritative reconstructed book feeds research or execution.** Raw capture and sequence
   synchronization now exist, but strategies still see five-second REST top-of-book values.
2. **No same-code deterministic strategy replay.** The dataset hash can be verified, but captured
   events do not yet run through the same strategy plus simulator to produce identical journals.
3. **Execution results are not realistic enough to optimize.** Fixed top size, inferred instrument
   precision, no depth sweep, no measured order latency, no funding and no queue model dominate
   short-horizon results.
4. **No accepted long-duration dataset.** A ten-second proof is a protocol check, not a research
   corpus. Disk growth, reconnects, clock behavior and resnapshot recovery remain unproven.

## P1

1. Gate instrument metadata is synthesized from the current price. Tick size, quantity step, minimum
   size, contract multiplier and maintenance terms are not authoritative.
2. Pair selection calls absolute 24-hour price change “volatility” and does not measure depth, trade
   rate, realized volatility, impact or stability.
3. Position size is fixed when the initial universe is created; notional drifts with price and equity.
4. Paper journal/checkpoint writes are not one crash-consistent transaction and are not reconciled at
   startup.
5. The fixed strategy confidence is a score (`net edge / hurdle`), not a calibrated probability.
6. No mandatory research report computes cost decomposition, MFE/MAE, segmentation, confidence
   intervals or parameter sensitivity.

## P2

1. Watchdog does not check free disk, capture freshness, clock synchronization, memory growth or log
   retention.
2. `paper-events.jsonl` and watchdog logs lack bounded rotation/archival.
3. Initial strategy universe is sticky for the process lifetime; pair eligibility changes but new
   strategy observers are not added dynamically.
4. Dashboard Playwright tests mock APIs; they do not prove a real backend control/data round trip.
5. Nautilus emits a pandas deprecation warning under the pinned version. It is not a current safety
   failure, but should be tracked with the dependency upgrade plan.

## P3

1. Research/settings UI pages can wait until research artifacts exist.
2. Maker simulation, micro-maker strategy and visualization can wait until L2 replay and taker fills
   are trustworthy.
3. VPS, multi-exchange support and richer deployment tooling are premature.

# 5. Things We Should NOT Build Yet

- Live or TestNet order submission, private Gate credentials, leverage above 1x.
- Optuna or any optimizer before accepted replay datasets and a valid objective exist.
- MLflow; a small SQLite registry and immutable files are sufficient.
- AI/LLM buy-sell agents, reinforcement learning or automatic strategy mutation.
- A micro-maker strategy before queue, cancel and adverse-selection simulation exists.
- React, Next.js, Redis, Kafka, Kubernetes, microservices or cloud orchestration.
- More indicators on the current REST momentum heuristic.
- A forced profit target, forced trade count, martingale, DCA or looser entry threshold to create
  activity.
- Multi-exchange execution; it multiplies data and reconciliation problems before one venue is valid.
- Dashboard polish beyond exposing truthful data/research health.

# 6. Market Data Assessment

## Current REST path

- Source: Gate USDT perpetual ticker REST response.
- Poll frequency: five seconds.
- Values: last, best bid/ask, 24-hour change/turnover and funding rate.
- Timestamp: local receipt time only; no authoritative exchange time enters the strategy.
- Staleness: local time since the last successful HTTP response; an unchanged upstream payload can
  still look live.
- Validation: finite positive prices, non-crossed bid/ask, spread and volume thresholds.
- Missing: trades, L2 sizes/depth, update rate, exchange sequence, microprice, order flow and observed
  market impact.

REST is acceptable for dashboard screening and a slow benchmark. It is not suitable for evaluating
sub-minute scalping edge.

## Implemented WebSocket capture foundation

The isolated capture path subscribes to public Gate `futures.order_book_update`, `futures.trades`,
`futures.book_ticker` and `futures.tickers`, with the documented decimal-size header. It also requests
matching REST depth snapshots. It provides:

- exchange and local receive timestamps;
- raw payload retention plus normalized protocol validation;
- `U`/`u` snapshot-delta reconciliation;
- gap, duplicate, reconnect and resnapshot counts;
- bounded WebSocket and application queues with backpressure;
- stream-stale timeout and reconnect;
- append-only canonical JSONL with a SHA-256 hash chain;
- an immutable session manifest with dataset, symbol, time, revision, event, gap, drop and latency
  metadata;
- deterministic integrity replay that rejects modified, reordered or missing rows.
- deterministic local-book replay with absolute-size replacement, zero deletion, overlap/duplicate
  handling, gap resnapshot recovery, crossed/empty-book rejection and a final metrics digest.

Official Gate's required local-book procedure is the design authority: subscribe and buffer deltas,
retrieve a REST snapshot with `id`, start at `U <= id + 1 <= u`, use absolute sizes, delete zero size,
and resnapshot on a gap. See the [Gate futures WebSocket documentation](https://www.gate.com/docs/developers/futures/ws/en/).

## Still required

1. Convert the validated replay book into time-ordered derived events consumable by the same strategy
   path; no strategy may consume an unsynchronized book.
2. Add trade-flow imbalance, update rate and rolling volatility to the implemented final-book spread,
   depth, imbalance and microprice metrics.
3. Add contract metadata capture and dataset revision.
4. Run one-hour, six-hour and 24-hour acceptance sessions; fail a session on drops, unrecovered gaps,
   invalid books, excessive clock skew or disk exhaustion risk.
5. Add segmented retention: preserve raw sessions, then derive Parquet only after raw acceptance.
6. Keep REST as a low-rate cross-check, never as the authority for an L2 strategy.

## Event schema

Every raw envelope must retain at least:

```json
{
  "schema_version": 1,
  "dataset_id": "gate-usdt-...",
  "local_sequence": 1,
  "source": "GATE_FUTURES_WS",
  "channel": "futures.order_book_update",
  "event": "update",
  "symbol": "ETH_USDT",
  "exchange_ts_ms": 1788087095094,
  "received_ts_ns": 1788087095400000000,
  "connection_id": 1,
  "previous_hash": "...",
  "payload": {},
  "event_hash": "..."
}
```

Do not discard the raw payload when producing derived records.

# 7. Execution Simulator Assessment

## Current behavior

- Market orders cross a synthetic pessimistic bid/ask.
- Taker fee is 5 bp per side; maker fee metadata is 2 bp.
- Configured slippage widens each side of the quote before the strategy and engine see it.
- A 3 bp safety buffer is included in the entry hurdle.
- Nautilus produces positions, fees, realized/unrealized PnL and rejects invalid orders.
- Fixed stop, take-profit, time exit and cooldown are event-driven.
- The synthetic top-of-book size is always 100 base units.

The model is internally consistent enough for software-path testing. It is not realistic enough for
short-horizon expectancy.

## Unrealistic assumptions

- Price/size precision and increments are inferred rather than loaded from Gate metadata.
- Trade size is fixed at initial universe creation.
- Market orders do not sweep observed book levels.
- The 100-base-unit top size creates artificial split fills for low-priced assets.
- No submit, acknowledgement, matching, cancel or network latency model exists.
- No queue position or post-only rejection model exists.
- No fill probability, stale resting order or adverse-selection model exists.
- No funding debit/credit, mark/index price or liquidation behavior exists.
- No Gate minimum order, contract multiplier or maintenance-margin rule is enforced.
- Stop/target decisions use the synthetic quote mid on a five-second clock, not observed executable
  trade/book state.

## MUST HAVE before strategy results are meaningful

1. Authoritative instrument metadata and quantity rounding.
2. Deterministic L2 market-order sweep with partial fills and explicit unfilled quantity.
3. Measured market-data, compute, submit and acknowledgement latency distributions.
4. Mark/index/funding events and funding PnL.
5. Stale-book rejection and sequence-integrity gate.
6. Complete cost decomposition per order/trade.
7. Identical simulator behavior in replay and forward PAPER paths.

## SHOULD HAVE before live consideration

- Post-only acceptance/rejection, conservative queue position and cancel latency.
- Adverse-selection measurement after fills.
- Abnormal slippage/fill/rejection kill switches.
- Sensitivity runs at worse-than-observed latency, fees, spread and depth.
- Crash-consistent order/fill/state reconciliation.

## NICE TO HAVE

- Detailed liquidation engine while leverage remains 1x.
- Full exchange matching-engine emulation.
- Sophisticated queue models beyond conservative bounds.
- Cross-venue impact and routing.

The smallest sufficient simulator is an event-time L2 book, deterministic latency offsets, depth
sweep for taker orders, conservative queue-ahead for post-only orders, Gate metadata, fees/funding,
and an auditable fill decision. Do not build an exchange clone.

# 8. Replay Architecture

```text
Gate public WebSocket + REST snapshot
              |
              v
protocol validation + local receive timestamp
              |
              v
hash-chained raw events + immutable manifest
              |
              v
dataset integrity verifier
              |
              v
snapshot/delta local-book reconstructor
              |
              v
Nautilus normalized Quote/Trade/Book events
              |
              v
same strategy code + deterministic execution simulator
              |
              v
trade journal + research report + experiment registry
```

The manifest must record:

- dataset ID, source, session, symbols, start/end, event count and file bytes;
- dropped events, sequence gaps, duplicates, resnapshots and connection count;
- first/last exchange time and observed latency distribution summary;
- Git revision, config hash, strategy revision and simulator revision;
- risk configuration and random seed, or explicit `null` where no randomness is used;
- final event hash and integrity status.

Replay ordering is `local_sequence`, while exchange time remains data used for causality and latency
checks. A strategy must never see an event before its stored local receive time. Any randomized queue
or latency draw must use a stored seed.

Acceptance test:

```text
same dataset + same Git revision + same config + same seed
→ byte-identical normalized-event digest
→ byte-identical order/fill/trade journal digest
→ identical metrics
```

Current status: raw integrity replay and deterministic final-book reconstruction pass. Nautilus event
conversion and same-strategy journal determinism are the next P0 implementation.

# 9. Strategy Assessment

The current `RestMomentumStrategy` should be retained **only as a benchmark/baseline**. Do not promote
it and do not spend more time hand-tuning its thresholds before replay is valid.

| Assumption | Rating | Reason |
|---|---|---|
| One portfolio-wide execution slot | VALID | Conservative and appropriate for $300 PAPER capital. |
| Cost hurdle before entry | VALID principle | Fees, widened spread and buffer are included, but costs are not calibrated to L2/latency. |
| No trade when no eligible signal | VALID | Prevents quotas and forced activity. |
| Long and short support | VALID | Matches perpetual product scope. |
| Five-tick persistence | PLAUSIBLE | Can reject one-tick bursts, but five REST ticks equal about 25 seconds and are unvalidated. |
| Prior/total regime agreement | PLAUSIBLE | Avoids some reversal chasing, but is not volatility/liquidity normalized. |
| 20 bp entry threshold / 12 bp net edge | UNTESTED | Chosen heuristically; no replay or sensitivity evidence. |
| 0.60 confidence | WEAK | Confidence is a bounded relative score, not a calibrated probability. |
| Absolute price move predicts continuation | UNTESTED | No out-of-sample forward-return study exists. |
| Fixed 35 bp stop / 55 bp target / 300 s hold | UNTESTED | No MFE/MAE or horizon evidence; extending hold time alone is not justified. |
| 60 s cooldown | UNTESTED | May reduce churn, but no conditional expectancy study exists. |
| 24 h absolute change is volatility/opportunity | WEAK | Ignores realized volatility, depth, trade rate, stability and impact. |
| Highest confidence is comparable across symbols | WEAK | Same formula does not normalize symbol-specific volatility/liquidity. |
| Fixed initial quantity remains 30 USDT | INVALID | Quantity is set once, so later notional drifts with price. |
| REST forward PnL estimates executable alpha | INVALID | Five-second sampling and synthetic fills dominate results. |

Recent evidence is adverse but not statistically decisive. On 2026-08-30, the timestamped journal had
26 trades and about -5.02 USDT net, including one -1.89 USDT recovery flatten. Eighteen stop losses,
five time exits and two take profits indicate poor current behavior, but the sample and simulator do
not identify whether the main cause is signal, execution assumptions, recovery contamination or
regime. The correct response is to improve measurement, not loosen gates or optimize against 26 trades.

# 10. Strategy Lab Recommendation

Build only two challengers after deterministic L2 replay exists:

1. **Order-flow momentum** — trade velocity, signed trade imbalance, microprice displacement, depth
   imbalance, liquidity pull/add behavior, spread and realized volatility.
2. **Liquidity-normalized mean reversion** — microprice/local VWAP deviation, short-window return
   z-score, flow exhaustion, replenishment and spread/depth regime.

Keep REST momentum as the naive benchmark. Do not build micro-maker yet. It needs reliable queue,
post-only, cancel and adverse-selection simulation; otherwise its backtest will be fiction.

Each strategy must emit the same contract:

- event/local receive time, symbol, direction and strategy revision;
- feature snapshot and regime label;
- gross edge, each cost component, net edge and confidence/score;
- order style, intended horizon, stop/target/invalidation;
- decision (`TRADE` or named rejection reason).

Run every challenger independently on the same datasets before adding a selector. A selector without
independent evidence creates selection bias and hides weak strategies.

# 11. Research / Statistical Validation

## Mandatory report

Returns: gross/net PnL, return, expectancy/trade and confidence interval.

Trading: count, win rate, average win/loss, payoff ratio and profit factor.

Risk: maximum/average drawdown, downside deviation, largest/tail loss and consecutive losses.

Execution: fees, funding, spread, modeled/realized simulated slippage, partial/unfilled rates,
fill/ack/cancel latency and rejection rate.

Trade quality: MAE, MFE, entry/exit efficiency and time in trade.

Segmentation: symbol, strategy, hour, day/session, long/short, volatility, spread and liquidity regime.

## Validation hierarchy

```text
unit/invariant tests
→ dataset integrity acceptance
→ in-sample research
→ untouched chronological holdout
→ rolling walk-forward
→ parameter/cost/latency sensitivity
→ block bootstrap / trade-sequence resampling
→ live-data shadow
→ long PAPER challenger
→ controlled promotion review
```

Use chronological splits. Random row splitting leaks overlapping market states. Purging and an embargo
are appropriate when feature windows, labels or holding periods overlap split boundaries. The embargo
must cover at least the maximum feature lookback plus outcome/holding horizon.

Anti-overfit controls:

- pre-register the objective and parameter ranges;
- keep a final untouched holdout;
- record every trial, including failures;
- compare with no-trade, random-time, simple momentum and simple reversion baselines;
- adjust conclusions for the number of strategies/parameters/datasets tried;
- prefer wide stable parameter regions over the single best point;
- reject results dependent on one symbol, day or regime;
- report performance under higher costs and worse latency.

## Evidence sizes

Do not use one rigid number as proof. Minimum practical gates should combine count, time and coverage:

- **Degradation alert:** at least 100 comparable trades or several hundred signals, at least 10
  trading days and more than one regime; require an economically material change outside a bootstrap
  interval, not a small point-estimate drop.
- **Parameter change:** generally at least 500 out-of-sample trades, 30 calendar days, multiple
  symbols/regimes and a stable sensitivity region.
- **Reject champion:** sustained material underperformance across two review windows, unless a hard
  safety/integrity failure requires immediate retirement.
- **Promote challenger:** at least 1,000 cost-aware out-of-sample/shadow trades where opportunity
  frequency permits, at least 30 days, at least five symbols and adverse/quiet/volatile regimes.

If the market does not produce those opportunities, wait. Do not lower the bar to meet a schedule.

# 12. Continuous Improvement Architecture

```text
PAPER champion (immutable version)
        |
        v
trade + signal + execution journal
        |
        v
scheduled performance/drift auditor
        |
        +---- no material drift ----> keep champion
        |
        v
offline research queue
        |
        v
challenger config generation
        |
        v
replay → holdout → walk-forward → robustness
        |
        v
shadow/PAPER comparison
        |
        v
manual, versioned promotion gate
```

Strict boundary:

```text
optimizer and research scheduler ≠ production/PAPER execution authority
```

No optimizer may edit the active config. It writes a new immutable candidate record. Promotion copies
an approved candidate version through an explicit operation with rollback metadata.

Use simple drift triggers:

- rolling expectancy and cost decomposition versus a fixed reference distribution;
- spread/depth/volatility/trade-rate regime shift;
- simulated versus forward fill/slippage error;
- symbol-specific deterioration;
- rejection, stale-book or sequence-gap rate.

Trigger research only when sample, duration and effect-size gates are all met. A hard integrity fault
halts immediately; performance variance does not.

# 13. Optimization Recommendation

Optuna is appropriate **later**, after deterministic replay, a fixed report, accepted datasets and a
small parameter space exist. Its constrained multi-objective support is useful, but introducing it now
would optimize simulator artifacts. See the [official Optuna multi-objective guide](https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/002_multi_objective.html).

Recommended use:

- maximize net expectancy and cross-window consistency;
- minimize drawdown/tail loss and cost sensitivity;
- enforce minimum count, data quality and symbol/regime coverage as constraints;
- keep two to four objectives; too many create a mostly non-dominated frontier;
- select from a robust Pareto region through holdout/walk-forward evidence, not a weighted PnL score.

Do not invent arbitrary weights. Use constraints for survival conditions, Pareto objectives for real
trade-offs, and a documented manual selection rule. Persist study definitions and trials in the same
SQLite registry, but never let a study overwrite champion configuration.

# 14. Risk Audit

## Current useful controls

- PAPER-only config validation and no live adapter;
- 1x leverage and 30 USDT target notional;
- one total open position;
- maximum spread and stale REST feed pause;
- minimum cost/net-edge/confidence gates;
- stop, target, maximum holding period and cooldown;
- 6 USDT UTC-day loss halt and 3% peak-equity drawdown halt;
- active Nautilus RiskEngine;
- manual pause, recovery flatten and fail-closed invalid state;
- persisted daily window, peak, halt state/reason and schema validation;
- sticky execution-integrity halt;
- stale cached recovery quote rejection.

## Missing controls and purpose

| Control | Purpose | When |
|---|---|---|
| Sequence-gap/invalid-book halt | Prevent decisions on corrupted depth | P0 with book builder |
| Maximum observed data/compute latency | Reject signals that arrive after their edge | P0/P1 |
| Symbol kill switch | Isolate bad metadata/liquidity/behavior | P1 |
| Strategy kill switch | Stop one degraded challenger without stopping all research | P1 |
| Consecutive-loss review gate | Detect gross model mismatch; not an automatic optimizer trigger | P1 after valid sim |
| Abnormal spread/volatility halt | Avoid discontinuous conditions outside calibration | P1, data-derived limits |
| Fill/slippage anomaly halt | Stop when forward execution differs materially from model | P1 |
| Repeated rejection/rate limit | Detect metadata/order lifecycle failure | P1 |
| Maximum order rate | Prevent loops and fee churn | P1 |
| Rolling multi-day loss budget | Bound sustained degradation beyond UTC-day resets | P2 after evidence |
| Maximum exposure duration watchdog | Detect stuck position/order lifecycle | P1 |

Do not add arbitrary numerical values. Derive latency, spread, volatility, slippage and rejection limits
from accepted capture/PAPER distributions, then choose conservative percentiles with explicit caps.

# 15. Reliability Audit

| Failure | Detection | Response | Auto-recovery? |
|---|---|---|---|
| Dashboard process crash | Port/API and process ownership | Run project launcher once | Yes, if no ambiguous position/state |
| Machine restart | User Startup launcher | Start dashboard/watchdog | Yes; persisted position requires manual flatten |
| Internet/Gate REST loss | request failure/stale age | Pause entries | Yes after fresh validated snapshot |
| WS disconnect | connection event/stale timer | reconnect, new snapshot | Yes for capture; dataset marks reconnect/degradation |
| Sequence gap | `U/u` continuity | invalidate book, resnapshot | Yes for research feed; never trade until synchronized |
| Crossed/invalid book | book invariant | invalidate symbol | Auto-resnapshot; no auto-trade until valid |
| Corrupt paper state | schema/finite/range validation | `STATE_INVALID`, halt | No |
| Crash between fill and checkpoint | journal/state mismatch | currently not fully detected | No; add startup reconciliation |
| Disk full/growth | currently missing | stop capture safely, pause research | No trading auto-recovery until space and integrity verified |
| Clock issue | currently missing | reject latency/session, pause research | No until clock is healthy |
| Duplicate process/port | listener count/ownership | report conflict | Do not kill unknown process automatically |
| Bad configuration | startup validation | fail closed | No |
| Dependency failure | import/startup/check suite | fail closed | Reinstall pinned environment explicitly |
| Partial state write | temp + `fsync` + replace | retain prior state or halt invalid | Usually yes if prior state reconciles |
| Browser failure | backend health independent | no trading action | Browser may reopen; engine continues |
| TradingView failure | isolated status/circuit breaker | observer-only restart | Yes; never touch trading |
| Capture corruption | hash-chain replay | reject dataset | No mutation; start a new session |

P0 operational additions:

- capture status/heartbeat and disk-growth forecast;
- one-hour, six-hour and 24-hour fault-injection acceptance tests;
- clock-offset check;
- rotation/retention for paper events, health logs and raw sessions;
- startup reconciliation between trade journal, checkpoint and open position;
- an explicit project stop/graceful-restart script.

# 16. Security Audit

Current strengths:

- dashboard binds only to `127.0.0.1`;
- POST controls require exact local Host and Origin;
- CSP, no-sniff, frame denial and no-store headers are set;
- no private Gate credentials are needed or used;
- `.env*`, data, logs, databases and vendor checkout are ignored;
- subprocess calls use argument arrays and `shell=False` in Python;
- TradingView CDP is forced to loopback, output is bounded and raw failures are not logged;
- TradingView cannot affect signal, risk, sizing, position or execution;
- capture uses only public Gate endpoints and cannot submit orders;
- dependencies are pinned.

Remaining risks:

- localhost controls have no user authentication; same-origin/Host protection is acceptable for the
  current single-user loopback scope but must not be exposed to LAN/proxy/container ports;
- local files inherit user permissions; future credentials require Windows user-only ACLs or a secret
  manager, not browser storage or source files;
- CDP port 9222 is powerful and must remain loopback-only;
- pinned upstream TradingView dependencies have advisories and need reviewed upgrades;
- capture payloads are untrusted external JSON; size/schema limits and canonical serialization must
  stay enforced;
- a future live design needs a separate process/config/credential boundary, trade-only permission,
  no withdrawal permission, explicit environment/confirmation and private-state reconciliation.

No private Gate credential or real-order code should be added in Phase 1.

# 17. External Repository Recommendations

## USE NOW

| Repository/library | Purpose / integration | Benefit | Cost / risk | Decision |
|---|---|---|---|---|
| [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) `1.231.0` | Canonical strategy, portfolio, risk and simulation engine | Reuses tested event/risk/accounting primitives | Gate has no official direct adapter; pinned version emits a pandas warning | Keep; do not replace. Official integrations currently do not list Gate. |
| [python-websockets/websockets](https://github.com/python-websockets/websockets) `17.1` | Public Gate capture transport | Maintained asyncio client, TLS, keepalive, bounded queues and reconnect support | One small runtime dependency and API upgrades to track | Integrated now for capture only. Official client supports automatic reconnect/backpressure. |
| Python stdlib `sqlite3` | Future experiment/champion registry | Serverless, transactional, already installed | Requires a small schema and backup discipline | Use when the first complete replay report exists; no framework. |

## USE LATER

| Repository/library | Purpose / integration | Benefit | Cost / risk | Decision |
|---|---|---|---|---|
| [optuna/optuna](https://github.com/optuna/optuna) | Constrained multi-objective challenger search | Good pruning/samplers and SQLite storage | Makes overfitting faster if introduced early | Add only after replay/holdout/walk-forward gates pass. |
| Apache Arrow/Parquet (`pyarrow`, already transitive) | Derived research tables from accepted raw sessions | Columnar analytics and compact storage | Schema/version/compaction work; raw must remain canonical | Use after capture acceptance, never instead of raw. |
| [HypothesisWorks/hypothesis](https://github.com/HypothesisWorks/hypothesis) | Property tests for book/order/state invariants | Finds sequence/rounding edge cases | New dev dependency and test design effort | Add when local book and simulator invariants grow. |
| Hummingbot strategy/controller source | Micro-maker/order-lifecycle reference | Crypto-specific patterns | Different engine/semantics; porting can add complexity | Study later; never run as a second account/order authority. |

## DO NOT USE

| Repository/library | Reason |
|---|---|
| MLflow | Operational burden exceeds value; immutable manifests plus SQLite are sufficient. |
| Backtrader/vectorbt as a second engine | Creates divergent semantics from Nautilus and duplicate strategy code. |
| Kafka/Redis/RabbitMQ | No measured need for distributed messaging on one laptop. |
| Kubernetes/Docker swarm/cloud microservices | Solves deployment scale the project does not have. |
| LLM trading agents or trading-assistant plugins | Non-deterministic, high-latency and inappropriate for the hot path. |
| Genetic/reinforcement-learning strategy mutation | Severe search/validation burden before basic data validity exists. |
| A paid historical-data feed now | First prove the free direct Gate capture and quantify missing coverage; reconsider only if direct collection cannot provide enough history. |

# 18. Architecture Recommendation

```text
                         RESEARCH / DATA PLANE

 Gate WS public ──> bounded receiver ──> raw hash-chain recorder
       |                     |                    |
 Gate REST snapshot ─────────+                    v
                                          immutable sessions
                                                   |
                                                   v
                                    integrity + local-book replay
                                                   |
                                     +-------------+-------------+
                                     |                           |
                                     v                           v
                              derived Parquet             dataset registry
                                     |                         SQLite
                                     v
                           same-code strategy replay
                                     |
                                     v
                         deterministic execution simulator
                                     |
                                     v
                       report → holdout/walk-forward → challenger

                         PAPER TRADING HOT PATH

 accepted synchronized book/trades
                 |
                 v
 Nautilus DataEngine → Strategy → RiskEngine → PAPER ExecutionModel
                 |                                  |
                 +──────── authoritative state ─────+
                                    |
                           journal + checkpoint
                                    |
                          localhost read/control API
                                    |
                               dashboard UI

                         OUTSIDE HOT PATH

 TradingView observer     optimizer     notebooks     heavy reports
 dashboard rendering      experiment registry        LLM development help
```

Keep one execution authority: NautilusTrader. The capture/research plane may be a separate process
because disk/network failures must not block PAPER risk/position handling. Communication can remain
files plus a small status document at current scale; no broker is justified.

# 19. Prioritized Roadmap

## P0 — immediately

### P0.1 Reconstruct and validate the local Gate book

**Status: implemented in `dd23d03`; long-run validation remains part of P0.2.**

- **WHY:** Raw deltas are not usable research state until snapshots and updates produce a verified,
  non-crossed depth book.
- **DEPENDENCY:** Implemented capture/sequence foundation.
- **ACCEPTANCE CRITERIA:** official snapshot/delta procedure; absolute-size replacement/zero deletion;
  per-symbol synchronization state; deterministic derived best bid/ask/depth digest; gap and crossed
  book fault tests; no trading input while unsynchronized.
- **EXPECTED VALUE:** Converts raw transport into trustworthy microstructure data.
- **COMPLEXITY:** Medium.

### P0.2 Capture acceptance and retention

- **WHY:** Ten seconds does not prove a laptop collector can create research-grade sessions.
- **DEPENDENCY:** P0.1 and disk/clock health checks.
- **ACCEPTANCE CRITERIA:** one-hour, six-hour and 24-hour runs; zero dropped events; every gap either
  recovered and segmented or dataset rejected; bounded disk estimate; graceful interruption manifest;
  verified replay hash; documented storage rate per symbol.
- **EXPECTED VALUE:** Produces the first valid corpus and exposes operational failures early.
- **COMPLEXITY:** Medium.

### P0.3 Same-code deterministic replay

- **WHY:** Strategy changes cannot be compared if research and PAPER paths differ.
- **DEPENDENCY:** accepted book events from P0.1/P0.2.
- **ACCEPTANCE CRITERIA:** captured events convert to Nautilus data; current baseline runs unchanged;
  two identical runs generate identical decision/order/fill/trade hashes; causality test prevents future
  data; dataset/code/config/seed recorded.
- **EXPECTED VALUE:** Makes strategy evaluation repeatable.
- **COMPLEXITY:** Medium-high.

### P0.4 Minimum realistic taker simulator

- **WHY:** Current fixed top size and inferred metadata dominate PnL.
- **DEPENDENCY:** P0.3 plus Gate instrument metadata.
- **ACCEPTANCE CRITERIA:** depth sweep, partial/unfilled quantity, Gate tick/step/minimum/multiplier,
  fees, funding/mark, measured latency and stale-book rejection; unit golden cases; cost breakdown.
- **EXPECTED VALUE:** Makes short-horizon net expectancy interpretable.
- **COMPLEXITY:** High.

## P1 — next

### P1.1 Research report and registry

- **WHY:** PnL alone hides risk, costs, overfitting and regime dependence.
- **DEPENDENCY:** P0 replay/simulator.
- **ACCEPTANCE CRITERIA:** mandatory metrics/segments, config and digest traceability, SQLite experiment
  states, immutable artifacts and comparison command.
- **EXPECTED VALUE:** Enables objective rejection and comparison.
- **COMPLEXITY:** Medium.

### P1.2 Validate REST momentum as baseline

- **WHY:** It is useful only as a benchmark against which challengers must add value.
- **DEPENDENCY:** P1.1.
- **ACCEPTANCE CRITERIA:** chronological holdout, walk-forward, cost/latency sensitivity, MFE/MAE and
  no promotion claim.
- **EXPECTED VALUE:** Quantifies whether any current signal remains after realistic costs.
- **COMPLEXITY:** Medium.

### P1.3 Order-flow momentum and mean-reversion challengers

- **WHY:** Independent economic hypotheses are higher value than more tuning of one heuristic.
- **DEPENDENCY:** valid derived features and P1.1.
- **ACCEPTANCE CRITERIA:** pre-registered features/parameters, same datasets/costs, independent reports,
  untouched holdout and named rejection result.
- **EXPECTED VALUE:** First credible search for alpha.
- **COMPLEXITY:** Medium-high.

### P1.4 Journal/checkpoint reconciliation and risk anomaly gates

- **WHY:** Unattended operation cannot tolerate ambiguous fills/state.
- **DEPENDENCY:** stable event IDs/order lifecycle.
- **ACCEPTANCE CRITERIA:** crash injection at each write boundary; deterministic reconciliation or
  `HALTED`; no restart bypass; order-rate/rejection/slippage/stuck-exposure gates.
- **EXPECTED VALUE:** Capital protection and operational reliability.
- **COMPLEXITY:** Medium-high.

## P2 — after foundation

- Champion/challenger shadow process and promotion registry.
- Optuna constrained multi-objective studies on small parameter spaces.
- Conservative maker/post-only queue and cancel model, then micro-maker feasibility study.
- Dynamic universe refresh using realized volatility, depth, trade rate, stability and impact.
- Full backend Playwright critical path, log/capture rotation, disk/clock/capture watchdog checks.

Each P2 item depends on P0/P1 artifacts and must have a deletion/rejection path if it adds no measured
value.

## P3 — later

- Future Gate TestNet private-state reconciliation.
- Small-capital live design review only after Section 21 is satisfied.
- VPS migration if laptop uptime demonstrably blocks validation.
- Multi-exchange data/execution only after Gate results and operations are valid.

# 20. Exact Next 10 Tasks

1. **Add capture status, free-disk forecast, UTC clock-offset check, graceful incomplete manifests and
   a one-hour acceptance runner; measure bytes/events per symbol.**
2. Run and verify six-hour then 24-hour public capture sessions across a controlled 5–8 symbol
   universe; reject or segment any degraded interval.
3. Convert accepted book/trade/ticker records into Nautilus data objects with original exchange and
   local receive timestamps.
4. Replay the unchanged REST momentum baseline through that event stream and require identical
   decision/order/trade hashes on repeated runs.
5. Load Gate contract metadata and replace inferred precision, fixed quantity and synthetic size
   assumptions.
6. Implement deterministic L2 taker depth-sweep fills with partial/unfilled quantity, measured latency,
   fees, funding and mark-price accounting.
7. Build the mandatory research report and lightweight SQLite experiment/strategy registry.
8. Run baseline chronological holdout, purged walk-forward, cost/latency sensitivity and block-bootstrap
   analysis; record a `FAILED` or benchmark result, not a promotion.
9. Implement one order-flow momentum and one liquidity-normalized mean-reversion challenger; only then
    begin shadow champion/challenger comparison.
10. Add drift monitoring and a human-reviewed promotion registry only after challengers pass the fixed
    validation report.

Task 1 is the highest-value next action because the local book now replays deterministically, but a
ten-second session does not establish a trustworthy research corpus or unattended collector.

# 21. Promotion Criteria Toward Live Trading

Phase 2/TestNet should not be considered until all of the following are evidenced:

## Data and replay

- At least 30 days of accepted data, preferably enough to cover quiet, volatile and adverse regimes.
- No accepted dataset contains dropped events or unrecovered sequence gaps.
- Reconnect/resnapshot, stale stream, crossed book, disk full and clock-fault tests pass.
- Same dataset/code/config/seed produces identical normalized events and trade journals.

## Execution validity

- Gate metadata, tick/step/minimum/multiplier, fees, funding, mark/index and 1x margin are modeled.
- Taker fills sweep observed depth; maker results use conservative queue/cancel assumptions.
- Latency inputs come from measured distributions and are stress-tested worse than observed.
- Forward PAPER slippage/fill/rejection behavior is close enough to simulator predictions under a
  documented calibration tolerance.

## Statistical evidence

- Positive net expectancy after all costs on untouched holdout and rolling walk-forward windows.
- Bootstrap lower confidence bound for expectancy is positive or otherwise demonstrates an equally
  conservative effect-size gate.
- Profit factor is materially above 1, not marginally above 1 through noise.
- At least 1,000 out-of-sample/shadow trades where opportunity frequency permits, across at least five
  symbols, 30+ days and multiple regimes.
- No single symbol, day, side or regime supplies an unacceptable share of profit.
- Drawdown, tail loss and consecutive loss behavior fit the approved risk budget under stress.
- Parameter neighborhoods are stable; the result is not the single best trial among many.

## Reliability and security

- 30 consecutive days of unattended PAPER stability with tested restart/recovery and no ambiguous
  position/order state.
- Journal/checkpoint reconciliation and all safety halts are fault-injection tested.
- Dashboard, TradingView, research and optimizer failures cannot affect execution integrity.
- No live-order path exists in the Phase 1 build.
- A future TestNet build has separate credentials/config, trade-only permission, no withdrawal,
  private-state reconciliation, explicit enablement and rollback.

Only after a successful TestNet phase should a separately reviewed, very small-capital live phase be
discussed. There is no evidence today that justifies live trading.

## Direct answer: the three major improvements

If development capacity permits only three major improvements before deciding whether real alpha
exists, do these in order:

1. **Finish trustworthy Gate L2/trade data:** deterministic local-book reconstruction plus long-run,
   integrity-gated immutable capture. Without this, the intended horizon is invisible.
2. **Replay the exact same strategy logic deterministically:** same dataset, code, config and seed must
   produce the same decisions and journal. Without this, comparisons and optimization are not valid.
3. **Calibrate a minimal realistic execution simulator:** Gate metadata, depth sweep/partial fills,
   measured latency, fees, funding, mark price and conservative maker behavior. Without this, apparent
   alpha may be only fill-model profit.

Everything else—including Optuna, new UI, more indicators and live trading—is lower value until those
three are complete.
