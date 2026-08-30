# Risk Management

## Principle

The bot is allowed to miss opportunities. It is not allowed to continue operating on corrupted assumptions.

## Phase 1 Defaults

- Paper capital: USD 300
- Leverage: 1x
- DCA: disabled
- Martingale: forbidden
- Real orders: impossible
- Position sizing: conservative and capped

The initial REST tournament monitors the eight screened Gate pairs in one shared paper account. It
uses 30 USDT notional (10% of paper capital) and enforces one simultaneous position across the whole
portfolio, not one position per symbol. The single execution slot goes only to the highest-confidence
signal that passes the liquidity, spread, 12 bp minimum net edge, 0.60 minimum confidence, five-tick
persistence and two-stage three-minute regime gates. Stops remain 35 bp, take-profit 55 bp, time exit
300 seconds, cooldown 60 seconds, daily
loss halt 6 USDT and drawdown halt 3%. Values live in `config/paper.toml`; validation rejects capital
allocation above 25%, loss limits above 5%, leverage above 1x, or an entry hurdle below modeled fees
and slippage.

## Required Risk Controls

Define configurable hard limits for:

- max position notional per symbol;
- max total notional;
- max simultaneous positions;
- max loss per trade;
- max daily paper loss;
- max drawdown;
- max consecutive losses;
- max order rate;
- max acceptable spread;
- max acceptable market-data age;
- max acceptable observed latency;
- max simulated slippage;
- max strategy allocation.

Initial numerical values should be conservative and must remain configurable. Do not invent aggressive defaults.

## Trading States

Use explicit states:

- `ACTIVE`
- `PAUSED`
- `REDUCING`
- `HALTED`

`HALTED` must require explicit recovery/restart logic.

## Automatic Halt Conditions

Halt new entries when:

- stale market data;
- WebSocket disconnected beyond tolerance;
- unrecoverable order-book gap;
- execution state cannot be reconciled;
- persistence fails critically;
- daily loss threshold reached;
- drawdown threshold reached;
- abnormal slippage/fill behavior detected;
- strategy produces invalid numeric state;
- system clock/data timing is unsafe.

## Resume

Auto-reconnect is allowed.

Transient public market-data staleness and request failures are explicitly recoverable. They pause
new entries while the feed is unhealthy and automatically restore entry eligibility only after a
new snapshot passes validation and freshness checks.

Auto-resume trading after any other critical state failure is not allowed. Execution, persistence,
risk-limit and invalid-state failures remain `HALTED` until explicit recovery proves state integrity.

## Nautilus Risk Engine

Use NautilusTrader's RiskEngine checks rather than bypassing them. Add application-level rules around it where necessary.

Reference:
https://nautilustrader.io/docs/latest/concepts/execution/
