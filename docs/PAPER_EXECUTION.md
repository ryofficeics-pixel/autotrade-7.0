# Paper Execution Simulator

## TradingView Boundary

TradingView does not participate in Phase 1 entry, exit, risk, sizing, fill, PnL, margin or recovery
logic. Configuration fields for a possible confirmation mode/weight are inert execution metadata.
This preserves Nautilus as the single execution authority and keeps TradingView failure outside the
paper engine's fail-closed data/risk state.

## Current Pilot

The REST-momentum tournament currently monitors the eight screened Gate pairs in one NautilusTrader
backtest account. A portfolio coordinator permits only the highest-confidence eligible signal to use
the single execution slot. It models bid/ask spread, 5 bps taker fees per side, configurable adverse
slippage, 1x margin, position accounting, stop/time exits and rejected orders. Every signal and fill is appended
to `logs/paper-events.jsonl`. New events carry a UTC observation timestamp; completed-trade rows also
carry open/close prices, quantity, side, net realized PnL, PnL percentage, combined fees and exit reason
for the dashboard's rolling 48-hour audit view.

When Nautilus splits a simulated market order across multiple fills, the audit record aggregates the
full entry and exit quantities, quantity-weighted prices and all fill fees before calculating the
dashboard PnL percentage.

Closed equity, fees, trade count and any open paper position are atomically checkpointed to
`logs/paper-state.json`, including the actual position symbol. After an unclean restart with a persisted position, entries fail closed until
the user invokes `FLATTEN PAPER POSITIONS` against a fresh quote and then explicitly resumes.

Checkpoint schema version 2 also stores the UTC risk window, day-start equity/trades, persistent
all-time peak, risk halt and halt reason. Writes reject non-finite state, flush and `fsync` before an
atomic replace. Daily PnL and trade count therefore survive restart without being confused with
lifetime performance.

Partial fills, measured acknowledgement/cancel latency, funding debits and queue-aware maker fills
remain pending L2/trade capture. Until those exist, results are research evidence only and must not be
treated as live-ready performance.

## Purpose

Make paper trading pessimistic enough that live trading is not a shock.

## Required Behaviors

Simulate:

- maker/taker fees;
- bid/ask spread;
- slippage;
- order acknowledgement latency;
- cancel latency;
- partial fills;
- rejected orders;
- queue uncertainty for resting limits;
- stale-order risk;
- funding;
- mark-price behavior;
- position accounting.

## Fill Rules

A limit order should not be considered filled simply because last price touched its level.

Where L2/trade data permits, use observed traded liquidity and conservative queue assumptions.

## Latency

Maintain configurable latency models using measured local observations.

At minimum distinguish:

- market-data latency;
- signal computation delay;
- submit delay;
- acknowledgement delay;
- cancel delay.

## Audit

Store enough event detail to replay why every simulated fill occurred.
