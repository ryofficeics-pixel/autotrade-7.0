# AGENTS.md

## Mission

Build the smallest safe implementation that satisfies the specifications in `/docs`.

Do not redesign the architecture without updating the relevant specification first.

## Source of Truth Priority

1. Safety constraints in `RISK_MANAGEMENT.md` and `SECURITY.md`
2. `TRADING_SPEC.md`
3. `ARCHITECTURE.md`
4. `DATA_SPEC.md`
5. `BACKTEST_VALIDATION.md`
6. `DASHBOARD_UI.md`
7. Remaining docs

## Coding Rules

Follow Ponytail-style minimalism:

1. Do not build code that does not need to exist.
2. Prefer Python stdlib and browser/platform-native features.
3. Reuse NautilusTrader capabilities before writing wrappers.
4. Reuse already-installed dependencies before adding new ones.
5. Add a dependency only with a documented reason.
6. Keep abstractions only when at least two real call sites need them.
7. Never remove safety checks, validation, error handling, persistence guarantees, accessibility, or security to reduce LOC.

## Hard Constraints

- Phase 1 is PAPER ONLY.
- Real Gate.io orders must not be sent.
- Do not place API secrets in source, logs, screenshots, browser storage, or Git.
- Dashboard must not become the trading engine.
- Strategy processes must remain operational if the browser is closed.
- No martingale.
- No DCA.
- No forced daily profit target.
- No forced trade-count target.
- Default leverage is 1x.
- Every strategy result must be evaluated after fees and simulated execution costs.
- Trading must halt on stale market data or broken execution-state assumptions.

## Definition of Done

A feature is not done until:

- unit/integration tests pass;
- type/lint checks pass;
- browser-visible behavior is Playwright-tested when applicable;
- failure path is tested;
- documentation is updated;
- no live-order path was accidentally introduced;
- logs expose enough information to diagnose failure.
