# Offline Research, Replay, and Promotion Engine

## Purpose and safety boundary

The research engine turns the current append-only XAU shadow evidence into reproducible offline
experiments. It cannot submit an order, arm a runtime strategy, clear a risk halt, or change the
active market scope. Phase 1 remains PAPER-only at 1x. LIVE has no lifecycle transition.

The runtime and research processes are deliberately separate:

```text
Gate public data -> PAPER runtime -> hash-chained AMA/Entry V3 evidence
                                      |
                                      v
                              immutable dataset freeze
                                      |
                       same-code causal strategy replay
                                      |
              candidate ledger + counterfactual ledger + costs
                                      |
                chronological validation and sealed holdout
                                      |
                    manual PAPER lifecycle review only
```

`autotrade/research.py` contains pure data, strategy, replay, validation, integrity, and lifecycle
primitives. `autotrade/research_cli.py` orchestrates them. Neither module imports the runtime trader
or an execution adapter.

## Dataset lifecycle

`full` selects the newest `ama-control-v2.jsonl`, verifies its complete hash chain, and snapshots the
complete byte prefix observed at the start of the run. A later runtime append cannot enter the frozen
dataset. The dataset identity covers the canonical manifest inputs and the source-prefix SHA-256.

Each `research/datasets/<dataset_id>/` contains:

- `events.jsonl`: normalized, causal observations in source order;
- `manifest.json`: row count, sample window, timestamp policy, source-prefix boundary, Git and
  capture-config identity, limitations, and content hashes;
- `manifest.sha256`: independent file hash.

Creation is fail-closed: duplicate/non-monotonic sequence, a broken source chain, malformed values,
or an existing conflicting identity aborts the freeze. Files are created once and made read-only.
`verify-dataset` re-hashes every artifact before replay.

The current AMA evidence has local REST receive time but not exchange event time, BBO depth, queue,
or executable fills. The manifest records those limitations; the engine never relabels them as
measured execution data.

## Strategy API and built-in challengers

`StrategyProtocol` requires immutable identity fields (`strategy_id`, version, strategy hash,
parameter hash) and a causal `evaluate(history, index, profile)` method. The replay passes only the
current and earlier observations. A candidate that claims a future timestamp is rejected.

Built-ins are:

- `XAU_KAMA20_RAW_REPLAY_V1`: raw price/KAMA20 cross with KAMA slope;
- `XAU_KAMA20_FILTERED_REPLAY_V1`: the same family with regime, persistence, hysteresis, slope,
  reference-quality, volatility, and realizable-edge gates;
- `XAU_FAIR_VALUE_V1`: XAU deviation from normalized XAUT/PAXG reference value;
- `XAU_COMBINED_V1`: agreement between filtered KAMA direction and fair-value direction.

Missing or stale reference inputs reject the reference strategies. REST Momentum V2 cannot be
reconstructed from an XAU-only timeline. Entry V3 cannot be reconstructed without its L2, trade,
exchange-timestamp, and synchronized-book evidence. Both are reported as `NOT_RECONSTRUCTABLE`;
the pipeline does not invent substitute inputs.

## Candidate and counterfactual evidence

Every observation produces one candidate identity, not one record per horizon. Candidate records
include the decision, direction, signal strength (not a calibrated probability), regime, gate-by-gate
results, first binding rejection, gross move, decomposed cost, and net realizable edge.

Resolved counterfactuals report 10, 30, 60, 180, 300, and 900 second returns, MFE, MAE, time to MFE,
time to MAE, realized modeled outcome, and an explicitly diagnostic oracle-best exit. The funnel
reports generated, accepted, rejected, binding gates, acceptance rate, and per-gate MFE/MAE. The
no-trade report separates avoided losses from profitable candidates missed and reports time spent in
no-trade state. Overlapping observations are not independent trades and are labeled accordingly.

Both ledgers are independently hash-chained and verified on read. `research/registry.jsonl` and
`research/lifecycle.jsonl` use append-only hash chains and an exclusive writer lock.

## Execution profiles

`config/research.toml` versions four profiles:

- `IDEALIZED`: fee only;
- `BASELINE`: fee, observed proxy spread, slippage, and funding buffers;
- `STRESSED`: larger costs plus latency, adverse selection, impact, and reduced/partial fills;
- `REALISTIC`: a separately labeled simulated sensitivity profile.

Cost output is decomposed into gross move, spread, fees, slippage, latency, adverse selection,
funding, impact, missed partial-fill effect, total cost, and net edge. Candidate acceptance requires
positive modeled realizable edge. Because latency, fill probability, adverse selection, and impact
are assumptions rather than calibrated Gate measurements, every current replay is blocked by
`EXECUTION_MODEL_NOT_VALIDATED`. Fair-value and combined results are also blocked by
`REFERENCE_FRESHNESS_UNAVAILABLE`.

## Chronological validation

The default stages are `SELECTION` 50%, `VALIDATION` 20%, `TEST` 15%, and a sealed
`FINAL_HOLDOUT` 15%. Boundaries are chronological. A 900-second purge and 900-second embargo prevent
forward horizons from crossing a stage boundary. Random splitting is not available.

The final holdout seal binds dataset, strategy hash, and parameter hash before evaluation. A
conflicting reuse fails. Parameter-neighborhood optimization remains withheld until a challenger
first demonstrates positive gross and net evidence; a losing family is not tuned until it wins.
Signal strengths remain `UNCALIBRATED` until sufficient out-of-sample accepted candidates exist.

## Promotion and retirement

Lifecycle states are `EXPERIMENTAL`, `OBSERVING`, `VALIDATED`, `PAPER_ELIGIBLE`, `PAPER_ACTIVE`,
`QUARANTINED`, and `RETIRED`. Initial policy records the failed REST baseline as retired, raw KAMA as
quarantined, filtered KAMA as observing, and other challengers as experimental.

Promotion requires all configured sample, expectancy, profit-factor, drawdown, temporal-window,
regime, test, sealed-holdout, stressed-execution, parameter-stability, data-integrity, and execution-
model conditions. Passing metrics would create evidence only. It would not activate trading.

The only operator command moves a previously `VALIDATED` strategy to `PAPER_ELIGIBLE`, then requires
a second explicit transition to `PAPER_ACTIVE`. Each transition records operator, reason, time, Git,
and evidence identity. There is no automatic transition and no LIVE target.

## Commands

Run the complete offline pipeline:

```bat
.venv\Scripts\python.exe -m autotrade.research_cli --project-root . full
```

Freeze or verify without replay:

```bat
.venv\Scripts\python.exe -m autotrade.research_cli --project-root . freeze
.venv\Scripts\python.exe -m autotrade.research_cli --project-root . verify-dataset research\datasets\DATASET_ID
```

Manual lifecycle transitions require an operator identity and reason. `PAPER_ELIGIBLE` also requires
a promotion result whose `promotion_eligible` field is true:

```bat
.venv\Scripts\python.exe -m autotrade.research_cli --project-root . approve-paper STRATEGY VERSION PAPER_ELIGIBLE --operator NAME --reason "reviewed evidence" --promotion-evidence PATH
```

## Outputs, dashboard, and retention

Canonical evidence is under ignored local `research/`: frozen datasets, experiment results, sealed
holdouts, ledgers, registry, and lifecycle. These artifacts are machine-local and can be large.
There is no automatic deletion of canonical evidence.

`reports/research-latest.json` and `.md` are regenerable views. The dashboard reads the JSON with a
size limit and mtime cache and exposes it under `/api/state.research`. It displays identity, Strategy
Lab, candidate funnel, no-trade value, execution edge, and lifecycle. A missing or malformed report
is a loud `UNAVAILABLE` or `ERROR` state. The browser has no research mutation or promotion control.

## Current evidence interpretation

A positive no-trade filter benefit can coexist with a losing accepted strategy: rejecting many bad
candidates is useful, but it does not prove the rare accepted candidates are profitable. Gross edge
must first be positive and must then survive costs, chronological test, sealed holdout, stress, and
PAPER operation. Recovery flatten PnL is operational evidence, not strategy alpha.
