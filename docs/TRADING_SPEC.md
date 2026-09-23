# Trading Specification

## Market

- Venue: Gate.io
- Product: USDT-margined perpetual futures
- Direction: long and short
- Phase 1 capital: USD 300 simulated
- Phase 1 leverage: 1x
- Real orders: disabled

## Trading Objective

Maximize risk-adjusted **net expectancy**, not gross PnL, trade count or daily percentage target.

A candidate trade is valid only if estimated edge exceeds estimated total cost plus a safety margin.

```text
expected_move
- fees
- spread cost
- expected slippage
- expected adverse selection
- funding impact where relevant
- safety buffer
= expected_net_edge
```

Trade only when `expected_net_edge > minimum_required_edge`.

## Pair Selection

Do not simply choose the highest-volatility symbol.

Pipeline:

1. start from supported Gate.io USDT perpetual instruments;
2. remove unavailable/suspended instruments;
3. enforce minimum recent notional turnover;
4. enforce minimum book depth;
5. reject excessive spread;
6. reject stale/inconsistent feeds;
7. rank survivors by short-horizon realized volatility and opportunity score;
8. choose a small active universe.

Initial universe target: 5–10 symbols.

The Phase 1 REST tournament monitors every symbol in the active universe. It ranks only signals that
have already passed the cost, spread, persistence and regime gates, then gives one portfolio-wide
execution slot to the highest-confidence eligible signal. If no signal qualifies, it does not trade.

The current PAPER gate requires at least 12 bps expected net edge and 0.60 confidence. Momentum must
persist for five observations, the pre-signal portion of the regime must already agree with the trade
direction, and the complete regime move must exceed the entry threshold. This rejects a late burst
that merely overwhelms an opposing earlier regime. These are forward-paper defaults, not validated
alpha parameters.

No fixed symbol list is sacred.

## Strategy Evidence Retirement Gate

The active PAPER strategy is retired from new entries after a meaningful losing sample, rather than
being allowed to consume the remaining paper balance indefinitely. The default gate requires at least
100 normal closes over at least 10 UTC trading days, splits those closes into chronological halves,
and fails only when both halves have negative net PnL and profit factor below 0.80. It ignores manual
and outage-held recovery closes.

Passing this gate means only that automatic retirement was not triggered. It is not evidence of alpha,
promotion eligibility, or live readiness. Failure is persisted as `STRATEGY_EVIDENCE_FAILED`, blocks
new entries before candidate selection, and does not force-close an existing position. Replacing the
failed strategy requires cost-aware chronological replay, holdout or walk-forward evidence, a fresh
PAPER run, and manual approval.

## Market Profiles

`WIDE_CRYPTO` is the unchanged cross-sectional scanner and tournament profile. `XAU_ONLY` is a
separate profile for `XAU_USDT` perpetual and does not reuse crypto thresholds blindly. It computes
normalized short-window and trend returns, volatility regime, cost hurdle, confidence, and optional
normalized-return agreement with `XAUT_USDT` and `PAXG_USDT`. Raw token prices are never compared.

The XAU profile may return `LONG`, `SHORT`, or `WAIT`. Stale primary data, abnormal divergence,
excessive spread, insufficient net edge, low confidence, invalid contract rules, or global risk state
must produce `WAIT` or a blocked entry. Confirmation markets are never treated as guaranteed
arbitrage. Initial values in `config/paper.toml` are conservative PAPER defaults, not validated alpha.

Every trade record carries `market_scope`, `market_class`, strategy, entry reason, regime,
confidence, risk profile, mode at entry, confirmation state, and a reserved session label. Analytics
split ALL, CRYPTO, and XAU results; XAU also splits direction and regime. No adaptive process may
combine XAU and crypto evidence into one undifferentiated training set.

## Frequency

Expected operating range:
- approximately 20–30 completed trades/day;
- 0 is valid when no edge exists;
- higher counts are allowed when quality criteria are satisfied.

There is no trade quota.

## Holding Horizon

Target short-duration scalping, but determine exit horizon empirically.

Measure forward behavior after each signal at:
- 100 ms
- 250 ms
- 500 ms
- 1 s
- 2 s
- 5 s
- 10 s
- 30 s
- 60 s

Use MFE, MAE and net-return-after-costs to determine suitable horizons per strategy/regime.

## Strategy Families

Phase 1 challengers:

1. Order-flow / momentum
2. Mean reversion
3. Micro market making / spread capture

No strategy receives production status merely because it backtests positively.

## Execution Bias

Prefer maker/post-only execution when it improves net expectancy and fill probability is acceptable.

Taker orders may be used in the simulator where strategy logic explicitly justifies them.

## Forbidden

- martingale;
- DCA;
- doubling after losses;
- chasing a fixed daily return;
- increasing leverage to rescue a weak strategy;
- forcing trades to reach 20–30/day.

## Paper Run and Accounting Authority

Every fresh experiment has a durable `run_id`; each process has a `session_id`. New events use schema
v3 with monotonic sequence, event identity, signal/order/fill/position identities, UTC timestamps and
financial fields. The atomic checkpoint records its sequence and last event sequence. Startup compares
checkpoint balance, fees, realized PnL, trade count and open-position identity with the current-run
ledger before Nautilus is allowed to accept entries. Decimal tolerance is 0.00000001 USDT.

An ordinary restart is recovery of the same run. A fresh 300 USDT experiment is a separate explicit
operation and may not be used to hide a recovery failure. Legacy rows remain preserved and
unattributed.

## Quote Failure Scope

Raw quotes must satisfy `0 < bid < ask`. Adverse slippage is applied with Decimal arithmetic and then
quantized outward to the Gate contract tick. A one-tick minimum spread is allowed only after raw quote
validation. A remaining symbol error is `QUARANTINED`; other valid symbols continue to monitor and may
compete for the one execution slot.
