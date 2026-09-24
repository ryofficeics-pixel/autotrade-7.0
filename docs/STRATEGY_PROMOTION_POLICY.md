# Strategy promotion policy

Runtime display statuses remain BASELINE, SHADOW, CANDIDATE, CHAMPION, and REJECTED. The durable
research lifecycle uses EXPERIMENTAL, OBSERVING, VALIDATED, PAPER_ELIGIBLE, PAPER_ACTIVE,
QUARANTINED, and RETIRED. V3 remains execution-disabled shadow evidence.

Promotion requires reproducible, cost-aware, chronological out-of-sample evidence across symbols and regimes, with positive net expectancy, profit factor above one, acceptable drawdown, sufficient independent samples, calibration quality, and a cost stress test. Dirty-tree evidence is not eligible for promotion.

No status permits live trading.

Promotion is fail-closed and append-only. A challenger must pass minimum sample, positive gross and
net expectancy, profit-factor, drawdown, chronological test, sealed final holdout, stressed execution,
temporal/regime coverage, parameter stability, data integrity, and execution-model validation. A
positive replay does not activate it. The only allowed path is a manual, attributed transition from
VALIDATED to PAPER_ELIGIBLE, followed by a second manual transition to PAPER_ACTIVE. There is no
automatic transition, no restart reset, and no LIVE lifecycle state.

REST Momentum V2 is retired after `STRATEGY_EVIDENCE_FAILED`. Raw KAMA is quarantined pending a new
premise. Filtered KAMA remains observing; Entry V3, fair value, and combined challengers remain
experimental until complete evidence exists. See `RESEARCH_ENGINE.md`.

Wide-crypto challengers also fail promotion when point-in-time universe history, historical
executable BBO, historical funding, or calibrated fill evidence is unavailable. CPCV, a positive
neighbor, or a result better than the retired baseline cannot waive those blockers. All three new
families begin and remain `EXPERIMENTAL` unless a later manual evidence review proves every existing
gate.
