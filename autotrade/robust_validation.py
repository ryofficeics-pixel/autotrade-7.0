from __future__ import annotations

import hashlib
import itertools
import json
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from autotrade.wide_crypto_strategy import SimulatedTrade, trade_metrics


@dataclass(frozen=True)
class ChronologicalStages:
    selection_end: int
    validation_end: int
    test_end: int
    holdout_end: int

    def record(self) -> dict[str, object]:
        return {
            "method": "CHRONOLOGICAL_50_20_15_15",
            "shuffled": False,
            "selection": _window(None, self.selection_end),
            "validation": _window(self.selection_end + 1, self.validation_end),
            "test": _window(self.validation_end + 1, self.test_end),
            "final_holdout": {
                **_window(self.test_end + 1, self.holdout_end),
                "sealed": True,
            },
        }


def _iso(timestamp: int | None) -> str | None:
    return (
        datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")
        if timestamp is not None
        else None
    )


def _window(start: int | None, end: int) -> dict[str, object]:
    return {"start": _iso(start), "end": _iso(end), "start_timestamp": start, "end_timestamp": end}


def chronological_stages(timestamps: Sequence[int]) -> ChronologicalStages:
    ordered = sorted(set(timestamps))
    if len(ordered) < 20:
        raise ValueError("at least 20 timestamps are required for chronological validation")
    boundaries = [int(len(ordered) * fraction) for fraction in (0.50, 0.70, 0.85, 1.0)]
    return ChronologicalStages(
        selection_end=ordered[max(0, boundaries[0] - 1)],
        validation_end=ordered[max(0, boundaries[1] - 1)],
        test_end=ordered[max(0, boundaries[2] - 1)],
        holdout_end=ordered[-1],
    )


def stage_trade_metrics(
    trades: Sequence[SimulatedTrade], stages: ChronologicalStages, *, total_hours: int
) -> dict[str, object]:
    windows = {
        "SELECTION": (0, stages.selection_end),
        "VALIDATION": (stages.selection_end + 1, stages.validation_end),
        "TEST": (stages.validation_end + 1, stages.test_end),
        "FINAL_HOLDOUT": (stages.test_end + 1, stages.holdout_end),
    }
    result: dict[str, object] = {}
    for name, (start, end) in windows.items():
        subset = [trade for trade in trades if start <= trade.entry_timestamp <= end]
        result[name] = trade_metrics(subset, total_hours=total_hours)
    return result


def write_holdout_seal(
    experiment_root: Path,
    *,
    dataset_id: str,
    dataset_hash: str,
    strategy_identities: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    identity = {
        "dataset_id": dataset_id,
        "dataset_hash": dataset_hash,
        "strategy_identities": list(strategy_identities),
        "policy": "ONE_BATCH_ACCESS_AFTER_CODE_AND_PARAMETERS_FROZEN",
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    seal: dict[str, object] = {
        **identity,
        "sealed_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "seal_hash": hashlib.sha256(encoded).hexdigest(),
    }
    path = experiment_root / "wide-crypto-final-holdout-seal.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or any(
            existing.get(key) != value for key, value in identity.items()
        ):
            raise ValueError("wide-crypto holdout was sealed to a different identity")
        return cast(dict[str, object], existing)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(seal, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return seal


def _exclude_boundary_leakage(
    trades: Sequence[SimulatedTrade], boundaries: Sequence[int], gap_seconds: int
) -> list[SimulatedTrade]:
    return [
        trade
        for trade in trades
        if all(
            not (
                boundary - gap_seconds <= trade.entry_timestamp <= boundary + gap_seconds
                or trade.entry_timestamp < boundary < trade.exit_timestamp + gap_seconds
            )
            for boundary in boundaries
        )
    ]


def cpcv(
    trades: Sequence[SimulatedTrade],
    timestamps: Sequence[int],
    *,
    groups: int,
    test_groups: int,
    purge_hours: int,
    embargo_hours: int,
) -> dict[str, object]:
    ordered = sorted(set(timestamps))
    if groups < 4 or test_groups <= 0 or test_groups >= groups or len(ordered) < groups:
        raise ValueError("invalid CPCV configuration")
    group_bounds: list[tuple[int, int]] = []
    for group in range(groups):
        start_index = len(ordered) * group // groups
        stop_index = len(ordered) * (group + 1) // groups
        group_bounds.append((ordered[start_index], ordered[stop_index - 1]))
    gap_seconds = max(purge_hours, embargo_hours) * 3600
    boundaries = [start for start, _ in group_bounds[1:]]
    clean_trades = _exclude_boundary_leakage(trades, boundaries, gap_seconds)
    paths: list[dict[str, object]] = []
    for path_id, selected in enumerate(itertools.combinations(range(groups), test_groups), 1):
        subset = [
            trade
            for trade in clean_trades
            if any(
                group_bounds[group][0] <= trade.entry_timestamp <= group_bounds[group][1]
                for group in selected
            )
        ]
        hours = sum(
            (group_bounds[group][1] - group_bounds[group][0]) // 3600 + 1 for group in selected
        )
        metrics = trade_metrics(subset, total_hours=hours)
        paths.append({"path": path_id, "test_groups": list(selected), "metrics": metrics})
    returns = [
        float(str(cast(dict[str, object], path["metrics"])["net_pnl_bps"])) for path in paths
    ]
    expectancy_values = [
        cast(dict[str, object], path["metrics"])["net_expectancy_bps"] for path in paths
    ]
    expectancies = [float(str(value)) for value in expectancy_values if value is not None]
    profit_factor_values = [
        cast(dict[str, object], path["metrics"])["profit_factor"] for path in paths
    ]
    profit_factors = [float(str(value)) for value in profit_factor_values if value is not None]
    best = max(
        paths,
        key=lambda path: float(str(cast(dict[str, object], path["metrics"])["net_pnl_bps"])),
    )
    worst = min(
        paths,
        key=lambda path: float(str(cast(dict[str, object], path["metrics"])["net_pnl_bps"])),
    )
    return {
        "method": "FIXED_PARAMETER_COMBINATORIAL_PURGED_CROSS_VALIDATION",
        "groups": groups,
        "test_groups_per_split": test_groups,
        "number_of_paths": len(paths),
        "contiguous_groups": True,
        "purge_hours": purge_hours,
        "embargo_hours": embargo_hours,
        "one_bar_execution_lag": True,
        "paths": paths,
        "median_oos_return_bps": statistics.median(returns) if returns else None,
        "median_oos_expectancy_bps": statistics.median(expectancies) if expectancies else None,
        "median_oos_profit_factor": statistics.median(profit_factors) if profit_factors else None,
        "path_return_dispersion_bps": statistics.pstdev(returns) if len(returns) > 1 else None,
        "positive_path_fraction": sum(value > 0 for value in returns) / len(returns)
        if returns
        else None,
        "best_path": best,
        "worst_path": worst,
        "limitation": (
            "PATHS_OVERLAP_AND_ARE_NOT_INDEPENDENT; NO_PARAMETER_OPTIMIZATION_INSIDE_FOLDS"
        ),
    }


def parameter_stability(variant_metrics: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    ordered = sorted(variant_metrics.items(), key=lambda item: float(item[0]))
    rows = [
        {
            "edge_cost_ratio": threshold,
            "sample_size": metrics.get("sample_size"),
            "net_expectancy_bps": metrics.get("net_expectancy_bps"),
            "profit_factor": metrics.get("profit_factor"),
            "net_pnl_bps": metrics.get("net_pnl_bps"),
        }
        for threshold, metrics in ordered
    ]
    usable = [row for row in rows if row["sample_size"] and row["net_expectancy_bps"] is not None]
    all_positive = bool(usable) and all(float(str(row["net_expectancy_bps"])) > 0 for row in usable)
    return {
        "status": "STABLE" if len(usable) == len(rows) and all_positive else "NOT_PROVEN",
        "neighbors": rows,
        "selection_policy": "PRE_REGISTERED_3.0; NEIGHBORS_ARE_DIAGNOSTIC_ONLY",
    }


def synthetic_null_test(
    trades: Sequence[SimulatedTrade], *, seed: int = 7, trials: int = 1000
) -> dict[str, object]:
    by_day: dict[str, float] = {}
    for trade in trades:
        day = datetime.fromtimestamp(trade.entry_timestamp, UTC).date().isoformat()
        by_day[day] = by_day.get(day, 0.0) + float(trade.net_pnl_bps)
    values = list(by_day.values())
    if len(values) < 20:
        return {"status": "UNAVAILABLE", "reason": "FEWER_THAN_20_TRADING_DAYS"}
    observed = statistics.fmean(values)
    rng = random.Random(seed)
    null_means = [
        statistics.fmean(value * (1 if rng.random() >= 0.5 else -1) for value in values)
        for _ in range(trials)
    ]
    return {
        "status": "AVAILABLE",
        "method": "DETERMINISTIC_DAILY_SIGN_FLIP_NULL",
        "days": len(values),
        "trials": trials,
        "observed_mean_daily_bps": observed,
        "one_sided_p_value": (1 + sum(value >= observed for value in null_means)) / (trials + 1),
        "limitation": "NOT_A_SUBSTITUTE_FOR_AN_EXECUTABLE_FILL_BOOTSTRAP",
    }


def overfitting_audit(
    trades: Sequence[SimulatedTrade],
    *,
    experiment_count: int,
    family_count: int,
    parameter_variant_count: int,
    stability: Mapping[str, object],
) -> dict[str, object]:
    return {
        "experiments_run": experiment_count,
        "families_tested": family_count,
        "parameter_variants_tested": parameter_variant_count,
        "holdout_access_count": 1,
        "deflated_sharpe_ratio": {
            "status": "UNAVAILABLE",
            "reason": "TRADE_RETURNS_ARE_SPARSE_AND_NON_IID; NO_VALIDATED_RETURN_FREQUENCY MODEL",
        },
        "probability_of_backtest_overfitting": {
            "status": "UNAVAILABLE",
            "reason": "ONLY_FOUR_PRE_REGISTERED_NEIGHBORS; PBO WOULD_BE UNSTABLE",
        },
        "whites_reality_check": {
            "status": "UNAVAILABLE",
            "reason": "STATIONARY_BOOTSTRAP_BLOCK_LENGTH_NOT_CALIBRATED",
        },
        "synthetic_null": synthetic_null_test(trades),
        "parameter_neighborhood": dict(stability),
        "temporal_stability": "REPORTED_IN_CHRONOLOGICAL_STAGES_AND_CPCV",
        "regime_stability": "REPORTED_IN_SEGMENTED_METRICS",
    }


def promotion_gate(
    metrics: Mapping[str, object],
    stages: Mapping[str, Mapping[str, object]],
    stressed: Mapping[str, object],
    stability: Mapping[str, object],
    *,
    minimum_trades: int,
    minimum_profit_factor: float,
) -> dict[str, object]:
    reasons: list[str] = []
    if int(str(metrics.get("sample_size", 0) or 0)) < minimum_trades:
        reasons.append("INSUFFICIENT_INDEPENDENT_SAMPLE")
    if float(str(metrics.get("gross_expectancy_bps") or 0)) <= 0:
        reasons.append("NON_POSITIVE_GROSS_EXPECTANCY")
    if float(str(metrics.get("net_expectancy_bps") or 0)) <= 0:
        reasons.append("NON_POSITIVE_NET_EXPECTANCY")
    if float(str(metrics.get("profit_factor") or 0)) < minimum_profit_factor:
        reasons.append("PROFIT_FACTOR_BELOW_FLOOR")
    for name in ("VALIDATION", "TEST", "FINAL_HOLDOUT"):
        stage = stages[name]
        if int(str(stage.get("sample_size", 0) or 0)) == 0:
            reasons.append(f"{name}_INSUFFICIENT_EVIDENCE")
        elif float(str(stage.get("net_expectancy_bps") or 0)) <= 0:
            reasons.append(f"{name}_FAILED")
    if float(str(stressed.get("net_expectancy_bps") or 0)) <= 0:
        reasons.append("STRESSED_EXECUTION_FAILED")
    if stability.get("status") != "STABLE":
        reasons.append("PARAMETER_STABILITY_NOT_PROVEN")
    reasons.extend(
        [
            "EXECUTION_MODEL_NOT_VALIDATED",
            "HISTORICAL_BBO_UNAVAILABLE",
            "HISTORICAL_FUNDING_UNAVAILABLE",
            "SURVIVORSHIP_BIAS_PRESENT",
        ]
    )
    return {"promotion_eligible": not reasons, "reasons": reasons}
