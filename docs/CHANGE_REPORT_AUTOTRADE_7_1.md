# Autotrade 7.1 change report

## Implemented

- REST Momentum V2 is prevented from submitting PAPER entries.
- Entry V3 has explicit enabled, shadow-enabled, and execution-enabled configuration. Execution must remain false.
- The dashboard starts a Gate WebSocket V3 shadow capture after its first universe refresh and exposes V3 status through `/api/state`.
- Raw Gate events and V3 decisions are stored separately under the configured log directory.

## Verification

- Config, dashboard, Gate market-data, capture tests, Ruff on `autotrade`, and mypy on `autotrade` passed locally.
- The dashboard Playwright run produced all expected screenshots before the command timeout; its final completion line was not captured in this run.

## Limitations

V3 has insufficient evidence and remains SHADOW. Candidate outcome labeling, calibrated model training, purged walk-forward validation, Optuna, native Nautilus L2 matching, CI, and provenance taint handling are not implemented by this change. No performance result is claimed.
