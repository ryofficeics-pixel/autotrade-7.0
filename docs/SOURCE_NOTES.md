# Source Notes

Checked August 2026.

## NautilusTrader

Official docs:
https://nautilustrader.io/docs/

Official GitHub:
https://github.com/nautechsystems/nautilus_trader

Relevant current characteristics:
- production-grade Rust-native trading engine;
- event-driven research/simulation/live architecture;
- Python control plane;
- Windows x86_64 support;
- quote/trade/order-book backtesting;
- risk engine;
- sandbox/paper and live contexts;
- live/research strategy parity is a core design goal.

## Hummingbot

Official:
https://hummingbot.org/

Strategies:
https://hummingbot.org/strategies/

Dashboard:
https://hummingbot.org/dashboard/

Current dashboard note:
The older Streamlit dashboard is no longer actively maintained; Hummingbot recommends Condor for current deployment/management workflows. Use the dashboard primarily as a UX/workflow reference, not as the foundation of this UI.

## Ponytail

Preferred upstream:
https://github.com/DietrichGebert/ponytail

Use for anti-overengineering/YAGNI discipline. Its own documentation explicitly preserves trust-boundary validation, security, data-loss handling and accessibility.

## Playwright

Official:
https://playwright.dev/

Use for dashboard E2E and visual/failure-state audit.
