# Execution model V2

The active dashboard exposes `BASELINE · EXECUTION DISABLED` for REST Momentum V2 and `SHADOW` for Entry V3. No Entry V3 code path submits an order.

Observed L2 spread and depth are captured. Modeled latency, residual slippage, and native Nautilus L2 fill simulation are not yet connected to a production PAPER execution path. They must not be represented as validated execution quality.
