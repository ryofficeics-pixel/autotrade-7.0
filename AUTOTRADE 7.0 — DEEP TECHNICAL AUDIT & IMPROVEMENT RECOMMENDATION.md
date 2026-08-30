# AUTOTRADE 7.0 — DEEP TECHNICAL AUDIT & IMPROVEMENT RECOMMENDATION

You are acting as the senior quantitative systems architect, trading-infrastructure engineer, reliability engineer, and adversarial reviewer for **Autotrade 7.0**.

You have access to the existing repository and the latest `PROJECT_HANDOFF.md`.

Your current task is NOT to immediately implement features.

Your first task is to perform a rigorous repository-level audit and produce a prioritized improvement plan based on what actually exists.

Do not assume the handoff is completely correct.

Verify important claims against the source code, configuration, tests, scripts, runtime architecture, and documentation.

---

# PRIMARY OBJECTIVE

Determine:

> What should Autotrade 7.0 build, fix, remove, postpone, or redesign next to maximize its probability of eventually becoming a robust, profitable, low-maintenance crypto futures scalping system?

The answer must prioritize **validity, trading expectancy, execution realism, reliability, and capital protection**, not feature count or dashboard polish.

---

# PROJECT INTENT

Autotrade 7.0 is intended to eventually become an autonomous crypto futures trading system.

Current intended characteristics:

- Gate.io
- NautilusTrader as primary execution authority
- Hummingbot ecosystem may be used for research where useful
- PAPER ONLY during the current phase
- approximately 300 USDT initial simulated capital
- initial leverage 1x
- small-capital friendly
- volatility-oriented pair selection
- short holding periods
- potentially approximately 20–30 trades/day if genuine opportunities exist
- no forced trade quota
- no forced daily-return target
- no martingale
- no DCA
- minimal human intervention
- high operational reliability
- eventual continuous strategy improvement
- live trading only after sufficient evidence

Do not interpret "aggressive" as permission to ignore risk or statistical validity.

---

# CRITICAL CURRENT CONTEXT

The existing handoff states that the current system includes:

- NautilusTrader paper execution
- active RiskEngine
- Gate public REST ticker polling
- 8-symbol momentum tournament
- one portfolio-wide execution slot
- spread/fee/slippage assumptions
- stops and time exits
- persistence and recovery
- local operations dashboard
- independent watchdog
- TradingView research sidecar isolated from execution
- Python tests
- Ruff
- mypy
- Playwright tests

However, the current research substrate reportedly lacks:

- WebSocket L2 order-book ingestion
- trade-by-trade market capture
- sequence-gap recovery
- deterministic historical replay
- realistic measured latency
- realistic partial fills
- queue-aware maker fills
- funding accounting
- proper execution-condition comparison
- holdout validation
- walk-forward validation
- sufficient forward sample size

Treat these as hypotheses to verify against the repository.

---

# IMPORTANT CONSTRAINT

DO NOT respond to recent paper losses by blindly tuning strategy parameters.

The current simulator may be insufficiently realistic for its PnL to represent executable expectancy.

Before recommending optimization, establish whether the underlying market data and execution simulation are good enough to optimize against.

The audit must explicitly answer:

> Are we currently measuring strategy quality, simulator behavior, or mostly noise?

---

# STEP 1 — VERIFY THE HANDOFF

Inspect the actual repository.

Verify:

1. Git state
2. application structure
3. Nautilus integration
4. Gate integration
5. market-data implementation
6. strategy implementation
7. execution simulator
8. position accounting
9. fee model
10. slippage model
11. risk controls
12. persistence
13. recovery
14. watchdog
15. dashboard
16. TradingView isolation
17. configuration safety
18. automated tests
19. Windows startup scripts
20. documentation

Create a table:

| Handoff claim | Verified | Partially verified | Incorrect | Evidence |
|---|---|---|---|---|

Do not trust documentation where implementation disagrees.

Implementation wins.

---

# STEP 2 — AUDIT MARKET DATA

Determine whether the current market-data system is suitable for the intended short-duration trading style.

Inspect:

- REST polling frequency
- timestamps
- local timestamps
- exchange timestamps
- clock synchronization assumptions
- bid/ask source
- last trade source
- volume
- funding
- stale-data handling
- missing updates
- crossed markets
- reconnect behavior
- raw-data persistence

Then determine what should replace or supplement REST.

Evaluate a Gate.io public WebSocket architecture supporting, where available:

- L2 order book
- incremental depth updates
- trades
- best bid/ask
- mark price
- index price
- funding information
- exchange timestamps
- local receive timestamps

Required design characteristics:

- sequence validation
- snapshot + delta synchronization
- gap detection
- automatic resnapshot
- reconnect
- bounded queues
- backpressure handling
- raw append-only event capture
- integrity metrics
- duplicate detection
- stale stream detection
- timestamp latency measurements

Recommend an event schema suitable for deterministic replay.

---

# STEP 3 — AUDIT EXECUTION REALISM

Inspect the current simulated execution.

Identify every unrealistic assumption.

Evaluate:

- spread crossing
- taker fees
- maker fees
- slippage
- order submission delay
- exchange acknowledgement delay
- cancellation latency
- market-order sweep behavior
- partial fills
- book depth
- queue position
- post-only behavior
- adverse selection
- stale resting orders
- funding
- mark price
- liquidation assumptions
- exchange minimum size
- precision
- tick size
- contract multiplier

Separate simulator requirements into:

### MUST HAVE

Required before strategy results are meaningful.

### SHOULD HAVE

Important before live trading.

### NICE TO HAVE

Can wait.

Design the smallest simulator that is realistic enough for the intended trading style without becoming an unnecessary exchange emulator.

---

# STEP 4 — DESIGN DETERMINISTIC REPLAY

Propose a replay architecture where recorded Gate events can be replayed through the same research strategy logic.

Desired flow:

```text
Gate WebSocket
      ↓
Raw event recorder
      ↓
Immutable session dataset
      ↓
Deterministic replay
      ↓
Strategy
      ↓
Execution simulator
      ↓
Trade journal
      ↓
Research report
```

Every replay should be reproducible.

Record:

- dataset ID
- source
- session
- symbols
- beginning timestamp
- ending timestamp
- event count
- dropped events
- sequence gaps
- code revision
- config revision
- strategy revision
- random seed if randomness exists
- simulator version

Two runs using the same:

```text
dataset
code
configuration
seed
```

must produce identical results.

---

# STEP 5 — AUDIT CURRENT STRATEGY

Review `RestMomentumStrategy` or its actual equivalent.

Do not optimize it yet.

Analyze:

- signal construction
- lookback
- persistence
- confidence
- movement threshold
- cost hurdle
- regime logic
- symbol ranking
- pair-selection logic
- exits
- cooldown
- stop loss
- take profit
- maximum holding period
- portfolio coordinator
- one-slot limitation

Determine which assumptions are:

```text
VALID
PLAUSIBLE
UNTESTED
WEAK
INVALID
```

Explain why.

Determine whether the existing strategy should eventually be:

```text
kept as baseline
rewritten
retired
or retained only as benchmark
```

---

# STEP 6 — DESIGN THE STRATEGY LAB

After the market-data/replay foundation exists, Autotrade should test several independent strategy families instead of endlessly tuning one momentum strategy.

Evaluate at least:

### A. Momentum

Short-duration directional continuation.

Potential inputs:

- trade velocity
- price velocity
- imbalance
- spread
- volatility
- volume acceleration

### B. Mean Reversion

Short-lived overextension/reversion.

Potential inputs:

- microprice deviation
- local VWAP deviation
- extreme short-window return
- temporary order-book imbalance
- liquidity normalization

### C. Order Flow

Potential inputs:

- aggressive buy/sell imbalance
- signed trade flow
- book pressure
- microprice
- depth imbalance
- liquidity pull/add behavior

### D. Micro-Maker

Only if data and simulator quality support it.

Potential concepts:

- inventory-neutral quoting
- spread capture
- imbalance-aware skew
- short-lived post-only orders
- strict adverse-selection protection

Do not assume every strategy should be implemented.

Recommend the smallest useful initial set.

---

# STEP 7 — DESIGN RESEARCH METRICS

The strategy report must not optimize on PnL alone.

Recommend mandatory metrics including:

### Returns

- gross PnL
- net PnL
- return %
- expectancy/trade

### Trading

- trade count
- win rate
- average win
- average loss
- payoff ratio
- profit factor

### Risk

- maximum drawdown
- average drawdown
- downside deviation
- largest loss
- consecutive losses
- tail-loss distribution

### Execution

- fees
- funding
- spread cost
- modeled slippage
- realized simulated slippage
- partial-fill rate
- average fill latency
- cancellation latency
- unfilled-order rate

### Trade Quality

- MAE
- MFE
- entry efficiency
- exit efficiency
- time in trade

### Segmentation

Break results down by:

- symbol
- strategy
- hour
- volatility regime
- spread regime
- liquidity regime
- long/short
- day/session

---

# STEP 8 — VALIDATION PIPELINE

Design a validation hierarchy.

Do NOT allow:

```text
backtest winner
→ production
```

Use something similar to:

```text
Research
   ↓
In-sample
   ↓
Holdout
   ↓
Walk-forward
   ↓
Sensitivity analysis
   ↓
Monte Carlo / trade resampling
   ↓
Shadow/Paper
   ↓
Champion comparison
   ↓
Controlled promotion
```

Evaluate whether purged or embargoed cross-validation is appropriate for the strategy style.

Detect:

- overfitting
- data leakage
- parameter instability
- regime dependence
- selection bias
- multiple-testing bias

---

# STEP 9 — CONTINUOUS IMPROVEMENT ENGINE

Only after the research foundation is reliable, design a Champion/Challenger system.

Architecture:

```text
LIVE/PAPER CHAMPION
        ↓
TRADE JOURNAL
        ↓
PERFORMANCE AUDITOR
        ↓
DRIFT / OPPORTUNITY DETECTOR
        ↓
RESEARCH ENGINE
        ↓
CHALLENGER GENERATION
        ↓
BACKTEST
        ↓
HOLDOUT
        ↓
WALK-FORWARD
        ↓
ROBUSTNESS TEST
        ↓
SHADOW/PAPER
        ↓
PROMOTION GATE
        ↓
NEW CHAMPION
```

Strict rule:

```text
optimizer
≠
production authority
```

Never allow automated parameter optimization to directly overwrite live production configuration.

---

# STEP 10 — OPTIMIZATION ENGINE

Evaluate whether Optuna is appropriate.

Potential optimization parameters may include:

```text
entry threshold
imbalance threshold
spread threshold
volatility threshold
confidence threshold
holding period
stop
target
cooldown
order offset
regime parameters
```

Do not optimize:

```text
maximum PnL
```

alone.

Recommend a robustness-oriented objective.

Example conceptual structure:

```text
reward:
expectancy
profit factor
risk-adjusted return
cross-window consistency
trade quality

penalize:
drawdown
tail losses
cost sensitivity
parameter instability
low sample count
regime dependence
```

Do not blindly use arbitrary weights.

Recommend how weights or multi-objective optimization should be handled.

---

# STEP 11 — SAMPLE-SIZE RULES

The system expects relatively frequent trading, potentially 20–30 opportunities/trades per day depending on actual market conditions.

Do not equate:

```text
one day
```

with:

```text
statistically meaningful performance
```

Design minimum evidence requirements before:

- declaring degradation
- changing parameters
- rejecting Champion
- promoting Challenger

Consider:

```text
trade count
market regime coverage
days observed
symbol coverage
confidence intervals
effect size
```

Avoid false precision.

---

# STEP 12 — CHAMPION/CHALLENGER REGISTRY

Recommend versioning for:

```text
strategy
parameters
dataset
simulator
execution model
code
risk configuration
```

Each research result should be traceable.

Evaluate using:

- MLflow
- a lightweight local alternative
- custom SQLite
- filesystem metadata

Do not add MLflow merely because it exists.

Recommend the lowest-complexity system justified by current scale.

Each candidate should eventually have states such as:

```text
DRAFT
TESTING
FAILED
SHADOW
CHALLENGER
CHAMPION
RETIRED
```

---

# STEP 13 — DRIFT DETECTION

Determine how Autotrade should recognize:

```text
normal variance
vs
actual strategy degradation
```

Potential signals:

- expectancy drift
- win-rate drift
- spread change
- volatility change
- slippage increase
- fill degradation
- regime shift
- liquidity deterioration
- pair-specific deterioration

Avoid triggering optimization from tiny samples.

Recommend robust triggers.

---

# STEP 14 — PAIR SELECTION

Audit the existing market-selection heuristic.

The current target is not necessarily maximum market capitalization.

The objective is:

> Find markets where small capital can efficiently exploit genuine short-duration opportunities after costs.

Evaluate ranking using:

- realized volatility
- spread
- depth
- turnover
- trade frequency
- order-book stability
- short-term impact
- funding
- slippage
- executable edge

Do not select coins solely because they are volatile.

Volatility without liquidity/executability may simply increase losses.

Recommend a better dynamic universe selection process.

---

# STEP 15 — RISK ENGINE AUDIT

Review all risk controls.

Evaluate:

- 1x leverage
- notional sizing
- maximum position
- one-position rule
- daily loss halt
- maximum drawdown
- stale feed protection
- reconnect protection
- persistence recovery
- flatten behavior
- manual halt
- watchdog auto-resume

Identify missing controls.

Consider:

- rolling loss limits
- strategy-level kill switch
- symbol-level kill switch
- abnormal spread halt
- abnormal volatility halt
- execution anomaly halt
- repeated rejection halt
- stale book halt
- sequence-gap halt
- max consecutive losses
- max exposure duration
- data-integrity halt

Do not add arbitrary limits without explaining their purpose.

---

# STEP 16 — RELIABILITY AUDIT

Assume this system eventually runs unattended 24/7.

Identify failure modes involving:

```text
process crash
machine restart
internet loss
Gate disconnect
sequence gap
stale book
corrupted state
disk full
log growth
clock issue
duplicate process
port conflict
bad configuration
dependency failure
partial writes
browser failure
TradingView failure
```

Determine:

- detection
- response
- auto-recovery
- cases where auto-recovery must NOT occur

The bot must fail closed where state integrity is uncertain.

---

# STEP 17 — PERFORMANCE / HOT PATH

Identify anything inappropriate for a latency-sensitive hot path.

The following should generally NOT sit in the execution hot path:

```text
LLMs
TradingView
dashboard
Optuna
MLflow
heavy analytics
research notebooks
```

Separate:

```text
TRADING HOT PATH
```

from:

```text
RESEARCH / CONTROL PLANE
```

Recommend clear boundaries.

---

# STEP 18 — DEPENDENCY / REPOSITORY AUDIT

Examine whether external repositories or libraries would materially improve the system.

For every proposed dependency provide:

```text
repository
purpose
integration point
benefit
cost
maintenance risk
whether to integrate now/later/never
```

Potential areas:

- WebSocket connectivity
- event storage
- optimization
- walk-forward validation
- portfolio analytics
- drift detection
- experiment tracking
- testing
- fault injection

Avoid dependency accumulation.

Prefer implementing simple functionality locally when adding a framework creates more operational burden than value.

---

# STEP 19 — SECURITY

Audit:

- local HTTP controls
- Origin/Host protection
- environment variables
- secrets
- logs
- subprocesses
- TradingView sidecar
- future Gate credentials
- file permissions
- command injection
- dashboard binding
- dependency pinning

Phase 1 must remain:

```text
PAPER ONLY
```

Do NOT add private Gate credentials.

Do NOT implement a real-order path.

---

# STEP 20 — GIT / REPRODUCIBILITY

The handoff reports an unborn Git HEAD and no commits.

Verify this immediately.

If true, classify it appropriately.

Recommend:

```text
baseline commit
small atomic commits
config versioning
research versioning
dataset versioning
tagging
rollback
```

Every future performance claim must identify:

```text
git revision
strategy revision
config revision
dataset
simulator revision
```

---

# REQUIRED OUTPUT

Create:

```text
docs/AUTOTRADE_7_IMPROVEMENT_AUDIT.md
```

Use this exact structure:

# 1. Executive Verdict

State clearly:

```text
What Autotrade 7.0 actually is today.
What it is not.
What its biggest bottleneck is.
```

# 2. Verified Current Architecture

Explain the actual system based on source code.

# 3. Handoff Accuracy Audit

Table comparing handoff claims with implementation.

# 4. Critical Problems

Rank:

```text
P0
P1
P2
P3
```

# 5. Things We Should NOT Build Yet

Be aggressive about removing premature complexity.

# 6. Market Data Assessment

REST versus WebSocket and required architecture.

# 7. Execution Simulator Assessment

Current realism and required upgrades.

# 8. Replay Architecture

Exact recommended design.

# 9. Strategy Assessment

Evaluate the current momentum baseline.

# 10. Strategy Lab Recommendation

Recommend which independent strategy families should be added and in what order.

# 11. Research / Statistical Validation

Design the validation pipeline.

# 12. Continuous Improvement Architecture

Design Champion/Challenger.

# 13. Optimization Recommendation

Explain whether/when Optuna or alternatives should be introduced.

# 14. Risk Audit

Current controls, missing controls, unnecessary controls.

# 15. Reliability Audit

24/7 failure scenarios and recovery.

# 16. Security Audit

Current and future risks.

# 17. External Repository Recommendations

For each:

```text
USE NOW
USE LATER
DO NOT USE
```

# 18. Architecture Recommendation

Provide a final target architecture diagram.

# 19. Prioritized Roadmap

Use:

```text
P0 — immediately
P1 — next
P2 — after foundation
P3 — later
```

For each item provide:

```text
WHY
DEPENDENCY
ACCEPTANCE CRITERIA
EXPECTED VALUE
COMPLEXITY
```

# 20. Exact Next 10 Tasks

Give an ordered list.

Task #1 must be the single highest-value next action based on actual repository condition.

# 21. Promotion Criteria Toward Live Trading

Define evidence required before Phase 2/live should even be considered.

---

# DECISION PRINCIPLES

Whenever deciding between two approaches, rank them in this order:

1. correctness
2. capital protection
3. execution realism
4. statistical validity
5. reliability
6. measurable trading value
7. simplicity
8. operating cost
9. implementation speed
10. visual polish

---

# ANTI-PATTERNS

Reject proposals that amount to:

```text
more indicators = better strategy

more AI = smarter trader

more optimization = better expectancy

higher win rate = better system

more trades = more profit

more volatility = more opportunity

backtest profit = live alpha

recent losses = strategy broken

recent wins = validated alpha

complex architecture = mature architecture
```

Demand evidence.

---

# IMPORTANT

Do NOT modify trading behavior yet unless required to fix a correctness or safety defect.

This is an **audit and recommendation task**.

Do not prematurely implement:

- Optuna
- MLflow
- AI decision agents
- genetic strategy mutation
- live trading
- leverage
- multi-exchange support
- cloud infrastructure
- React migration
- microservices
- Redis
- Kubernetes

unless the audit can demonstrate that one of them solves a present bottleneck better than a simpler solution.

The likely immediate bottleneck is expected to be:

```text
market data
→ replay
→ execution realism
→ validation
```

but verify this independently.

If repository evidence proves otherwise, explain why.

---

# FINAL QUESTION TO ANSWER

End the audit with a direct answer:

> If we had only enough development capacity to make THREE major improvements to Autotrade 7.0 before evaluating whether the strategy has real alpha, what exactly should those three improvements be, in order, and why?

Do not give generic advice.

Base the answer on the actual repository.