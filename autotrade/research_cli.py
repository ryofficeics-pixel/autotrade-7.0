from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from autotrade.research import (
    AppendOnlyRegistry,
    LifecycleRegistry,
    LifecycleState,
    SignalCandidate,
    StrategyProtocol,
    analyze_ama_evidence,
    analyze_entry_v3_evidence,
    calibration_diagnostics,
    counterfactual_summary,
    default_strategies,
    entry_funnel,
    evaluate_promotion,
    experiment_identity,
    freeze_xau_dataset,
    load_dataset_events,
    load_research_settings,
    replay_metrics,
    replay_strategy,
    resolve_counterfactuals,
    seal_final_holdout,
    stage_metrics,
    unsupported_strategy_results,
    validation_plan,
    verify_dataset,
    write_candidate_ledger,
    write_counterfactual_ledger,
    write_json,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode()


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return cast(dict[str, object], value)


def _latest_file(root: Path, pattern: str) -> Path | None:
    paths = [path for path in root.glob(pattern) if path.is_file()]
    return max(paths, key=lambda path: path.stat().st_mtime_ns) if paths else None


def _source_paths(project_root: Path) -> tuple[Path, Path | None]:
    ama = _latest_file(project_root / "data" / "runs", "*/ama-control-v2.jsonl")
    if ama is None:
        raise FileNotFoundError("no AMA Control V2 evidence was found")
    entry = _latest_file(
        project_root / "logs" / "entry-v3-captures", "*/entry-v3-events.jsonl"
    )
    return ama, entry


def _mean_cost(candidates: Sequence[SignalCandidate]) -> dict[str, object]:
    costs = [candidate.cost for candidate in candidates if candidate.cost is not None]
    if not costs:
        return {"sample_size": 0}
    fields = (
        "gross_move_bps",
        "spread_cost_bps",
        "fee_cost_bps",
        "slippage_cost_bps",
        "latency_cost_bps",
        "adverse_selection_bps",
        "funding_cost_bps",
        "impact_bps",
        "missed_partial_fill_effect_bps",
        "total_cost_bps",
        "net_edge_bps",
    )
    result: dict[str, object] = {"sample_size": len(costs)}
    for field in fields:
        result[field] = sum(float(getattr(cost, field)) for cost in costs) / len(costs)
    return result


def _strategy_report_markdown(result: Mapping[str, object]) -> str:
    metrics = cast(Mapping[str, object], result["metrics"])
    promotion = cast(Mapping[str, object], result["promotion"])
    lines = [
        f"# {result['strategy_id']} replay",
        "",
        f"- Experiment: `{result['experiment_id']}`",
        f"- Dataset: `{result['dataset_id']}`",
        f"- Execution profile: `{result['execution_profile']}`",
        f"- Candidates: {metrics['candidate_count']}",
        f"- Accepted: {metrics['accepted_count']}",
        f"- Net expectancy: {metrics['net_expectancy_bps']} bps",
        f"- Promotion eligible: {promotion['promotion_eligible']}",
        f"- Blocking reasons: {', '.join(cast(list[str], promotion['reasons']))}",
        "",
        "This is offline research evidence. It grants no order or PAPER activation authority.",
        "",
    ]
    return "\n".join(lines)


def _load_existing_experiment(path: Path) -> dict[str, object]:
    result_path = path / "result.json"
    sidecar = (path / "result.sha256").read_text(encoding="ascii").strip()
    result = _json_object(result_path)
    if _file_hash(result_path) != sidecar:
        raise ValueError(f"experiment result integrity failed: {path.name}")
    if result.get("result_hash") != _sha256(
        {key: value for key, value in result.items() if key != "result_hash"}
    ):
        raise ValueError(f"experiment deterministic result hash failed: {path.name}")
    return result


def _run_experiment(
    project_root: Path,
    dataset_path: Path,
    strategy: StrategyProtocol,
    *,
    seed: int,
) -> dict[str, object]:
    research_root = project_root / "research"
    settings_path = project_root / "config" / "research.toml"
    settings = load_research_settings(settings_path)
    manifest = verify_dataset(dataset_path)
    events = load_dataset_events(dataset_path)
    profile = settings.execution_profiles["BASELINE"]
    identity = experiment_identity(
        dataset_manifest=manifest,
        strategy=strategy,
        profile=profile,
        git_commit=str(manifest["code_commit"]),
        config_hash=_file_hash(settings_path),
        seed=seed,
    )
    experiment_path = research_root / "experiments" / str(identity["experiment_id"])
    if experiment_path.exists():
        return _load_existing_experiment(experiment_path)
    experiment_path.mkdir(parents=True, exist_ok=False)

    validation = validation_plan(events, settings)
    seal_final_holdout(
        experiment_path,
        str(manifest["dataset_id"]),
        strategy.strategy_hash,
        strategy.parameter_hash,
    )
    candidates = replay_strategy(events, strategy, profile)
    outcomes = resolve_counterfactuals(
        candidates,
        events,
        settings.horizons_seconds,
        seed=seed,
    )
    metrics = replay_metrics(candidates, outcomes)
    stages = stage_metrics(outcomes, validation)
    profile_results: dict[str, object] = {}
    for name, sensitivity_profile in settings.execution_profiles.items():
        profile_candidates = replay_strategy(events, strategy, sensitivity_profile)
        profile_outcomes = resolve_counterfactuals(
            profile_candidates,
            events,
            settings.horizons_seconds,
            seed=seed,
        )
        profile_results[name] = {
            "metrics": replay_metrics(profile_candidates, profile_outcomes),
            "execution_edge": _mean_cost(profile_candidates),
        }
    stressed = cast(dict[str, object], profile_results["STRESSED"])
    regimes = {
        candidate.regime
        for candidate in candidates
        if candidate.decision == "ACCEPTED" and candidate.regime != "UNKNOWN"
    }
    temporal_windows = sum(
        int(str(cast(Mapping[str, object], stage).get("sample_size", 0))) > 0
        for stage in stages.values()
    )
    promotion = evaluate_promotion(
        metrics,
        stages,
        cast(Mapping[str, object], stressed["metrics"]),
        settings,
        temporal_windows=temporal_windows,
        regimes=len(regimes),
        parameter_stability="INSUFFICIENT_EVIDENCE",
        data_integrity="VALID",
    )
    evidence_reasons = ["EXECUTION_MODEL_NOT_VALIDATED"]
    if strategy.strategy_id in {"XAU_FAIR_VALUE_V1", "XAU_COMBINED_V1"}:
        evidence_reasons.append("REFERENCE_FRESHNESS_UNAVAILABLE")
    promotion["reasons"] = list(cast(Sequence[str], promotion["reasons"])) + (
        evidence_reasons
    )
    promotion["promotion_eligible"] = False
    candidate_ledger = write_candidate_ledger(
        experiment_path / "candidate-ledger.jsonl", candidates
    )
    counterfactual_ledger = write_counterfactual_ledger(
        experiment_path / "counterfactual-ledger.jsonl", outcomes
    )
    funnel = entry_funnel(candidates, outcomes)
    counterfactual = counterfactual_summary(outcomes)
    deterministic_result: dict[str, object] = {
        **identity,
        "sample_window": {
            "start": events[0].event_time.isoformat().replace("+00:00", "Z"),
            "end": events[-1].event_time.isoformat().replace("+00:00", "Z"),
        },
        "status": "COMPLETE",
        "metrics": metrics,
        "counterfactual": counterfactual,
        "funnel": funnel,
        "walk_forward": validation,
        "stage_metrics": stages,
        "execution_profiles": profile_results,
        "execution_edge": _mean_cost(candidates),
        "calibration": calibration_diagnostics(outcomes),
        "parameter_stability": {
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": (
                "No challenger passed gross-entry-edge screening; neighborhood search withheld."
            ),
        },
        "promotion": promotion,
        "candidate_ledger": candidate_ledger,
        "counterfactual_ledger": counterfactual_ledger,
        "limitations": [
            "REST receive time is used because exchange event time is unavailable.",
            "The source has no BBO, depth, queue, or executable fill evidence.",
            "REALISTIC execution inputs are conservative simulated assumptions, not measurements.",
            "Candidate observations overlap and are not independent trades.",
            "Signal strength is heuristic and remains uncalibrated.",
        ],
    }
    deterministic_result["result_hash"] = _sha256(deterministic_result)
    write_json(experiment_path / "result.json", deterministic_result)
    (experiment_path / "result.sha256").write_text(
        _file_hash(experiment_path / "result.json") + "\n", encoding="ascii"
    )
    (experiment_path / "report.md").write_text(
        _strategy_report_markdown(deterministic_result), encoding="utf-8", newline="\n"
    )
    registry = AppendOnlyRegistry(research_root / "registry.jsonl")
    registry.append(
        "experiment_completed",
        {
            **identity,
            "timestamp": _utc_now(),
            "sample_window": deterministic_result["sample_window"],
            "walk_forward_fold": "ALL_WITH_SEALED_FINAL_HOLDOUT",
            "result_files": {
                "result": str(experiment_path / "result.json"),
                "candidate_ledger": str(experiment_path / "candidate-ledger.jsonl"),
                "counterfactual_ledger": str(
                    experiment_path / "counterfactual-ledger.jsonl"
                ),
            },
            "result_hash": deterministic_result["result_hash"],
            "status": "COMPLETE",
        },
    )
    return deterministic_result


def _lifecycle_for_report(registry: LifecycleRegistry) -> dict[str, dict[str, object]]:
    return registry.latest()


def _strategy_row(
    result: Mapping[str, object], lifecycle: Mapping[str, Mapping[str, object]]
) -> dict[str, object]:
    metrics = cast(Mapping[str, object], result["metrics"])
    stages = cast(Mapping[str, Mapping[str, object]], result["stage_metrics"])
    promotion = cast(Mapping[str, object], result["promotion"])
    key = f"{result['strategy_id']}:{result['strategy_version']}"
    state = lifecycle.get(key, {"state": "EXPERIMENTAL"})
    return {
        "strategy": result["strategy_id"],
        "version": result["strategy_version"],
        "lifecycle_state": state.get("state", "EXPERIMENTAL"),
        "sample": metrics.get("sample_size"),
        "acceptance_rate": metrics.get("acceptance_rate"),
        "gross_expectancy_bps": metrics.get("gross_expectancy_bps"),
        "net_expectancy_bps": metrics.get("net_expectancy_bps"),
        "profit_factor": metrics.get("profit_factor"),
        "maximum_drawdown_bps": metrics.get("maximum_drawdown_bps"),
        "test_result": stages.get("TEST", {}).get("net_expectancy_bps"),
        "final_holdout_result": stages.get("FINAL_HOLDOUT", {}).get(
            "net_expectancy_bps"
        ),
        "stress_result": cast(Mapping[str, object], cast(Mapping[str, object], result[
            "execution_profiles"
        ])["STRESSED"])["metrics"],
        "parameter_stability": cast(Mapping[str, object], result[
            "parameter_stability"
        ])["status"],
        "promotion_eligible": promotion["promotion_eligible"],
        "reason_blocked": cast(list[str], promotion["reasons"]),
        "experiment_id": result["experiment_id"],
    }


def _summary_markdown(summary: Mapping[str, object]) -> str:
    dataset = cast(Mapping[str, object], summary["dataset"])
    rows = cast(Sequence[Mapping[str, object]], summary["strategy_lab"])
    lines = [
        "# Research and Promotion Evidence",
        "",
        f"Generated: {summary['generated_at']}",
        f"Dataset: `{dataset['dataset_id']}` ({dataset['row_count']} observations)",
        "",
        "| Strategy | State | Sample | Net expectancy bps | Promotion |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {strategy} | {lifecycle_state} | {sample} | {net_expectancy_bps} | "
            "{promotion_eligible} |".format(**row)
        )
    lines.extend(
        [
            "",
            (
                "No result in this report grants execution authority. PAPER activation remains a "
                "separate manual operator action, and LIVE is unavailable."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def run_full(project_root: Path, *, seed: int = 7) -> dict[str, object]:
    ama_source, entry_source = _source_paths(project_root)
    dataset_manifest = freeze_xau_dataset(
        project_root,
        ama_source,
        project_root / "research" / "datasets",
        project_root / "config" / "paper.toml",
    )
    dataset_path = project_root / "research" / "datasets" / str(
        dataset_manifest["dataset_id"]
    )
    lifecycle_registry = LifecycleRegistry(project_root / "research")
    lifecycle_registry.initialize(git_commit=str(dataset_manifest["code_commit"]))
    results = [
        _run_experiment(project_root, dataset_path, strategy, seed=seed)
        for strategy in default_strategies()
    ]
    lifecycle = _lifecycle_for_report(lifecycle_registry)
    strategy_lab = [_strategy_row(result, lifecycle) for result in results]
    unsupported = unsupported_strategy_results()
    ama_diagnostic = analyze_ama_evidence(ama_source)
    entry_diagnostic = analyze_entry_v3_evidence(entry_source) if entry_source else {
        "strategy_id": "MICROSTRUCTURE_ENTRY_V3",
        "status": "UNAVAILABLE",
        "reason": "NO_ENTRY_V3_EVIDENCE_FOUND",
    }
    summary: dict[str, object] = {
        "report_schema_version": 1,
        "generated_at": _utc_now(),
        "dataset": dataset_manifest,
        "experiment_identity": {
            "dataset_id": dataset_manifest["dataset_id"],
            "dataset_hash": dataset_manifest["identity_hash"],
            "git_commit": dataset_manifest["code_commit"],
            "config_hash": results[0]["config_hash"],
            "capture_config_hash": dataset_manifest["config_hash"],
            "execution_profile": "BASELINE",
            "experiment_ids": [result["experiment_id"] for result in results],
        },
        "strategy_lab": strategy_lab,
        "unsupported_strategies": unsupported,
        "candidate_funnels": {
            "KAMA_FILTERED_CAPTURED": ama_diagnostic.get("funnel"),
            "ENTRY_V3_CAPTURED": entry_diagnostic.get("funnel"),
        },
        "no_trade_value": {
            "KAMA_FILTERED_CAPTURED": ama_diagnostic.get("counterfactual"),
            "ENTRY_V3_CAPTURED": entry_diagnostic.get("counterfactual"),
        },
        "execution_edge": {
            str(result["strategy_id"]): result["execution_edge"] for result in results
        },
        "lifecycle": lifecycle,
        "experiments": results,
        "ama_diagnostic": ama_diagnostic,
        "entry_v3_diagnostic": entry_diagnostic,
        "paper_eligible_strategies": [
            row["strategy"] for row in strategy_lab if row["promotion_eligible"] is True
        ],
        "automatic_promotion": False,
        "paper_activation": "EXPLICIT_OPERATOR_APPROVAL_REQUIRED",
        "live_trading": "UNAVAILABLE",
        "current_execution_authority_changed": False,
        "retention": {
            "canonical": "datasets, candidate ledgers, counterfactual ledgers, registry, lifecycle",
            "regenerable": "dashboard summaries and Markdown reports",
            "deletion_policy": "no automatic deletion of canonical evidence",
        },
    }
    reports = project_root / "reports"
    write_json(reports / "research-latest.json", summary, replace_existing=True)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "research-latest.md").write_text(
        _summary_markdown(summary), encoding="utf-8", newline="\n"
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline AUTOTRADE research and promotion evidence pipeline"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "full", help="freeze current evidence and run all reconstructable challengers"
    )
    freeze = subparsers.add_parser("freeze", help="freeze an immutable XAU research dataset")
    freeze.add_argument("--source", type=Path)
    verify = subparsers.add_parser("verify-dataset", help="verify a frozen dataset")
    verify.add_argument("dataset", type=Path)
    approve = subparsers.add_parser(
        "approve-paper", help="perform a manual PAPER lifecycle transition"
    )
    approve.add_argument("strategy_id")
    approve.add_argument("strategy_version")
    approve.add_argument("target", choices=("PAPER_ELIGIBLE", "PAPER_ACTIVE"))
    approve.add_argument("--operator", required=True)
    approve.add_argument("--reason", required=True)
    approve.add_argument("--promotion-evidence", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = args.project_root.resolve()
    if args.command == "full":
        result = run_full(project_root)
    elif args.command == "freeze":
        source = args.source
        if source is None:
            source, _ = _source_paths(project_root)
        result = freeze_xau_dataset(
            project_root,
            source,
            project_root / "research" / "datasets",
            project_root / "config" / "paper.toml",
        )
    elif args.command == "verify-dataset":
        result = verify_dataset(args.dataset)
    else:
        registry = LifecycleRegistry(project_root / "research")
        evidence = (
            _json_object(args.promotion_evidence) if args.promotion_evidence else None
        )
        result = registry.operator_transition(
            strategy_id=args.strategy_id,
            strategy_version=args.strategy_version,
            target=LifecycleState(args.target),
            operator=args.operator,
            reason=args.reason,
            promotion_evidence=evidence,
        )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
