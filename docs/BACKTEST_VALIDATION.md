# Backtest and Validation

## Objective

Reject strategies that look profitable only because simulation is unrealistic.

## Validation Ladder

1. deterministic unit tests;
2. historical replay;
3. cost-aware backtest;
4. sensitivity testing;
5. out-of-sample test;
6. walk-forward test;
7. live market-data paper execution;
8. long-duration stability run;
9. future TestNet phase;
10. future small-capital live validation.

## Execution Costs

Every reported result must include:

- maker/taker fees;
- spread;
- slippage;
- funding where relevant;
- partial-fill assumptions;
- order latency;
- cancel latency;
- fill probability / queue assumptions where relevant.

## Metrics

At minimum report:

- net PnL;
- return;
- trade count;
- win rate;
- average win;
- average loss;
- expectancy/trade;
- profit factor;
- max drawdown;
- Sharpe or another risk-adjusted metric where statistically useful;
- fees as % of gross PnL;
- slippage cost;
- average holding time;
- MFE;
- MAE;
- results by symbol;
- results by strategy;
- results by market regime.

## Acceptance Philosophy

No single magic threshold proves a strategy is safe.

Minimum qualitative requirements before any future live phase:

- positive expectancy after all modeled costs;
- profit factor materially above 1, not marginally above 1 due to noise;
- no dependence on one symbol/day;
- acceptable drawdown;
- out-of-sample profitability;
- walk-forward robustness;
- execution assumptions reasonably close to observed paper behavior;
- thousands of observations/signals where feasible.

## Anti-Overfit Rules

- Do not optimize dozens of parameters on a small dataset.
- Preserve untouched holdout periods.
- Prefer robust parameter regions over one best point.
- Record every experiment configuration.
- Compare against simple baselines.

The implemented research split is strictly chronological: 50% selection, 20% validation, 15% test,
and 15% sealed final holdout, with 900-second purge and embargo windows by default. Random splits are
not supported. The final holdout seal binds dataset, strategy hash, and parameter hash before it is
read. Parameter-neighborhood searches are withheld until a strategy has positive gross and net edge;
this prevents tuning a family that has not demonstrated a viable premise.

Every experiment identity includes dataset and source-prefix hashes, Git commit, research-config
hash, strategy and parameter hashes, execution-profile version, and deterministic seed. Candidate
and counterfactual ledgers are hash-chained. A successful software test proves reproducibility and
invariants, not profitability.

## XAU KAMA Control Comparison

The raw and filtered KAMA20 shadows share the same observations, notional, and execution-profile
assumptions. Reports must show sample count before PnL and must return `INSUFFICIENT_EVIDENCE` until
each family has at least the configured minimum number of closed shadow trades.

Execution sensitivity uses three labeled profiles:

- `IDEALIZED`: round-trip fee only;
- `BASELINE`: fee, observed spread, fixed per-side slippage buffer, and funding buffer;
- `STRESSED`: baseline costs multiplied by the configured stress factor.

The new sensitivity profiles can model latency, partial-fill probability, impact, and adverse
selection, but those values are conservative assumptions rather than calibrated measurements; queue
position remains unavailable. Therefore, the result is a shadow counterfactual, not executable-fill proof. Filter complexity is
supported only when it improves net PnL without worsening drawdown after the minimum sample. Even then,
promotion remains manual and requires chronological replay, holdout/walk-forward evidence, and extended
PAPER validation.
