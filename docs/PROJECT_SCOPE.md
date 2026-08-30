# Project Scope

## Product

A personal automated crypto perpetual-futures scalping application.

It gives NautilusTrader a custom operational dashboard while keeping NautilusTrader as the canonical trading engine.

Hummingbot is not a second master engine. Its strategy/controller implementations may be studied, adapted or ported into the Nautilus strategy layer when useful.

## Phase 1 Objective

Prove that the system can:

- ingest real Gate.io market data reliably;
- record usable microstructure data;
- run multiple paper strategies;
- simulate realistic order execution;
- measure net expectancy;
- recover from failures;
- present reliable operational state in a dashboard;
- remain stable during long laptop sessions.

Profit is a research outcome, not a guaranteed software feature.

## In Scope

- Gate.io USDT perpetual futures
- real WebSocket market data
- dynamic pair ranking
- L2/order book features
- trade flow features
- paper portfolio of USD 300
- 1x leverage
- strategy tournament
- realistic paper execution
- backtest/replay
- walk-forward validation
- local dashboard
- health monitoring
- Windows startup/restart scripts
- Playwright UI audit
- local persistence

## Out of Scope for Phase 1

- real-money order submission
- VPS
- multi-exchange execution
- sub-millisecond colocated HFT
- AI/LLM deciding trades in the hot path
- reinforcement-learning production strategy
- portfolio leverage above 1x
- mobile app
- social/copy trading
- withdrawal permission
- automated API-key creation
