from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from autotrade.analytics import (
    aggregate_trades,
    chronological_split,
    evaluate_outcomes,
    load_jsonl,
    reconstruct_trades,
)


def build_report(
    ledger: Path,
    *,
    maximum_sequence: int | None,
    utc_day: str | None,
    active_hours: Decimal | None,
) -> dict[str, object]:
    events = load_jsonl(ledger)
    if maximum_sequence is not None:
        events = [
            event for event in events if int(str(event.get("sequence", 0))) <= maximum_sequence
        ]
    trades = reconstruct_trades(events)
    if utc_day is not None:
        trades = [trade for trade in trades if str(trade["closed_at"]).startswith(utc_day)]
    normal = [trade for trade in trades if trade["classification"] == "NORMAL"]
    non_recovery = [trade for trade in trades if trade["classification"] != "OUTAGE_HELD"]
    observed = aggregate_trades(
        trades,
        starting_equity=Decimal("300"),
        active_hours=active_hours,
    )
    matrix: dict[str, object] = {}
    if len(normal) >= 5:
        split = chronological_split(len(normal))
        matrix["BASELINE"] = {
            "selection": evaluate_outcomes([normal[index] for index in split.selection]),
            "validation": evaluate_outcomes([normal[index] for index in split.validation]),
            "holdout": evaluate_outcomes([normal[index] for index in split.holdout]),
            "holdout_doubled_cost": evaluate_outcomes(
                [normal[index] for index in split.holdout], doubled_costs=True
            ),
        }
    unavailable = {
        "status": "INSUFFICIENT EVIDENCE",
        "reason": (
            "Historical entries lack the candidate sets and continuous market path needed "
            "for a causal alternative outcome. The new instrumentation applies prospectively."
        ),
    }
    for name in (
        "REENTRY_CHURN_ONLY",
        "RISK_NORMALIZED_ONLY",
        "PARTIAL_RUNNER_ONLY",
        "EDGE_DECAY_ONLY",
        "COMBINED_CANDIDATE",
    ):
        matrix[name] = unavailable
    return {
        "ledger": str(ledger),
        "maximum_sequence": maximum_sequence,
        "utc_day": utc_day,
        "closed_trades": len(trades),
        "normal_strategy_trades": len(normal),
        "non_recovery_closes": len(non_recovery),
        "analytics": observed,
        "experiment_matrix": matrix,
        "promotion_verdict": "INSUFFICIENT EVIDENCE",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild ledger-derived PAPER profitability evidence"
    )
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--max-sequence", type=int)
    parser.add_argument("--utc-day")
    parser.add_argument("--active-hours", type=Decimal)
    args = parser.parse_args()
    print(
        json.dumps(
            build_report(
                args.ledger,
                maximum_sequence=args.max_sequence,
                utc_day=args.utc_day,
                active_hours=args.active_hours,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
