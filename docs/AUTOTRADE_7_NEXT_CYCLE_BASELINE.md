# AUTOTRADE 7.0 Next-Cycle Baseline

## Cutoff

- Implementation-start cutoff: 2026-09-19 00:57 UTC.
- Starting Git commit: `d227aae`.
- Runtime mode: PAPER, Gate public REST, 1x maximum leverage, flat at discovery.
- Existing user-owned worktree item preserved: `docs/AUDIT_REPORT_2026-09-18_POST_RESTART.md`.

## Evidence at Start

The latest fixed-cutoff operational audit reported a valid ledger and healthy PAPER runtime, but the
normal strategy evidence was economically negative: 315 normal closes, -18.38589949 USDT net,
32.38% win rate, 0.5004 profit factor, and -0.05836793 USDT expectancy per trade. Three recovery
closes contributed +4.68891114 USDT and were correctly excluded from normal strategy evidence. XAU
had no closed trades, so no XAU profitability conclusion was possible.

The baseline execution model observed spread and modeled fees/slippage, but did not model decision,
submit, or fill latency, queue position, funding, market impact, or adverse selection. The V3 entry
path remained shadow-only and execution-disabled.

## Architecture Map

- `PaperTrader` owns the Nautilus simulated account, entry routing, risk, persistence, and recovery.
- `MarketScopeController` is the durable authority for `WIDE_CRYPTO` and `XAU_ONLY` entry scope.
- `XauSignalEngine` supplies the existing executable XAU PAPER decision only when XAU scope is active.
- `GatePoller` already retains `XAU_USDT`, `XAUT_USDT`, and `PAXG_USDT` public tickers in every scope.
- Entry V3 consumes its separate WebSocket capture timeline and cannot submit orders.
- The dashboard is a local read/control plane and is not an execution engine.

## Bottleneck

The next evidence bottleneck was not another executable strategy. It was the absence of an independent,
same-timeline XAU control that could measure raw versus filtered KAMA decisions, rejection outcomes,
reference quality, and modeled costs without influencing PAPER orders.

Continuous observations use the five-second poll. Candidate decisions are paired on state changes or
a 30-second cadence so every defined candidate is attributable without creating an unbounded six-horizon
outcome fan-out on every quote.

## Authorized Change Boundary

This cycle may add an execution-isolated XAU KAMA control, append-only research evidence, derivative
reports, tests, and a read-only dashboard view. It may not enable Entry V3, increase leverage, relax
risk limits, reinterpret recovery PnL as alpha, force trades, or promote any strategy automatically.

## Baseline Decision

Build the research control as a sidecar to `PaperTrader.process`, before any scope-specific entry work,
and expose only its snapshots. Do not connect it to `submit_candidate`, sizing, risk authorization, or
the market-scope execution decision.
