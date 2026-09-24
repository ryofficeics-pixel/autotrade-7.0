# AUTOTRADE 7.0 RESEARCH & PROMOTION ENGINE IMPLEMENTATION REPORT

Evidence cutoff: 2026-09-24T01:17:48.492Z for the generated report. The frozen XAU dataset ends at
2026-09-24T01:15:35.776Z. Runtime state was checked again after generation.

## 1. Executive Summary

This implementation adds a separate offline research system with immutable XAU datasets, a common
causal Strategy API, deterministic replay, candidate and counterfactual ledgers, explicit execution
costs, chronological validation, a sealed final holdout, append-only experiment and lifecycle
registries, manual PAPER approval gates, and a read-only dashboard Research section.

The trading engine, account authority, 1x leverage, PAPER-only restriction, risk limits, existing
ledger, and failed REST baseline remain unchanged. Entry V3 and both KAMA controls remain unable to
execute. No new strategy is armed.

Current trading state is PAPER, HALTED, flat, and unarmed. Risk remains
`STRATEGY_EVIDENCE_FAILED`. No strategy is PAPER eligible.

The reason the existing system lost money is direct, not ambiguous:

- 427 normal closes produced gross price PnL of -12.17927339 USDT before fees.
- Fees added another -12.66494518 USDT.
- Normal net PnL is -24.84421854 USDT and profit factor is 0.5062.
- Fees caused 50.98% of the total normal loss, but the strategy was already losing before fees.
- Recovery and manual results are excluded from normal expectancy. They do not rescue the strategy.

The implemented improvement is a fail-closed research and promotion process. It prevents more money
from being assigned to an unproven strategy. It does not fabricate a profitable replacement.

## 2. Repository Baseline

- Commit before changes: `bc0e4b8980f0f082d5f3a09d4886ba9ac1d0763d`.
- Original validation: 125 Python tests passed, Ruff passed, mypy passed, and 5 Playwright tests
  passed.
- Original runtime: PAPER, HALTED, zero positions, accounting VALID, data LIVE, risk
  `STRATEGY_EVIDENCE_FAILED`, strategy unarmed.
- Original normal evidence: 427 trades, -12.17927339 USDT gross price PnL, 12.66494518 USDT fees,
  -24.84421854 USDT net PnL, and profit factor 0.5062.
- Pre-existing user file `docs/AUDIT_REPORT_2026-09-18_POST_RESTART.md` was not modified or committed.

## 3. Architecture Changes

Added modules:

- `autotrade/research.py`: immutable dataset, strategy protocol, replay, counterfactuals, costs,
  validation, ledgers, registries, and lifecycle.
- `autotrade/research_cli.py`: `full`, `freeze`, `verify-dataset`, and `approve-paper` commands.
- `config/research.toml`: versioned validation and execution assumptions.
- `docs/RESEARCH_ENGINE.md`: operating and evidence specification.

Modified modules:

- `autotrade/dashboard.py` reads a bounded research report into dashboard state. It adds no control.
- `dashboard/index.html`, `dashboard/app.js`, and `dashboard/styles.css` show research evidence and
  explicit empty/error states.
- Tests and source specifications now cover the new boundary.

Data flow:

```text
runtime shadow evidence
  -> verified source prefix
  -> immutable dataset
  -> causal strategy candidates
  -> cost-aware outcomes and counterfactuals
  -> chronological stages and sealed holdout
  -> append-only experiment/lifecycle evidence
  -> read-only dashboard report
```

The research modules do not import `PaperTrader`, runtime controls, market-scope mutation, or order
submission. The dashboard remains a read/control surface for existing PAPER controls. Research adds
no execution authority.

## 4. Immutable Dataset

- Dataset ID: `xau-ama-v2-5b3f99347a088eb363d7`.
- Status: `FROZEN`, immutable.
- Window: 2026-09-19T06:31:02.561Z to 2026-09-24T01:15:35.776Z.
- Primary rows: 8,006.
- Reference rows: 16,012.
- Symbols: `XAU_USDT`, `XAUT_USDT`, `PAXG_USDT`.
- Code commit: `7e04aed2755fe205363d4c60308e05c8ef458054`.
- Dataset identity hash: `5b3f99347a088eb363d7a623a413cd694488d9afc2a7bcdad2822590d4da6dc8`.
- Source-prefix SHA-256: `5f696e28c0a1cbbd7f167222fdfba1c111afab72e1311295a2ed6b820f2428f7`.
- Source last sequence: 53,580.
- Source last record hash:
  `9362dd367207b9b613e527f9df75ce2756da53ea31395c7f0228c1f0b2bbd752`.
- Market events SHA-256: `c5931249bb7609be3f0f5b1719869e06d2bb2c06b86afa8cd4d156866d427358`.
- Reference events SHA-256:
  `bd0f0478f06f960bdb93966758993b7898865192d60f4d6953462b0a7a7b43b3`.
- Metadata SHA-256: `095c3cf4378d31f082085cd379e91313e60f7fb3aee9fec7e752c318af9a6290`.

Known limitations:

- REST receive time is the observation clock because exchange event time is absent.
- The source lacks executable BBO, depth, queue position, and fill evidence.
- Reference rows share a poll but do not carry independent freshness proof.
- Candidate observations overlap and are not independent trades.
- Execution profiles are assumptions, not calibrated Gate fills.

## 5. Strategy API

`StrategyProtocol` supplies immutable strategy/version/hash/parameter identity and one causal
`evaluate(history, index, profile)` entry point. Replay exposes only the current and earlier event
prefix. A candidate timestamp later than its current observation fails.

`SignalCandidate` records candidate ID, event identity, time, direction, signal strength, regime,
decision, full gate results, first binding rejection, cost decomposition, and strategy identity.
Signal strength is explicitly heuristic, not a probability.

Isolation guarantees:

- no trader or order interface is present;
- every strategy uses the same frozen timeline where its inputs exist;
- missing reference data rejects fair-value families;
- missing L2/trade inputs mark Entry V3 non-reconstructable rather than synthesizing them;
- lifecycle writes cannot arm the runtime.

## 6. Replay Engine

Replay is deterministic for dataset, strategy hash, parameter hash, profile version, Git commit,
config hash, and seed. Result files and candidate/counterfactual ledgers have independent integrity
hashes. Re-running the same identity loads and verifies the same experiment.

Supported same-timeline strategies:

- `XAU_KAMA20_RAW_REPLAY_V1`;
- `XAU_KAMA20_FILTERED_REPLAY_V1`;
- `XAU_FAIR_VALUE_V1`;
- `XAU_COMBINED_V1`.

Unsupported comparisons:

- REST Momentum V2 needs its original multi-symbol crypto timeline.
- Entry V3 needs synchronized L2, trades, exchange timestamps, and books.

Both unsupported families appear as `NOT_RECONSTRUCTABLE`. The captured Entry V3 decision funnel is
still analyzed across all restart segments.

## 7. Counterfactual Analysis

KAMA filtered captured evidence contains 3,195 distinct decisions, 3,188 resolved outcomes, one
accepted decision, and 3,187 resolved rejected decisions.

- Avoided-loss classifications: 3,022.
- Missed profitable candidates: 165.
- Sum of avoided loss: 56,528.5616 bps on reference notional.
- Sum of missed opportunity: 1,767.9168 bps.
- Net modeled filter benefit: 54,760.6448 bps.
- Summed no-trade intervals: 412,996.241 seconds.

These bps are sums across overlapping shadow candidates. They are not portfolio return, independent
trades, or executable PnL. The filter rejected many bad directions, but its only accepted replay
sample lost after costs. A useful rejector is not yet a profitable entry strategy.

Entry V3 has 282,949 distinct candidates across 21 capture prefixes, zero accepted candidates, and
no accepted forward-outcome ledger. Its no-trade duration is 184,373.738 seconds after excluding
offline gaps between capture files.

## 8. Entry Funnel

KAMA filtered:

| Gate | Rejected |
|---|---:|
| Regime | 1,790 |
| Regime persistence | 549 |
| Hysteresis | 479 |
| Slope | 356 |
| Quality | 13 |
| Data freshness | 7 |

Entry V3:

| Gate | Rejected |
|---|---:|
| Regime | 193,899 |
| Quality | 86,779 |
| Expected edge | 1,648 |
| Extension | 623 |

The evidence does not justify loosening these gates. Entry V3 has no accepted sample to establish
that rejected candidates would survive costs. KAMA has one accepted sample and it is negative.

## 9. Walk-Forward

The method is chronological 50/20/15/15 with three rolling and three anchored folds. Shuffling is
disabled.

| Stage | Effective window | Purge | Embargo | Result |
|---|---|---:|---:|---|
| Selection | 2026-09-19T06:31:02.561Z to 2026-09-22T07:59:55.564Z | 900 s | 0 s | development only |
| Validation | 2026-09-22T08:30:11.238Z to 2026-09-23T03:26:05.787Z | 900 s | 900 s | no promoted result |
| Test | 2026-09-23T03:56:13.399Z to 2026-09-23T06:22:17.113Z | 900 s | 900 s | no accepted evidence |
| Final holdout | 2026-09-23T06:52:25.905Z to 2026-09-24T01:15:35.776Z | 0 s | 900 s | sealed |

The final holdout seal binds dataset, strategy hash, and parameter hash before evaluation. Raw KAMA
has a final-holdout net result of -21.176434 bps. The other strategies have no accepted final-holdout
sample. Parameter-neighborhood search remains withheld because no family passed gross and net entry
screening.

## 10. Execution Realism

All values below are versioned assumptions.

| Profile | Fee | Spread | Slippage | Latency | Adverse | Funding | Impact | Fill / partial |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| IDEALIZED v1 | 10 bps | 0x | 0 | 0 | 0 | 0 | 0 | 100% / 100% |
| BASELINE v1 | 10 bps | 1x | 4 bps | 0 | 0 | 3 bps | 0 | 100% / 100% |
| STRESSED v1 | 15 bps | 1.5x | 6 bps | 3 bps | 2 bps | 4.5 bps | 1 bps | 80% / 80% |
| REALISTIC SIMULATED_V1 | 10 bps | 1x | 4 bps | 2 bps | 1.5 bps | 3 bps | 0.5 bps | 90% / 90% |

Every result decomposes gross move, spread, fee, slippage, latency, adverse selection, funding,
impact, partial-fill effect, total cost, and net edge. Current promotion remains blocked by
`EXECUTION_MODEL_NOT_VALIDATED`. The `REALISTIC` label means simulated sensitivity, not observed
execution truth.

## 11. Challenger Results

Baseline profile results:

| Strategy | Experiment | Accepted | Gross exp. | Net exp. | PF | Test | Holdout | Status |
|---|---|---:|---:|---:|---:|---:|---:|---|
| XAU_KAMA20_RAW_REPLAY_V1 | `cd89f0cd43b7fbac29a3229b2e22addb` | 8 | -7.3812 bps | -24.4042 bps | 0.00 | no sample | -21.1764 bps | failed |
| XAU_KAMA20_FILTERED_REPLAY_V1 | `41c929da96e72f8a0da5c9c94b033397` | 1 | -3.5756 bps | -20.5986 bps | 0.00 | no sample | no sample | failed |
| XAU_FAIR_VALUE_V1 | `41deb6c3349d9d08e68e3053ee15ac45` | 1 | +2.6017 bps | -14.4213 bps | 0.00 | no sample | no sample | failed |
| XAU_COMBINED_V1 | `50988cf837e9a1b96352a739b82ce12f` | 0 | no sample | no sample | N/A | no sample | no sample | failed |

Raw KAMA also fails under IDEALIZED assumptions. Fair value creates positive gross movement in one
baseline sample, but total modeled cost is 17.023 bps and makes the result negative. Combined has no
baseline acceptance. No result reaches 100 independent trades, profit factor 1.10, positive test,
positive holdout, stress survival, or parameter stability.

## 12. Promotion Status

| Strategy family | Lifecycle | Promotion state |
|---|---|---|
| REST Momentum Tournament V2 | RETIRED | failed baseline, reconsider only after material new version |
| XAU KAMA20 raw V2 | QUARANTINED | cost-dominated negative evidence |
| XAU KAMA20 filtered V2 | OBSERVING | one accepted sample, insufficient evidence |
| Entry V3 | EXPERIMENTAL | 282,949 rejected, zero accepted |
| XAU fair value V1 | EXPERIMENTAL | positive gross sample, negative net, inadequate reference evidence |
| XAU combined V1 | EXPERIMENTAL | zero accepted baseline samples |

Validated: none. PAPER eligible: none. PAPER active challengers: none. Automatic promotion is
impossible. A future eligible strategy still requires separate attributed transitions from VALIDATED
to PAPER_ELIGIBLE and then PAPER_ACTIVE. LIVE has no transition.

## 13. XAU Fair Value

The implementation normalizes XAUT and PAXG reference movement and evaluates XAU deviation against
their combined reference. It rejects missing, abnormal, or directionless reference inputs and applies
the same realizable-edge gate as other strategies.

Evidence: one BASELINE acceptance, +2.6017 bps gross expectancy, -14.4213 bps net expectancy, profit
factor 0, and no test or holdout acceptance. Under IDEALIZED fee-only assumptions, the wider candidate
set remains negative after the 10 bps fee.

Limitations: individual reference freshness, executable spreads, exchange event time, and synchronized
books are unavailable. Promotion is explicitly blocked by `REFERENCE_FRESHNESS_UNAVAILABLE` and
`EXECUTION_MODEL_NOT_VALIDATED`.

## 14. XAU Combined

The combined strategy requires agreement between filtered KAMA direction and fair-value direction,
then applies the common cost gate. It adds no runtime execution path.

Evidence: zero BASELINE, STRESSED, or REALISTIC accepted candidates. IDEALIZED accepted eight and
lost -23.3737 bps per accepted sample after fee. There is no basis for tuning or promotion.

Limitations are the union of KAMA overlap, REST timing, reference freshness, and execution-model
limitations.

## 15. Confidence Calibration

All four replay families are `UNCALIBRATED`. Accepted samples are 8, 1, 1, and 0 against a minimum
calibration sample of 200. Brier score, expected calibration error, and calibrated probability remain
unset. The dashboard now uses `Signal strength` and does not present heuristic scores as probability.

## 16. Dashboard

Functional changes only:

- added a real `Research` navigation target;
- added dataset, hash, Git, config, profile, and experiment identity;
- added Strategy Lab, Candidate Funnel, No-Trade Value, Execution Edge, and lifecycle tables;
- added explicit READY, UNAVAILABLE, ERROR, loading, and empty states;
- added no strategy eligible and manual approval messages;
- contained wide research tables in horizontal table wrappers;
- improved control/status boundary contrast and retained visible focus styling;
- added no research mutation, promotion, activation, or LIVE control.

The backend caches the report by mtime and rejects files over 5 MB. A malformed report becomes a
loud ERROR state. Browser closure still has no effect on the trading service.

## 17. Test Results

- Python unit/integration tests: 137 passed.
- Research-specific tests: 11 passed.
- Ruff: passed all 33 checked source files.
- mypy: passed all checked `autotrade` and `tests` modules.
- Playwright: 5 passed, including research content and mobile overflow checks.
- `pip check`: no broken requirements.
- Runtime restart: graceful shutdown and hidden restart completed.
- Post-restart polling: three consecutive cycles remained PAPER, HALTED, unarmed, flat, accounting
  VALID, data LIVE, and risk `STRATEGY_EVIDENCE_FAILED`.
- Ordinary Resume after restart: HTTP 409.
- Health check: service healthy; expected warning reports intentional halted/unarmed state.
- pandas: pinned to 2.3.3, which satisfies NautilusTrader 1.231.0. A third-party NumPy timedelta
  deprecation warning remains in Nautilus/pandas test paths; it does not fail tests.

Anti-Slop Delivery Gate:

- R-02 PASS: dashboard source contains no em dash.
- R-03 PASS: Playwright at 390 px reports no document-level horizontal overflow; tables scroll inside wrappers.
- R-17 PASS: UI numbers come from `/api/state` or the frozen report.
- R-18 PASS: no testimonials or people were added.
- R-23 PASS: no logo, avatar, image, or fabricated navigation asset was created.
- R-24 PASS: every sidebar anchor targets an existing section, including `#research`.
- R-25 PASS: tested text pairs pass AA; muted text is 6.20:1 and primary text is 16.55:1.
- R-26 PASS: no research controls were added; existing tested controls retain handlers.
- R-27 PASS: research loading, empty, unavailable, error, and ready states are explicit.
- R-28 PASS: no FAQ exists.
- R-32 PASS: native links/controls remain keyboard operable and `:focus-visible` uses a high-contrast outline.
- R-33 PASS: UI source was edited directly, not rewritten by a helper script.
- R-34 PASS: the app ships one documented dark operations theme and no broken theme toggle.
- R-35 PASS: the app ran under Playwright and the rendered desktop/mobile screenshots were inspected.
- R-36 PASS: no security, compliance, performance, or customer claim was added.
- R-37 PASS: `DASHBOARD_UI.md` records the design read and ENERGY 2 / RHYTHM 2 / MOTION 1.
- R-38 PASS: all displayed content maps to real backend/report fields or an honest empty state.
- R-01 PASS: restrained gradients serve existing control/evidence hierarchy and are documented.
- R-04 PASS: no icon library or generic AI glyph was added.
- R-06 PASS: existing compact Roboto operations typography is preserved and documented.
- R-07 PASS: no decorative background grid or pattern exists.
- R-08 PASS: no decorative button arrows exist.
- R-09 PASS: badges represent real operational states.
- R-10 PASS: no glassmorphism was added.
- R-12 PASS: no component shadow system was added.
- R-13 PASS: no glow treatment exists.
- R-14 PASS: research uses evidence tables, not feature-card templates.
- R-19 PASS: no template animation was added, matching MOTION 1.
- R-22 PASS: no generic illustration exists.
- Liveliness dials PASS: ENERGY 2, RHYTHM 2, MOTION 1 are explicit.
- Dial consistency PASS: dense static operations layout matches the declared values.
- Focal point PASS: unsafe operating state remains the first visual priority.
- Whitespace PASS: spacing separates runtime, evidence, and audit groups.
- Accent PASS: cyan is reserved for PAPER-safe/valid state; amber and red retain warning roles.
- Identity motif PASS: square status marks, hard borders, compact labels, and dense evidence tables repeat consistently.
- Design Read PASS: the operations-console direction is recorded in `DASHBOARD_UI.md`.
- C-1 PASS: major visual and copy choices have an operations/evidence reason.
- C-2 PASS: no dead research interaction exists.
- C-3 PASS: every new section corresponds to requested research evidence.
- C-4 PASS: ready, empty, error, disconnected, desktop, and mobile paths are tested.
- C-5 PASS: all statistics are generated from actual local evidence.
- R-05 PASS: the layout follows operational priority, not a landing-page template.
- R-11 PASS: hard rectangular controls remain distinct from status marks.
- R-15 PASS: control labels name their exact actions.
- R-16 PASS: UI copy contains no AI marketing language.
- R-20 PASS: the research view extends the existing Scalper operations identity.
- R-21 PASS: dark mode serves continuous monitoring and is documented.
- R-29 PASS: neutral base, cyan, amber, and red use a documented state system.
- R-30 PASS: no popular-product layout was copied.
- R-31 PASS: color, layout, typography, spacing, and table choices each have a documented reason.

## 18. Bugs Found

1. No immutable, comparable research dataset or experiment identity existed.
2. No shared strategy interface prevented inconsistent comparison logic.
3. Candidate outcomes could be mistaken for independent trades or counted once per horizon.
4. KAMA filter benefit could be mistaken for accepted-strategy profitability.
5. Entry V3 had zero accepted candidates, but the binding-gate history was not summarized.
6. The first Entry V3 report implementation selected only the newest capture directory. A restart
   therefore reduced visible history from 20,667 candidates to 2,232.
7. Heuristic confidence labels could read like calibrated probability.
8. Research evidence had no dashboard identity, lifecycle, or explicit promotion boundary.
9. pandas 3.0.5 produced a Nautilus compatibility warning path.

## 19. Bugs Fixed

1. Added immutable prefix snapshots with manifests and content hashes.
2. Added one causal Strategy API and common result schema.
3. Added distinct candidate identity and separate horizon outcomes.
4. Split avoided losses, missed profitable candidates, and accepted-strategy metrics.
5. Added KAMA and Entry V3 binding-gate funnels.
6. Aggregated Entry V3 across 21 capture prefixes, excluded offline gaps from no-trade duration,
   rejected duplicate identities, and added a restart regression test.
7. Replaced user-facing probability-like confidence labels with signal strength.
8. Added append-only lifecycle evidence and a read-only dashboard surface.
9. Pinned pandas 2.3.3, within NautilusTrader 1.231.0 requirements.

## 20. Remaining Risks

P0:

- Keep the failed REST strategy halted. It has negative gross edge and negative net edge.
- Do not loosen Entry V3 or KAMA filters. Neither has positive accepted out-of-sample evidence.
- Do not activate fair value from the one positive gross sample. Costs make it negative.

P1:

- Capture synchronized exchange timestamps, executable BBO/L2, trades, and per-reference freshness.
- Calibrate spread, latency, adverse selection, fill probability, partial fills, impact, and funding
  from PAPER evidence before treating `REALISTIC` as evidence.
- Freeze a new versioned dataset after the richer capture is complete, then rerun the same sealed
  process.

P2:

- Replace in-memory multi-capture Entry V3 parsing with streaming aggregation only if capture volume
  causes measurable memory pressure. The current implementation favors simpler verified code.
- Revisit third-party NumPy timedelta deprecation warnings when Nautilus or pandas publishes a stable
  compatible fix. Do not move to a pre-release engine solely to silence warnings.

## 21. Changed Files

1. `.gitignore`
2. `README.md`
3. `autotrade/dashboard.py`
4. `autotrade/research.py`
5. `autotrade/research_cli.py`
6. `config/research.toml`
7. `dashboard/app.js`
8. `dashboard/index.html`
9. `dashboard/styles.css`
10. `docs/ARCHITECTURE.md`
11. `docs/BACKTEST_VALIDATION.md`
12. `docs/DASHBOARD_UI.md`
13. `docs/DATA_SPEC.md`
14. `docs/RESEARCH_ENGINE.md`
15. `docs/RISK_MANAGEMENT.md`
16. `docs/STRATEGY_PROMOTION_POLICY.md`
17. `docs/TRADING_SPEC.md`
18. `requirements.txt`
19. `tests/e2e/dashboard.spec.js`
20. `tests/test_dashboard.py`
21. `tests/test_research.py`
22. `docs/AUTOTRADE_7_RESEARCH_PROMOTION_ENGINE_IMPLEMENTATION_REPORT_2026-09-24.md`

## 22. Commits

- `bc0e4b8980f0f082d5f3a09d4886ba9ac1d0763d`: baseline before this implementation.
- `07e1b9bd02850a064b59e303a9f2d5828603fc6a`: offline research, replay, promotion evidence,
  dashboard, tests, and documentation.
- `7e04aed2755fe205363d4c60308e05c8ef458054`: preserve Entry V3 evidence across restart capture
  segments.

The report commit is created after this document. Git content cannot contain its own final object hash
without changing that hash; the delivery response records the report commit exactly.

## 23. Current Runtime State

- Mode: PAPER.
- Trading state: HALTED.
- Strategy: REST Momentum Tournament, PAUSED, unarmed.
- Positions: 0.
- Equity: 279.9134338 USDT.
- Accounting: VALID.
- Market data: LIVE.
- Risk: `STRATEGY_EVIDENCE_FAILED`.
- Research: READY.
- Research dataset: `xau-ama-v2-5b3f99347a088eb363d7`.
- PAPER-eligible strategies: 0.
- Resume: disabled; direct request returns HTTP 409.
- Runtime execution authority changed: no.

## 24. Recommended Next Experiment

Do not tune the existing thresholds. The next useful experiment is data acquisition, not strategy
optimization.

Capture one uninterrupted, synchronized XAU/XAUT/PAXG and Entry V3 dataset with exchange timestamps,
executable BBO/L2, trades, depth, and per-feed freshness. Use PAPER fills to estimate the execution
profile. Then test one new versioned fair-value premise with a gross edge floor above measured total
cost. Keep the existing 50/20/15/15 chronology, purge, embargo, sealed holdout, and manual lifecycle.

Advance only if the new version shows positive gross and net expectancy, at least 100 independent
accepted samples, profit factor at least 1.10, positive test and final holdout results, stress survival,
more than one regime and time window, stable neighboring parameters, and calibrated execution inputs.
Otherwise retire that version and remain halted.
