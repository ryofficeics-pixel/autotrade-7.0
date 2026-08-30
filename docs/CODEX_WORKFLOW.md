# Codex Development Workflow

## Objective

Give Codex enough specification to implement incrementally without inventing scope.

## Recommended Workflow

### Step 1 — Repository Bootstrap

Codex reads:
- `AGENTS.md`
- `PROJECT_SCOPE.md`
- `ARCHITECTURE.md`

Deliver:
- minimal project skeleton;
- dependency file;
- environment example;
- test runner;
- lint/type configuration;
- no trading logic yet.

### Step 2 — Nautilus Spike

Prove:
- NautilusTrader installs on target Windows/Python environment;
- basic event loop starts;
- example market-data object can be processed;
- simple paper/sandbox lifecycle works.

Do not build UI during this spike.

### Step 3 — Gate Market Data

Implement:
- public market-data adapter path;
- timestamp normalization;
- reconnect;
- book integrity;
- recorder.

### Step 4 — Research Dataset

Implement raw capture and replay.

### Step 5 — Paper Execution

Implement pessimistic execution simulation.

### Step 6 — Strategy Challengers

Implement one at a time:
1. order-flow;
2. mean reversion;
3. micro market making.

Do not introduce strategy selector until independent metrics exist.

### Step 7 — Risk Layer

Wire hard trading states and halt rules.

### Step 8 — Metrics/API

Expose authoritative read-only state first.

### Step 9 — Dashboard

Implement only the pages in `DASHBOARD_UI.md`.

### Step 10 — Playwright Audit

Implement critical flows and failure-state tests.

### Step 11 — Long Run

Operate for extended paper sessions and fix stability problems before adding features.

## Ponytail Use

Use official Ponytail concepts to reduce over-engineering.

Preferred current upstream:
https://github.com/DietrichGebert/ponytail

Codex guidance:
- enable Ponytail if available in the user's Codex installation;
- treat its YAGNI/minimal-code discipline as secondary to this repo's safety rules;
- never remove validation or risk controls to reduce LOC.

## Commit Discipline

Small commits by vertical capability.

Examples:
- `feat(data): record Gate L2 updates`
- `test(risk): halt on stale market data`
- `feat(ui): add system health panel`

Avoid one giant generated commit.

## Mandatory Stop Conditions for Codex

Codex must stop feature expansion and fix the issue when:

- tests fail;
- market-data integrity is uncertain;
- risk state can be bypassed;
- real-order capability appears unexpectedly;
- UI reports a healthy state when backend state is unknown.
