from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from autotrade.research import (
    AppendOnlyRegistry,
    LifecycleRegistry,
    load_research_settings,
    write_json,
)
from autotrade.robust_validation import (
    ChronologicalStages,
    chronological_stages,
    cpcv,
    overfitting_audit,
    parameter_stability,
    promotion_gate,
    stage_trade_metrics,
    write_holdout_seal,
)
from autotrade.wide_crypto_data import (
    Candle,
    freeze_wide_crypto_dataset,
    load_candles,
    load_wide_config,
    verify_wide_crypto_dataset,
)
from autotrade.wide_crypto_strategy import (
    ResearchCandidate,
    SimulatedTrade,
    StrategyFamily,
    WideStrategySettings,
    prepare_btc_regimes,
    prepare_feature_timeline,
    replay_family,
    segmented_metrics,
    trade_metrics,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str
    ).encode()


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _settings(config: Mapping[str, object]) -> WideStrategySettings:
    strategy = cast(dict[str, object], config["strategy"])
    dataset = cast(dict[str, object], config["dataset"])
    horizons = cast(list[object], strategy["momentum_hours"])
    weights = cast(list[object], strategy["momentum_weights"])
    return WideStrategySettings(
        momentum_hours=cast(tuple[int, int, int], tuple(int(str(value)) for value in horizons)),
        momentum_weights=cast(
            tuple[Decimal, Decimal, Decimal], tuple(Decimal(str(value)) for value in weights)
        ),
        tail_fraction=Decimal(str(strategy["tail_fraction"])),
        donchian_hours=int(str(strategy["donchian_hours"])),
        atr_hours=int(str(strategy["atr_hours"])),
        volatility_baseline_hours=int(str(strategy["volatility_baseline_hours"])),
        volume_baseline_hours=int(str(strategy["volume_baseline_hours"])),
        minimum_volume_ratio=Decimal(str(strategy["minimum_volume_ratio"])),
        minimum_volatility_ratio=Decimal(str(strategy["minimum_volatility_ratio"])),
        maximum_breakout_extension_atr=Decimal(str(strategy["maximum_breakout_extension_atr"])),
        edge_cost_ratio=Decimal(str(strategy["edge_cost_ratio"])),
        stop_atr=Decimal(str(strategy["stop_atr"])),
        profit_atr=Decimal(str(strategy["profit_atr"])),
        trail_activation_r=Decimal(str(strategy["trail_activation_r"])),
        trail_atr=Decimal(str(strategy["trail_atr"])),
        time_stop_hours=int(str(strategy["time_stop_hours"])),
        btc_ema_fast_4h=int(str(strategy["btc_ema_fast_4h"])),
        btc_ema_slow_4h=int(str(strategy["btc_ema_slow_4h"])),
        btc_slope_periods_4h=int(str(strategy["btc_slope_periods_4h"])),
        minimum_universe_size=int(str(strategy["minimum_universe_size"])),
        minimum_rolling_24h_quote_volume_usdt=Decimal(
            str(dataset["minimum_rolling_24h_quote_volume_usdt"])
        ),
    )


def _universe(dataset_root: Path) -> dict[str, object]:
    raw = json.loads((dataset_root / "universe.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("wide-crypto universe is invalid")
    return cast(dict[str, object], raw)


def _spreads(universe: Mapping[str, object]) -> dict[str, Decimal]:
    selected = cast(list[dict[str, object]], universe["selected"])
    return {
        str(row["symbol"]): Decimal(str(row["snapshot_spread_bps"]))
        for row in selected
        if row.get("snapshot_spread_bps") is not None
    }


def _write_hash_chain(path: Path, rows: Sequence[Mapping[str, object]], event_type: str) -> None:
    previous = "0" * 64
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        for sequence, payload in enumerate(rows, 1):
            envelope: dict[str, object] = {
                "schema_version": 1,
                "sequence": sequence,
                "event_type": event_type,
                "previous_hash": previous,
                "payload": dict(payload),
            }
            previous = hashlib.sha256(previous.encode() + _canonical(envelope)).hexdigest()
            envelope["record_hash"] = previous
            stream.write(_canonical(envelope) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _counterfactuals(
    candidates: Sequence[ResearchCandidate], candles_by_symbol: Mapping[str, Sequence[Candle]]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    records: list[dict[str, object]] = []
    counts = {"AVOIDED_LOSS": 0, "MISSED_OPPORTUNITY": 0, "NEUTRAL": 0, "OBSERVED": 0}
    values: list[float] = []
    indices = {
        symbol: {row.timestamp: index for index, row in enumerate(rows)}
        for symbol, rows in candles_by_symbol.items()
    }
    for candidate in candidates:
        classification = "OBSERVED" if candidate.decision == "ACCEPTED" else "NEUTRAL"
        outcome: float | None = None
        index = indices[candidate.symbol].get(candidate.timestamp)
        rows = candles_by_symbol[candidate.symbol]
        if index is not None and index + 18 < len(rows):
            start = rows[index + 1].open
            end = rows[index + 18].close
            direction = Decimal(1) if candidate.direction == "LONG" else Decimal(-1)
            net = direction * (end / start - Decimal(1)) * Decimal(10_000)
            net -= candidate.execution_cost_bps
            outcome = float(net)
            if candidate.decision != "ACCEPTED":
                classification = (
                    "MISSED_OPPORTUNITY" if net > 5 else "AVOIDED_LOSS" if net < -5 else "NEUTRAL"
                )
                values.append(outcome)
        counts[classification] += 1
        records.append(
            {
                "candidate_id": candidate.candidate_id,
                "strategy_id": candidate.strategy_id,
                "classification": classification,
                "horizon_hours": 18,
                "modeled_net_bps": outcome,
                "independence": "OVERLAPPING_CANDIDATE_OUTCOME; NOT_A_TRADE",
            }
        )
    summary = {
        "candidate_count": len(records),
        "rejected_resolved_count": len(values),
        "classifications": counts,
        "average_rejected_modeled_net_bps": statistics.fmean(values) if values else None,
        "warning": "OVERLAPPING_CANDIDATES_ARE_NOT_INDEPENDENT_TRADES",
    }
    return records, summary


def _gate_funnel(candidates: Sequence[ResearchCandidate]) -> dict[str, object]:
    reasons: dict[str, int] = {}
    for candidate in candidates:
        if candidate.first_rejection_reason:
            reasons[candidate.first_rejection_reason] = (
                reasons.get(candidate.first_rejection_reason, 0) + 1
            )
    return {
        "generated": len(candidates),
        "accepted": sum(candidate.decision == "ACCEPTED" for candidate in candidates),
        "rejected": sum(candidate.decision != "ACCEPTED" for candidate in candidates),
        "rejection_reasons": dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
        "binding_gates": [
            {"gate": name, "reject_count": count}
            for name, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def _register_once(registry: AppendOnlyRegistry, payload: Mapping[str, object]) -> None:
    experiment_id = payload.get("experiment_id")
    for record in registry.records():
        existing = record.get("payload")
        if isinstance(existing, dict) and existing.get("experiment_id") == experiment_id:
            return
    registry.append("wide_crypto_experiment", payload)


def _lifecycle_once(lifecycle: LifecycleRegistry, strategy_id: str, git_commit: str) -> None:
    key = f"{strategy_id}:1"
    if key in lifecycle.latest():
        return
    lifecycle.store.append(
        "strategy_registered",
        {
            "strategy_id": strategy_id,
            "strategy_version": "1",
            "state": "EXPERIMENTAL",
            "reason": "RESEARCH_ONLY; NO_RUNTIME_EXECUTION_AUTHORITY",
            "git_commit": git_commit,
        },
    )


def _experiment_result(
    family: StrategyFamily,
    candidates: Sequence[ResearchCandidate],
    trades: Sequence[SimulatedTrade],
    stressed_trades: Sequence[SimulatedTrade],
    *,
    total_hours: int,
    stages: ChronologicalStages,
    timeline: Sequence[int],
    stability: Mapping[str, object],
    validation_config: Mapping[str, object],
    dataset_id: str,
    config_hash: str,
) -> dict[str, object]:
    metrics = trade_metrics(trades, total_hours=total_hours)
    stages_metrics = cast(
        dict[str, dict[str, object]], stage_trade_metrics(trades, stages, total_hours=total_hours)
    )
    stress = trade_metrics(stressed_trades, total_hours=total_hours)
    cpcv_result = cpcv(
        [trade for trade in trades if trade.entry_timestamp <= stages.test_end],
        [timestamp for timestamp in timeline if timestamp <= stages.test_end],
        groups=int(str(validation_config["cpcv_groups"])),
        test_groups=int(str(validation_config["cpcv_test_groups"])),
        purge_hours=int(str(validation_config["purge_hours"])),
        embargo_hours=int(str(validation_config["embargo_hours"])),
    )
    promotion = promotion_gate(
        metrics,
        stages_metrics,
        stress,
        stability,
        minimum_trades=int(str(validation_config["minimum_promotion_trades"])),
        minimum_profit_factor=float(str(validation_config["minimum_profit_factor"])),
    )
    experiment_identity = {
        "dataset_id": dataset_id,
        "strategy_id": family.value,
        "strategy_version": "1",
        "parameter_version": "BASE_V1_EDGE_COST_3.0",
        "config_hash": config_hash,
        "execution_profile": "BASELINE_V1",
        "seed": 7,
    }
    return {
        **experiment_identity,
        "experiment_id": _hash(experiment_identity)[:32],
        "comparable": "COMPARABLE",
        "comparability_reason": "SAME_DATASET_TIMELINE_AND_EXECUTION_ASSUMPTIONS",
        "metrics": metrics,
        "segments": segmented_metrics(trades, total_hours=total_hours),
        "chronological": stages_metrics,
        "cpcv": cpcv_result,
        "stressed": stress,
        "parameter_stability": dict(stability),
        "promotion": promotion,
        "candidate_funnel": _gate_funnel(candidates),
    }


def _strategy_row(result: Mapping[str, object]) -> dict[str, object]:
    metrics = cast(dict[str, object], result["metrics"])
    chronological = cast(dict[str, dict[str, object]], result["chronological"])
    stressed = cast(dict[str, object], result["stressed"])
    promotion = cast(dict[str, object], result["promotion"])
    stability = cast(dict[str, object], result["parameter_stability"])
    return {
        "strategy": result["strategy_id"],
        "version": "1",
        "lifecycle_state": "EXPERIMENTAL",
        "comparable": result["comparable"],
        "experiment_id": result["experiment_id"],
        "sample": metrics["sample_size"],
        "accepted_trades": metrics["sample_size"],
        "gross_expectancy_bps": metrics["gross_expectancy_bps"],
        "net_expectancy_bps": metrics["net_expectancy_bps"],
        "profit_factor": metrics["profit_factor"],
        "maximum_drawdown_bps": metrics["maximum_drawdown_bps"],
        "turnover": metrics["turnover_roundtrips"],
        "edge_cost_ratio": metrics["average_edge_cost_ratio"],
        "validation_result": chronological["VALIDATION"]["net_expectancy_bps"],
        "test_result": chronological["TEST"]["net_expectancy_bps"],
        "final_holdout_result": chronological["FINAL_HOLDOUT"]["net_expectancy_bps"],
        "stress_result": stressed,
        "stressed_net_expectancy_bps": stressed["net_expectancy_bps"],
        "parameter_stability": stability["status"],
        "promotion_eligible": promotion["promotion_eligible"],
        "reason_blocked": promotion["reasons"],
    }


def run_full(
    project_root: Path,
    *,
    dataset_only: bool = False,
    dataset_root: Path | None = None,
) -> dict[str, object]:
    config_path = project_root / "config" / "wide_crypto_research.toml"
    config = load_wide_config(config_path)
    if dataset_root is None:
        manifest = freeze_wide_crypto_dataset(project_root, config_path=config_path)
        dataset_root = project_root / "research" / "datasets" / str(manifest["dataset_id"])
    else:
        dataset_root = dataset_root.resolve()
    manifest = verify_wide_crypto_dataset(dataset_root)
    if dataset_only:
        return manifest
    one_hour = load_candles(dataset_root, "1h")
    four_hour = load_candles(dataset_root, "4h")
    universe = _universe(dataset_root)
    snapshot_spreads = _spreads(universe)
    settings = _settings(config)
    validation_config = cast(dict[str, object], config["validation"])
    research_settings = load_research_settings(project_root / "config" / "research.toml")
    baseline_profile = research_settings.execution_profiles["BASELINE"]
    stressed_profile = research_settings.execution_profiles["STRESSED"]
    timeline = [row.timestamp for row in one_hour["BTC_USDT"]]
    prepared_features = prepare_feature_timeline(one_hour, timeline, settings)
    prepared_regimes = prepare_btc_regimes(four_hour["BTC_USDT"], timeline, settings)
    stages = chronological_stages(timeline)
    total_hours = max(1, (timeline[-1] - timeline[0]) // 3600 + 1)
    config_hash = _file_hash(config_path)

    strategy = cast(dict[str, object], config["strategy"])
    neighbor_thresholds = [
        Decimal(str(value)) for value in cast(list[object], strategy["edge_cost_neighbors"])
    ]
    variant_metrics: dict[str, Mapping[str, object]] = {}
    for threshold in neighbor_thresholds:
        variant_settings = replace(settings, edge_cost_ratio=threshold)
        _, trades, _ = replay_family(
            StrategyFamily.CROSS_SECTIONAL_BREAKOUT_V1,
            one_hour,
            four_hour["BTC_USDT"],
            dataset_id=str(manifest["dataset_id"]),
            config_hash=config_hash,
            parameter_version=f"EDGE_COST_{threshold}",
            profile=baseline_profile,
            snapshot_spreads=snapshot_spreads,
            settings=variant_settings,
            end_timestamp=stages.test_end,
            prepared_features=prepared_features,
            prepared_regimes=prepared_regimes,
        )
        variant_metrics[str(threshold)] = trade_metrics(
            trades, total_hours=max(1, (stages.test_end - timeline[0]) // 3600 + 1)
        )
    stability = parameter_stability(variant_metrics)
    strategy_identities = [
        {
            "strategy_id": family.value,
            "strategy_version": "1",
            "parameter_version": "BASE_V1_EDGE_COST_3.0",
            "config_hash": config_hash,
        }
        for family in StrategyFamily
    ]
    holdout_seal = write_holdout_seal(
        project_root
        / "research"
        / "experiments"
        / "holdout-seals"
        / str(manifest["dataset_id"]),
        dataset_id=str(manifest["dataset_id"]),
        dataset_hash=str(manifest["identity_hash"]),
        strategy_identities=strategy_identities,
    )

    results: list[dict[str, object]] = []
    candidate_sets: dict[str, list[ResearchCandidate]] = {}
    trade_sets: dict[str, list[SimulatedTrade]] = {}
    counterfactual_summaries: dict[str, object] = {}
    registry = AppendOnlyRegistry(project_root / "research" / "registry.jsonl")
    lifecycle = LifecycleRegistry(project_root / "research")
    lifecycle.initialize(git_commit=str(manifest["code_commit"]))
    for family in StrategyFamily:
        candidates, trades, universe_rejections = replay_family(
            family,
            one_hour,
            four_hour["BTC_USDT"],
            dataset_id=str(manifest["dataset_id"]),
            config_hash=config_hash,
            parameter_version="BASE_V1_EDGE_COST_3.0",
            profile=baseline_profile,
            snapshot_spreads=snapshot_spreads,
            settings=settings,
            prepared_features=prepared_features,
            prepared_regimes=prepared_regimes,
        )
        _, stressed_trades, _ = replay_family(
            family,
            one_hour,
            four_hour["BTC_USDT"],
            dataset_id=str(manifest["dataset_id"]),
            config_hash=config_hash,
            parameter_version="BASE_V1_EDGE_COST_3.0",
            profile=stressed_profile,
            snapshot_spreads=snapshot_spreads,
            settings=settings,
            prepared_features=prepared_features,
            prepared_regimes=prepared_regimes,
        )
        result = _experiment_result(
            family,
            candidates,
            trades,
            stressed_trades,
            total_hours=total_hours,
            stages=stages,
            timeline=timeline,
            stability=stability
            if family == StrategyFamily.CROSS_SECTIONAL_BREAKOUT_V1
            else {"status": "NOT_TESTED", "neighbors": []},
            validation_config=validation_config,
            dataset_id=str(manifest["dataset_id"]),
            config_hash=config_hash,
        )
        result["universe_rejections"] = universe_rejections
        experiment_root = project_root / "research" / "experiments" / str(result["experiment_id"])
        if not experiment_root.exists():
            experiment_root.mkdir(parents=True)
            _write_hash_chain(
                experiment_root / "candidates.jsonl",
                [candidate.record() for candidate in candidates],
                "candidate",
            )
            _write_hash_chain(
                experiment_root / "trades.jsonl",
                [trade.record() for trade in trades],
                "simulated_trade",
            )
            counterfactual_rows, counterfactual_summary = _counterfactuals(candidates, one_hour)
            _write_hash_chain(
                experiment_root / "counterfactuals.jsonl",
                counterfactual_rows,
                "candidate_counterfactual",
            )
            result["counterfactual"] = counterfactual_summary
            write_json(experiment_root / "result.json", result)
        else:
            _, counterfactual_summary = _counterfactuals(candidates, one_hour)
            result["counterfactual"] = counterfactual_summary
        _register_once(
            registry,
            {
                "experiment_id": result["experiment_id"],
                "strategy_id": family.value,
                "strategy_version": "1",
                "parameter_version": "BASE_V1_EDGE_COST_3.0",
                "dataset_id": manifest["dataset_id"],
                "code_commit": manifest["code_commit"],
                "config_hash": config_hash,
                "execution_profile": "BASELINE_V1",
                "seed": 7,
                "reason": "PRE_REGISTERED_BASE_FAMILY_TEST",
                "promotion_eligible": cast(dict[str, object], result["promotion"])[
                    "promotion_eligible"
                ],
            },
        )
        _lifecycle_once(lifecycle, family.value, str(manifest["code_commit"]))
        results.append(result)
        candidate_sets[family.value] = candidates
        trade_sets[family.value] = trades
        counterfactual_summaries[family.value] = result["counterfactual"]

    experiment_count = len(StrategyFamily) + len(neighbor_thresholds)
    combined_trades = trade_sets[StrategyFamily.CROSS_SECTIONAL_BREAKOUT_V1.value]
    audit = overfitting_audit(
        combined_trades,
        experiment_count=experiment_count,
        family_count=len(StrategyFamily),
        parameter_variant_count=len(neighbor_thresholds),
        stability=stability,
    )
    prior_path = project_root / "reports" / "research-latest.json"
    prior: dict[str, object] = {}
    if prior_path.exists():
        loaded = json.loads(prior_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            prior = cast(dict[str, object], loaded)
    new_rows = [_strategy_row(result) for result in results]
    prior_rows = cast(list[dict[str, object]], prior.get("strategy_lab", []))
    unsupported_rows = cast(list[dict[str, object]], prior.get("unsupported_strategies", []))
    tournament = [*prior_rows, *unsupported_rows, *new_rows]
    latest_candidates = sorted(
        candidate_sets[StrategyFamily.CROSS_SECTIONAL_BREAKOUT_V1.value],
        key=lambda item: (item.timestamp, item.candidate_quality_score),
        reverse=True,
    )[:50]
    report: dict[str, object] = {
        **prior,
        "report_schema_version": 2,
        "generated_at": _utc_now(),
        "dataset": manifest,
        "wide_crypto_dataset": manifest,
        "experiment_identity": {
            "dataset_id": manifest["dataset_id"],
            "dataset_hash": manifest["identity_hash"],
            "git_commit": manifest["code_commit"],
            "config_hash": config_hash,
            "execution_profile": "BASELINE_V1",
            "experiment_ids": [result["experiment_id"] for result in results],
        },
        "strategy_lab": [*prior_rows, *new_rows],
        "strategy_tournament": tournament,
        "wide_crypto_experiments": results,
        "candidate_funnels": {
            **cast(dict[str, object], prior.get("candidate_funnels", {})),
            **{str(result["strategy_id"]): result["candidate_funnel"] for result in results},
        },
        "no_trade_value": {
            **cast(dict[str, object], prior.get("no_trade_value", {})),
            **counterfactual_summaries,
        },
        "universe": universe,
        "top_candidates": [candidate.record() for candidate in latest_candidates],
        "overfitting_audit": audit,
        "cpcv": {result["strategy_id"]: result["cpcv"] for result in results},
        "validation_plan": stages.record(),
        "final_holdout_seal": holdout_seal,
        "experiment_budget": {
            "experiments_run": experiment_count,
            "families_tested": len(StrategyFamily),
            "parameter_variants_tested": len(neighbor_thresholds),
            "holdout_access_count": 1,
        },
        "funding_carry_arbitrage": {
            "strategy_id": "FUNDING_CARRY_ARBITRAGE_V1",
            "status": "SCAFFOLD_ONLY",
            "execution_enabled": False,
            "blockers": [
                "TWO_LEG_LIFECYCLE_UNAVAILABLE",
                "HEDGE_RECONCILIATION_UNAVAILABLE",
                "PARTIAL_FILL_RECOVERY_UNAVAILABLE",
                "FUNDING_SETTLEMENT_VERIFICATION_UNAVAILABLE",
            ],
        },
        "lifecycle": lifecycle.latest(),
        "paper_eligible_strategies": [
            row["strategy"] for row in new_rows if row["promotion_eligible"] is True
        ],
        "automatic_promotion": False,
        "paper_activation": "EXPLICIT_OPERATOR_APPROVAL_REQUIRED",
        "live_trading": "UNAVAILABLE",
        "current_execution_authority_changed": False,
    }
    write_json(prior_path, report, replace_existing=True)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AUTOTRADE wide-crypto offline research")
    parser.add_argument("command", choices=("full", "freeze", "verify"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--dataset", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.project_root.resolve()
    if args.command == "full":
        result = run_full(root, dataset_root=args.dataset)
    elif args.command == "freeze":
        result = run_full(root, dataset_only=True)
    else:
        if args.dataset is None:
            raise SystemExit("--dataset is required for verify")
        result = verify_wide_crypto_dataset(args.dataset.resolve())
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
