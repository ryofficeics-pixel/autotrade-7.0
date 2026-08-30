# Playwright Testing and UI Audit

## Purpose

Use Playwright to verify the dashboard as a user sees it, not just component internals.

Reference:
https://playwright.dev/

## Required Browser Coverage

Primary:
- Chromium desktop

Add Firefox/WebKit only after the primary flow is stable or if cross-browser support is required.

## Critical E2E Tests

### Startup

- dashboard loads;
- PAPER mode visibly shown;
- engine/data health shown;
- live trading control does not exist;
- no secret appears in page source/storage.

### Overview

- equity and PnL render from mocked/test backend;
- stale state is visibly marked;
- engine offline state is not falsely green.

### Controls

- pause new entries requires clear state transition;
- resume restores only permitted paper operations;
- flatten-paper control handles confirmation correctly;
- controls are disabled when backend state is unknown.

### Trades

- fills appear correctly;
- filters work;
- fees/slippage/PnL display consistently;
- large tables remain usable.

### Markets

- rankings update;
- spread/liquidity rejection states are visible;
- disconnected symbols are not shown as tradable.

### Failure Tests

Simulate:

- WebSocket loss;
- backend unavailable;
- stale data;
- malformed metric;
- database error;
- strategy halted.
- TradingView disabled, degraded, disconnected and unavailable while the bot remains healthy.

UI must fail loudly and truthfully.

### TradingView Card

Verify connected and failure states, long chart symbols, latency/freshness updates, confidence
formatting, responsive layout, no console errors, no API errors, and continued rendering of the
authoritative Gate/Nautilus state. Save connected, disconnected and mobile screenshots.

## Visual Audit

Take screenshots for canonical pages at a fixed desktop viewport.

Audit:
- clipping;
- overflow;
- unreadable number formatting;
- layout shift;
- misleading colors/states;
- inaccessible controls;
- missing empty/error states.

## Rule

Playwright is an audit layer. It does not replace unit tests for strategy/risk logic.
