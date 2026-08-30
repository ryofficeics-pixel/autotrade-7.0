# Strategy Lab

## Purpose

Create a controlled tournament of strategies rather than betting the project on one idea.

## Engine Ownership

NautilusTrader owns strategy execution.

Hummingbot may be used as:

- reference for market-making logic;
- reference for controller/executor patterns;
- source of ideas for order lifecycle management;
- comparison implementation during research.

Do not directly run a Hummingbot bot against the same paper/live account while Nautilus is the execution authority.

## Challenger Strategies

### Current Pilot: REST Momentum Tournament

The armed challenger observes Gate public best bid/ask every five seconds for every pair in the
eight-symbol screened universe. Each pair warms and evaluates the same short-window momentum logic
independently. A pair becomes eligible only when its move exceeds round-trip taker fees, pessimistic
spread/slippage and a safety buffer by at least 12 bps net, reaches 0.60 confidence, persists across
five quote moves, and agrees with both the pre-signal and complete three-minute regime. Among eligible
pairs, one coordinator selects the highest net-edge confidence and allows exactly one shared-account
position. It uses 30 USDT notional, fixed stop/take-profit, a 300-second time exit and cooldown.

Confidence is a bounded comparison score derived from expected net edge relative to the configured
entry hurdle. It must pass its own minimum before ranking already-valid signals; it does not bypass
any entry or risk gate.

This is an operational paper challenger, not validated alpha. It exists to collect execution and
forward-performance evidence while L2/trade capture is built. It must not loosen its cost hurdle merely
to manufacture trades.

### A. Order-Flow Momentum

Potential inputs:
- L2 imbalance;
- microprice displacement;
- aggressive trade imbalance;
- short-horizon momentum;
- spread;
- volatility;
- book replenishment/depletion.

### B. Mean Reversion

Potential inputs:
- short-term price displacement;
- normalized move relative to recent volatility;
- book asymmetry reversal;
- trade-flow exhaustion;
- spread/liquidity regime.

### C. Micro Market Making

Potential inputs:
- spread capture opportunity;
- queue/fill probability;
- inventory risk;
- adverse-selection risk;
- book stability;
- volatility regime.

Use Hummingbot's modern strategy/controller concepts as reference, but port only the minimal logic actually needed.

Reference:
https://hummingbot.org/strategies/

## Strategy Contract

Each strategy must expose or log:

- signal timestamp;
- symbol;
- direction;
- confidence/opportunity score;
- expected gross edge;
- expected costs;
- expected net edge;
- intended holding horizon;
- intended order style;
- invalidation condition.

## Champion / Challenger

All strategies begin as challengers.

Promotion requires:
- sufficient sample size;
- positive net expectancy;
- acceptable drawdown;
- out-of-sample performance;
- walk-forward robustness;
- stable paper execution.

The selector may later choose strategies by market regime, but Phase 1 should first measure each strategy independently.

## No LLM in Hot Path

LLMs may help development and post-trade analysis.

They must not make latency-sensitive buy/sell decisions.
