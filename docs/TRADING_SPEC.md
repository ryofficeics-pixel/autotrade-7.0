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
