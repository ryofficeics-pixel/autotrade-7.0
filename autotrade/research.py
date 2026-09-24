from __future__ import annotations

import bisect
import hashlib
import json
import os
import statistics
import subprocess
import tomllib
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

ZERO_HASH = "0" * 64
DATASET_SCHEMA_VERSION = 1
CANDIDATE_SCHEMA_VERSION = 1
REGISTRY_SCHEMA_VERSION = 1
DEFAULT_HORIZONS = (10, 30, 60, 180, 300, 900)
FINAL_HOLDOUT = "FINAL_HOLDOUT"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit(project_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"


def _json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return cast(dict[str, object], value)


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _finite_decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    return number


def _float(value: Decimal | float | int | None, digits: int = 8) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _as_int(value: object) -> int:
    return int(str(value))


def _as_float(value: object) -> float:
    return float(str(value))


def _prefix_bytes(path: Path) -> bytes:
    size = path.stat().st_size
    with path.open("rb") as stream:
        data = stream.read(size)
    newline = data.rfind(b"\n")
    if newline < 0:
        raise ValueError(f"{path} contains no complete records")
    return data[: newline + 1]


@dataclass(frozen=True)
class ExecutionProfile:
    profile_id: str
    version: str
    fee_bps: Decimal
    spread_multiplier: Decimal
    slippage_bps: Decimal
    latency_bps: Decimal
    adverse_selection_bps: Decimal
    funding_bps: Decimal
    impact_bps: Decimal
    fill_probability: Decimal
    partial_fill_ratio: Decimal

    def __post_init__(self) -> None:
        costs = (
            self.fee_bps,
            self.spread_multiplier,
            self.slippage_bps,
            self.latency_bps,
            self.adverse_selection_bps,
            self.funding_bps,
            self.impact_bps,
        )
        if any(not value.is_finite() or value < 0 for value in costs):
            raise ValueError(f"execution profile {self.profile_id} has invalid costs")
        if not Decimal(0) <= self.fill_probability <= Decimal(1):
            raise ValueError("fill_probability must be between zero and one")
        if not Decimal(0) < self.partial_fill_ratio <= Decimal(1):
            raise ValueError("partial_fill_ratio must be greater than zero and at most one")


@dataclass(frozen=True)
class ResearchSettings:
    schema_version: int
    horizons_seconds: tuple[int, ...]
    purge_seconds: int
    embargo_seconds: int
    stage_fractions: tuple[Decimal, Decimal, Decimal, Decimal]
    minimum_promotion_trades: int
    minimum_profit_factor: Decimal
    maximum_drawdown_pct: Decimal
    minimum_temporal_windows: int
    minimum_regimes: int
    execution_profiles: Mapping[str, ExecutionProfile]


def load_research_settings(path: Path) -> ResearchSettings:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    research = cast(dict[str, object], raw["research"])
    profiles_raw = cast(dict[str, dict[str, object]], raw["execution_profiles"])
    profiles = {
        name: ExecutionProfile(
            profile_id=name,
            version=str(values["version"]),
            fee_bps=_finite_decimal(values["fee_bps"], f"{name}.fee_bps"),
            spread_multiplier=_finite_decimal(
                values["spread_multiplier"], f"{name}.spread_multiplier"
            ),
            slippage_bps=_finite_decimal(values["slippage_bps"], f"{name}.slippage_bps"),
            latency_bps=_finite_decimal(values["latency_bps"], f"{name}.latency_bps"),
            adverse_selection_bps=_finite_decimal(
                values["adverse_selection_bps"], f"{name}.adverse_selection_bps"
            ),
            funding_bps=_finite_decimal(values["funding_bps"], f"{name}.funding_bps"),
            impact_bps=_finite_decimal(values["impact_bps"], f"{name}.impact_bps"),
            fill_probability=_finite_decimal(
                values["fill_probability"], f"{name}.fill_probability"
            ),
            partial_fill_ratio=_finite_decimal(
                values["partial_fill_ratio"], f"{name}.partial_fill_ratio"
            ),
        )
        for name, values in profiles_raw.items()
    }
    fractions = tuple(
        _finite_decimal(research[key], key)
        for key in (
            "selection_fraction",
            "validation_fraction",
            "test_fraction",
            "final_holdout_fraction",
        )
    )
    if sum(fractions, Decimal(0)) != Decimal(1):
        raise ValueError("research stage fractions must sum to one")
    horizons = tuple(
        _as_int(value) for value in cast(list[object], research["horizons_seconds"])
    )
    if not horizons or tuple(sorted(set(horizons))) != horizons or min(horizons) <= 0:
        raise ValueError("research horizons must be unique positive ascending seconds")
    return ResearchSettings(
        schema_version=_as_int(research["schema_version"]),
        horizons_seconds=horizons,
        purge_seconds=_as_int(research["purge_seconds"]),
        embargo_seconds=_as_int(research["embargo_seconds"]),
        stage_fractions=cast(tuple[Decimal, Decimal, Decimal, Decimal], fractions),
        minimum_promotion_trades=_as_int(research["minimum_promotion_trades"]),
        minimum_profit_factor=_finite_decimal(
            research["minimum_profit_factor"], "minimum_profit_factor"
        ),
        maximum_drawdown_pct=_finite_decimal(
            research["maximum_drawdown_pct"], "maximum_drawdown_pct"
        ),
        minimum_temporal_windows=_as_int(research["minimum_temporal_windows"]),
        minimum_regimes=_as_int(research["minimum_regimes"]),
        execution_profiles=profiles,
    )


@dataclass(frozen=True)
class DatasetEvent:
    dataset_id: str
    dataset_position: int
    source_event_id: str
    source_sequence: int
    event_time: datetime
    receive_time: datetime
    processing_time: datetime | None
    timestamp_semantics: str
    symbol: str
    last: Decimal
    spread_bps: Decimal
    quote_volume_usdt: Decimal
    features: Mapping[str, object]
    reference: Mapping[str, object]
    quality_flags: tuple[str, ...]


@dataclass(frozen=True)
class CostBreakdown:
    execution_profile_id: str
    execution_profile_version: str
    gross_move_bps: Decimal
    spread_cost_bps: Decimal
    fee_cost_bps: Decimal
    slippage_cost_bps: Decimal
    latency_cost_bps: Decimal
    adverse_selection_bps: Decimal
    funding_cost_bps: Decimal
    impact_bps: Decimal
    missed_partial_fill_effect_bps: Decimal
    total_cost_bps: Decimal
    net_edge_bps: Decimal
    fill_probability: Decimal
    partial_fill_ratio: Decimal

    def record(self) -> dict[str, object]:
        return {
            key: _float(value, 6) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }


@dataclass(frozen=True)
class SignalCandidate:
    candidate_id: str
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    parameter_hash: str
    timestamp: datetime
    symbol: str
    direction: str
    reference_entry_price: Decimal
    signal_strength: Decimal
    calibrated_probability: Decimal | None
    expected_move_bps: Decimal
    expected_edge_bps: Decimal
    feature_snapshot: Mapping[str, object]
    market_snapshot: Mapping[str, object]
    regime: str
    quality_state: str
    decision: str
    primary_rejection_reason: str | None
    all_rejection_reasons: tuple[str, ...]
    source_event_id: str
    dataset_position: int
    data_freshness: str
    reference_state: str
    cost: CostBreakdown | None = None

    def record(self) -> dict[str, object]:
        value = asdict(self)
        value["timestamp"] = self.timestamp.isoformat().replace("+00:00", "Z")
        for field in (
            "reference_entry_price",
            "signal_strength",
            "calibrated_probability",
            "expected_move_bps",
            "expected_edge_bps",
        ):
            number = value[field]
            value[field] = _float(number, 8) if isinstance(number, Decimal) else None
        value["all_rejection_reasons"] = list(self.all_rejection_reasons)
        value["schema_version"] = CANDIDATE_SCHEMA_VERSION
        return cast(dict[str, object], value)


class StrategyProtocol(Protocol):
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    parameter_hash: str

    def on_market_event(self, market_state: DatasetEvent) -> SignalCandidate | None: ...


def _parameter_hash(parameters: Mapping[str, object]) -> str:
    return _sha256(_canonical(parameters))


def _strategy_hash(strategy: type[object]) -> str:
    path = Path(strategy.__module__.replace(".", os.sep) + ".py")
    candidate = Path.cwd() / path
    return _file_hash(candidate) if candidate.exists() else _sha256(strategy.__name__.encode())


def _candidate_id(event: DatasetEvent, strategy_id: str, version: str, parameter_hash: str) -> str:
    identity = (
        event.dataset_id,
        str(event.dataset_position),
        event.source_event_id,
        strategy_id,
        version,
        parameter_hash,
    )
    return _sha256("|".join(identity).encode())[:32]


def _base_candidate(
    event: DatasetEvent,
    *,
    strategy_id: str,
    strategy_version: str,
    strategy_hash: str,
    parameter_hash: str,
    direction: str,
    strength: Decimal,
    expected_move_bps: Decimal,
    reasons: Sequence[str],
    quality_state: str,
) -> SignalCandidate:
    reference = event.reference
    reference_state = str(reference.get("status", "UNAVAILABLE"))
    decision = "REJECTED" if reasons or direction not in {"LONG", "SHORT"} else "ACCEPTED"
    normalized_reasons = tuple(reasons) or (() if decision == "ACCEPTED" else ("NO_DIRECTION",))
    return SignalCandidate(
        candidate_id=_candidate_id(event, strategy_id, strategy_version, parameter_hash),
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_hash=strategy_hash,
        parameter_hash=parameter_hash,
        timestamp=event.event_time,
        symbol=event.symbol,
        direction=direction,
        reference_entry_price=event.last,
        signal_strength=max(Decimal(0), strength),
        calibrated_probability=None,
        expected_move_bps=max(Decimal(0), expected_move_bps),
        expected_edge_bps=max(Decimal(0), expected_move_bps),
        feature_snapshot=event.features,
        market_snapshot={
            "last": _float(event.last),
            "spread_bps": _float(event.spread_bps),
            "quote_volume_usdt": _float(event.quote_volume_usdt, 2),
        },
        regime=str(event.features.get("regime", "UNKNOWN")),
        quality_state=quality_state,
        decision=decision,
        primary_rejection_reason=normalized_reasons[0] if normalized_reasons else None,
        all_rejection_reasons=normalized_reasons,
        source_event_id=event.source_event_id,
        dataset_position=event.dataset_position,
        data_freshness="STALE" if "STALE_DATA" in event.quality_flags else "CURRENT",
        reference_state=reference_state,
    )


class KamaRawStrategy:
    strategy_id = "XAU_KAMA20_RAW_REPLAY_V1"
    strategy_version = "1"

    def __init__(self) -> None:
        self.parameters: dict[str, object] = {"source_features": "AMA_CONTROL_V2_CAUSAL"}
        self.parameter_hash = _parameter_hash(self.parameters)
        self.strategy_hash = _strategy_hash(type(self))

    def on_market_event(self, market_state: DatasetEvent) -> SignalCandidate:
        direction = str(market_state.features.get("raw_direction", "WAIT"))
        distance = _finite_decimal(
            market_state.features.get("distance_from_kama20_bps", 0), "distance"
        )
        strength = _finite_decimal(market_state.features.get("confidence", 0), "strength")
        reasons: list[str] = []
        if market_state.features.get("kama20") is None:
            reasons.append("WARMING_UP")
        elif direction not in {"LONG", "SHORT"}:
            reasons.append("NO_DIRECTION")
        return _base_candidate(
            market_state,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            strategy_hash=self.strategy_hash,
            parameter_hash=self.parameter_hash,
            direction=direction,
            strength=strength,
            expected_move_bps=distance,
            reasons=reasons,
            quality_state="RAW_CONTROL",
        )


class KamaFilteredStrategy:
    strategy_id = "XAU_KAMA20_FILTERED_REPLAY_V1"
    strategy_version = "1"

    def __init__(self, parameters: Mapping[str, object]) -> None:
        self.parameters = dict(parameters)
        self.parameter_hash = _parameter_hash(self.parameters)
        self.strategy_hash = _strategy_hash(type(self))

    def on_market_event(self, market_state: DatasetEvent) -> SignalCandidate:
        features = market_state.features
        direction = str(features.get("raw_direction", "WAIT"))
        distance = _finite_decimal(features.get("distance_from_kama20_bps", 0), "distance")
        strength = _finite_decimal(features.get("confidence", 0), "strength")
        slope_map = cast(Mapping[str, object], features.get("kama_slopes_bps", {}))
        slope = abs(_finite_decimal(slope_map.get("20", 0), "kama20 slope"))
        hysteresis = _finite_decimal(features.get("hysteresis_bps", 0), "hysteresis")
        reasons: list[str] = []
        reference_state = str(market_state.reference.get("status", "UNAVAILABLE"))
        regime = str(features.get("regime", "UNKNOWN"))
        allowed_regimes = {
            "LONG": {"UPTREND", "STRONG_UPTREND"},
            "SHORT": {"DOWNTREND", "STRONG_DOWNTREND"},
        }
        if features.get("kama20") is None:
            reasons.append("WARMING_UP")
        if direction not in {"LONG", "SHORT"}:
            reasons.append("NO_DIRECTION")
        if "STALE_DATA" in market_state.quality_flags:
            reasons.append("STALE_DATA")
        if market_state.spread_bps > _finite_decimal(
            self.parameters["maximum_spread_bps"], "maximum_spread_bps"
        ):
            reasons.append("SPREAD_LIMIT")
        if reference_state == "UNAVAILABLE" and bool(self.parameters["reference_required"]):
            reasons.append("REFERENCE_UNAVAILABLE")
        if reference_state == "ABNORMAL":
            reasons.append("REFERENCE_ABNORMAL")
        if direction in allowed_regimes and regime not in allowed_regimes[direction]:
            reasons.append("REGIME_MISMATCH")
        if _as_int(features.get("regime_observations", 0)) < _as_int(
            self.parameters["minimum_regime_observations"]
        ):
            reasons.append("REGIME_NOT_PERSISTENT")
        if slope < _finite_decimal(self.parameters["minimum_slope_bps"], "minimum_slope_bps"):
            reasons.append("SLOPE_TOO_FLAT")
        if distance < hysteresis:
            reasons.append("HYSTERESIS_BAND")
        if distance > _finite_decimal(
            self.parameters["maximum_distance_bps"], "maximum_distance_bps"
        ):
            reasons.append("EXTENDED_PRICE")
        if market_state.quote_volume_usdt < _finite_decimal(
            self.parameters["minimum_quote_volume_usdt"], "minimum_quote_volume_usdt"
        ):
            reasons.append("LIQUIDITY_LIMIT")
        if strength < _finite_decimal(
            self.parameters["minimum_signal_strength"], "minimum_signal_strength"
        ):
            reasons.append("LOW_SIGNAL_STRENGTH")
        return _base_candidate(
            market_state,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            strategy_hash=self.strategy_hash,
            parameter_hash=self.parameter_hash,
            direction=direction,
            strength=strength,
            expected_move_bps=distance,
            reasons=tuple(dict.fromkeys(reasons)),
            quality_state="FILTERED",
        )


class XauFairValueStrategy:
    strategy_id = "XAU_FAIR_VALUE_V1"
    strategy_version = "1"

    def __init__(self, parameters: Mapping[str, object]) -> None:
        self.parameters = dict(parameters)
        self.parameter_hash = _parameter_hash(self.parameters)
        self.strategy_hash = _strategy_hash(type(self))

    def on_market_event(self, market_state: DatasetEvent) -> SignalCandidate:
        reference = market_state.reference
        status = str(reference.get("status", "UNAVAILABLE"))
        dislocation_value = reference.get("dislocation_bps")
        dispersion_value = reference.get("dispersion_bps")
        dislocation = (
            _finite_decimal(dislocation_value, "dislocation_bps")
            if dislocation_value is not None
            else Decimal(0)
        )
        dispersion = (
            _finite_decimal(dispersion_value, "dispersion_bps")
            if dispersion_value is not None
            else Decimal("1000000")
        )
        direction = "SHORT" if dislocation > 0 else "LONG" if dislocation < 0 else "WAIT"
        expected = abs(dislocation)
        uncertainty = dispersion * _finite_decimal(
            self.parameters["dispersion_allowance"], "dispersion_allowance"
        )
        strength = min(Decimal(1), expected / max(uncertainty, Decimal(1)))
        reasons: list[str] = []
        if status == "UNAVAILABLE":
            reasons.append("REFERENCE_UNAVAILABLE")
        elif status == "ABNORMAL":
            reasons.append("REFERENCE_ABNORMAL")
        if direction == "WAIT":
            reasons.append("NO_DISLOCATION")
        if expected <= uncertainty:
            reasons.append("DISLOCATION_WITHIN_REFERENCE_UNCERTAINTY")
        return _base_candidate(
            market_state,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            strategy_hash=self.strategy_hash,
            parameter_hash=self.parameter_hash,
            direction=direction,
            strength=strength,
            expected_move_bps=max(Decimal(0), expected - uncertainty),
            reasons=reasons,
            quality_state="RESEARCH_ONLY",
        )


class XauCombinedStrategy:
    strategy_id = "XAU_COMBINED_V1"
    strategy_version = "1"

    def __init__(self, parameters: Mapping[str, object]) -> None:
        self.parameters = dict(parameters)
        self.parameter_hash = _parameter_hash(self.parameters)
        self.strategy_hash = _strategy_hash(type(self))

    def on_market_event(self, market_state: DatasetEvent) -> SignalCandidate:
        reference = market_state.reference
        features = market_state.features
        status = str(reference.get("status", "UNAVAILABLE"))
        dislocation_value = reference.get("dislocation_bps")
        dislocation = (
            _finite_decimal(dislocation_value, "dislocation_bps")
            if dislocation_value is not None
            else Decimal(0)
        )
        fair_direction = "SHORT" if dislocation > 0 else "LONG" if dislocation < 0 else "WAIT"
        trend_direction = str(features.get("raw_direction", "WAIT"))
        distance = _finite_decimal(features.get("distance_from_kama20_bps", 0), "distance")
        expected = min(abs(dislocation), distance)
        strength = min(
            Decimal(1),
            _finite_decimal(features.get("confidence", 0), "strength")
            * min(Decimal(1), abs(dislocation) / Decimal(20)),
        )
        reasons: list[str] = []
        if status != "HEALTHY":
            reasons.append("REFERENCE_NOT_HEALTHY")
        if fair_direction == "WAIT":
            reasons.append("NO_DISLOCATION")
        if trend_direction != fair_direction:
            reasons.append("TREND_DISAGREES_WITH_FAIR_VALUE")
        if str(features.get("regime", "UNKNOWN")) in {"CHOP", "UNKNOWN"}:
            reasons.append("REGIME_NOT_PERMITTED")
        if market_state.spread_bps > _finite_decimal(
            self.parameters["maximum_spread_bps"], "maximum_spread_bps"
        ):
            reasons.append("SPREAD_LIMIT")
        return _base_candidate(
            market_state,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            strategy_hash=self.strategy_hash,
            parameter_hash=self.parameter_hash,
            direction=fair_direction,
            strength=strength,
            expected_move_bps=expected,
            reasons=reasons,
            quality_state="RESEARCH_ONLY",
        )


def default_strategies() -> tuple[StrategyProtocol, ...]:
    filtered = {
        "maximum_spread_bps": "8",
        "reference_required": False,
        "minimum_regime_observations": 3,
        "minimum_slope_bps": "0.10",
        "maximum_distance_bps": "50",
        "minimum_quote_volume_usdt": "1000000",
        "minimum_signal_strength": "0.55",
    }
    return (
        KamaRawStrategy(),
        KamaFilteredStrategy(filtered),
        XauFairValueStrategy({"dispersion_allowance": "1"}),
        XauCombinedStrategy({"maximum_spread_bps": "8"}),
    )


def _verify_source_chain(records: Sequence[dict[str, object]]) -> None:
    previous = ZERO_HASH
    expected_sequence = 1
    for line_number, original in enumerate(records, 1):
        record = dict(original)
        stored = str(record.pop("record_hash", ""))
        if record.get("previous_hash") != previous:
            raise ValueError(f"source line {line_number} previous hash mismatch")
        if _as_int(record.get("sequence", 0)) != expected_sequence:
            raise ValueError(f"source line {line_number} sequence mismatch")
        expected = _sha256(previous.encode() + _canonical(record))
        if stored != expected:
            raise ValueError(f"source line {line_number} record hash mismatch")
        previous = stored
        expected_sequence += 1


def freeze_xau_dataset(
    project_root: Path,
    source_path: Path,
    dataset_root: Path,
    config_path: Path,
) -> dict[str, object]:
    source_prefix = _prefix_bytes(source_path)
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(source_prefix.splitlines(), 1):
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"source line {line_number} is not an object")
        records.append(cast(dict[str, object], value))
    _verify_source_chain(records)

    market_rows: list[dict[str, object]] = []
    reference_rows: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    previous_time: datetime | None = None
    data_quality = Counter[str]()
    for record in records:
        if record.get("event_type") != "observation":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("observation payload is not an object")
        observation_id = str(payload.get("observation_id", ""))
        if not observation_id or observation_id in seen_ids:
            raise ValueError("observation identities must be non-empty and unique")
        seen_ids.add(observation_id)
        timestamp = _parse_utc(payload.get("observed_at_utc"))
        flags = ["RECEIVE_TIME_ONLY", "BBO_UNAVAILABLE"]
        if previous_time is not None and timestamp < previous_time:
            flags.append("CLOCK_REGRESSION")
        previous_time = timestamp
        price = _finite_decimal(payload.get("price"), "price")
        spread = _finite_decimal(payload.get("spread_bps"), "spread_bps")
        volume = _finite_decimal(payload.get("quote_volume_usdt"), "quote_volume_usdt")
        if price <= 0:
            flags.append("INVALID_PRICE")
        if spread < 0:
            flags.append("NEGATIVE_SPREAD")
        if volume < 0:
            flags.append("INVALID_VOLUME")
        reference = payload.get("reference")
        reference_map = cast(dict[str, object], reference) if isinstance(reference, dict) else {}
        if str(reference_map.get("status", "UNAVAILABLE")) != "HEALTHY":
            flags.append("REFERENCE_NOT_HEALTHY")
        for flag in flags:
            data_quality[flag] += 1
        position = len(market_rows) + 1
        feature_keys = (
            "atr_proxy_bps",
            "confidence",
            "distance_from_kama20_bps",
            "hysteresis_bps",
            "kama10",
            "kama20",
            "kama50",
            "kama_slopes_bps",
            "raw_direction",
            "regime",
            "regime_observations",
        )
        row = {
            "dataset_position": position,
            "source_event_id": str(record.get("event_id", "")),
            "source_observation_id": observation_id,
            "source_sequence": _as_int(record["sequence"]),
            "event_time": None,
            "receive_time": timestamp.isoformat().replace("+00:00", "Z"),
            "processing_time": str(record.get("recorded_at_utc", "")),
            "timestamp_semantics": "GATE_REST_LOCAL_RECEIVE_TIME",
            "symbol": str(payload.get("symbol", "XAU_USDT")),
            "bid": None,
            "ask": None,
            "mid": None,
            "last": _float(price),
            "spread_bps": _float(spread, 6),
            "quote_volume_usdt": _float(volume, 2),
            "depth": None,
            "source": "AMA_CONTROL_V2_GATE_PUBLIC_REST",
            "data_freshness": "NOT_MEASURED_IN_SOURCE_EVIDENCE",
            "quality_flags": flags,
            "features": {key: payload.get(key) for key in feature_keys},
            "reference": reference_map,
        }
        market_rows.append(row)
        sources = reference_map.get("sources", {})
        if isinstance(sources, dict):
            for symbol in ("XAUT_USDT", "PAXG_USDT"):
                value = sources.get(symbol)
                if value is None:
                    continue
                reference_price = _finite_decimal(value, f"{symbol} price")
                reference_rows.append(
                    {
                        "dataset_position": position,
                        "source_observation_id": observation_id,
                        "event_time": None,
                        "receive_time": timestamp.isoformat().replace("+00:00", "Z"),
                        "timestamp_semantics": "GATE_REST_LOCAL_RECEIVE_TIME",
                        "symbol": symbol,
                        "last": _float(reference_price),
                        "source": "AMA_CONTROL_V2_GATE_PUBLIC_REST",
                        "quality_flags": ["RECEIVE_TIME_ONLY", "BBO_UNAVAILABLE"],
                    }
                )
    if not market_rows:
        raise ValueError("source evidence contains no observations")
    if data_quality["CLOCK_REGRESSION"]:
        raise ValueError("source evidence has receive-time clock regression")
    if data_quality["INVALID_PRICE"] or data_quality["NEGATIVE_SPREAD"]:
        raise ValueError("source evidence has invalid price or spread")

    market_bytes = b"".join(_canonical(row) + b"\n" for row in market_rows)
    reference_bytes = b"".join(_canonical(row) + b"\n" for row in reference_rows)
    config_hash = _file_hash(config_path)
    git_commit = _git_commit(project_root)
    identity = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "market_events_sha256": _sha256(market_bytes),
        "reference_events_sha256": _sha256(reference_bytes),
        "source_prefix_sha256": _sha256(source_prefix),
        "source_last_sequence": _as_int(records[-1]["sequence"]),
        "source_last_record_hash": str(records[-1]["record_hash"]),
        "config_hash": config_hash,
        "code_commit": git_commit,
    }
    identity_hash = _sha256(_canonical(identity))
    dataset_id = f"xau-ama-v2-{identity_hash[:20]}"
    target = dataset_root / dataset_id
    if target.exists():
        manifest = verify_dataset(target)
        if manifest.get("identity_hash") != identity_hash:
            raise ValueError("existing dataset identity does not match its content")
        return manifest
    target.mkdir(parents=True, exist_ok=False)
    metadata = {
        "dataset_id": dataset_id,
        "created_at": _utc_now(),
        "capture_start": market_rows[0]["receive_time"],
        "capture_end": market_rows[-1]["receive_time"],
        "symbols": ["XAU_USDT", "XAUT_USDT", "PAXG_USDT"],
        "schema_version": DATASET_SCHEMA_VERSION,
        "source": "AMA_CONTROL_V2_HASH_CHAIN_PREFIX",
        "row_count": len(market_rows),
        "reference_row_count": len(reference_rows),
        "code_commit": git_commit,
        "config_hash": config_hash,
        "source_evidence": str(source_path.resolve()),
        "source_evidence_prefix_bytes": len(source_prefix),
        "source_evidence_prefix_sha256": _sha256(source_prefix),
        "source_last_sequence": _as_int(records[-1]["sequence"]),
        "source_last_record_hash": str(records[-1]["record_hash"]),
        "timezone_handling": "UTC timezone-aware",
        "timestamp_semantics": "local REST receive time; exchange event time unavailable",
        "execution_profile_version": "config/research.toml",
        "causal_policy": "no forward fill; references are from the same recorded poll",
        "limitations": [
            "Exchange event timestamps are unavailable.",
            "Bid, ask, depth, and individual reference freshness are unavailable.",
            "Observations are REST polls and do not prove executable fills.",
            "KAMA features were causally computed by AMA Control V2 at capture time.",
        ],
        "data_quality": dict(sorted(data_quality.items())),
    }
    metadata_bytes = _canonical(metadata) + b"\n"
    _write_new(target / "market_events.jsonl", market_bytes)
    _write_new(target / "reference_events.jsonl", reference_bytes)
    _write_new(target / "metadata.json", metadata_bytes)
    files = {
        name: {"sha256": _file_hash(target / name), "bytes": (target / name).stat().st_size}
        for name in ("market_events.jsonl", "reference_events.jsonl", "metadata.json")
    }
    manifest = {
        "dataset_id": dataset_id,
        "identity_hash": identity_hash,
        "schema_version": DATASET_SCHEMA_VERSION,
        "status": "FROZEN",
        "immutable": True,
        "row_count": len(market_rows),
        "reference_row_count": len(reference_rows),
        "capture_start": metadata["capture_start"],
        "capture_end": metadata["capture_end"],
        "symbols": metadata["symbols"],
        "code_commit": git_commit,
        "config_hash": config_hash,
        "source_prefix_sha256": _sha256(source_prefix),
        "source_last_sequence": _as_int(records[-1]["sequence"]),
        "source_last_record_hash": str(records[-1]["record_hash"]),
        "files": files,
    }
    manifest_bytes = _canonical(manifest) + b"\n"
    _write_new(target / "manifest.json", manifest_bytes)
    _write_new(target / "manifest.sha256", (_sha256(manifest_bytes) + "\n").encode())
    for path in target.iterdir():
        path.chmod(0o444)
    target.chmod(0o555)
    return verify_dataset(target)


def verify_dataset(dataset_path: Path) -> dict[str, object]:
    manifest_path = dataset_path / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    sidecar = (dataset_path / "manifest.sha256").read_text(encoding="ascii").strip()
    if sidecar != _sha256(manifest_bytes):
        raise ValueError("dataset manifest hash mismatch")
    manifest = _json_object(manifest_path)
    if manifest.get("status") != "FROZEN" or manifest.get("immutable") is not True:
        raise ValueError("dataset is not frozen")
    if manifest.get("dataset_id") != dataset_path.name:
        raise ValueError("dataset directory and manifest identity differ")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("dataset file manifest is missing")
    for name, raw_identity in files.items():
        if not isinstance(raw_identity, dict):
            raise ValueError(f"invalid identity for {name}")
        path = dataset_path / str(name)
        if not path.is_file() or _file_hash(path) != raw_identity.get("sha256"):
            raise ValueError(f"dataset file hash mismatch: {name}")
        if path.stat().st_size != _as_int(raw_identity.get("bytes", -1)):
            raise ValueError(f"dataset file size mismatch: {name}")
    return manifest


def load_dataset_events(dataset_path: Path) -> list[DatasetEvent]:
    manifest = verify_dataset(dataset_path)
    dataset_id = str(manifest["dataset_id"])
    events: list[DatasetEvent] = []
    seen_ids: set[str] = set()
    previous_time: datetime | None = None
    with (dataset_path / "market_events.jsonl").open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"dataset line {line_number} is not an object")
            row = cast(dict[str, object], raw)
            source_id = str(row["source_observation_id"])
            if source_id in seen_ids:
                raise ValueError(f"duplicate observation at dataset line {line_number}")
            seen_ids.add(source_id)
            receive_time = _parse_utc(row["receive_time"])
            event_time = (
                _parse_utc(row["event_time"]) if row.get("event_time") else receive_time
            )
            if previous_time is not None and event_time < previous_time:
                raise ValueError(f"clock regression at dataset line {line_number}")
            previous_time = event_time
            flags = tuple(str(value) for value in cast(list[object], row.get("quality_flags", [])))
            price = _finite_decimal(row["last"], "last")
            spread = _finite_decimal(row["spread_bps"], "spread_bps")
            volume = _finite_decimal(row["quote_volume_usdt"], "quote_volume_usdt")
            if price <= 0 or spread < 0 or volume < 0:
                raise ValueError(f"invalid numeric market data at dataset line {line_number}")
            processing = row.get("processing_time")
            events.append(
                DatasetEvent(
                    dataset_id=dataset_id,
                    dataset_position=_as_int(row["dataset_position"]),
                    source_event_id=source_id,
                    source_sequence=_as_int(row["source_sequence"]),
                    event_time=event_time,
                    receive_time=receive_time,
                    processing_time=_parse_utc(processing) if processing else None,
                    timestamp_semantics=str(row["timestamp_semantics"]),
                    symbol=str(row["symbol"]),
                    last=price,
                    spread_bps=spread,
                    quote_volume_usdt=volume,
                    features=cast(dict[str, object], row.get("features", {})),
                    reference=cast(dict[str, object], row.get("reference", {})),
                    quality_flags=flags,
                )
            )
    if len(events) != _as_int(manifest["row_count"]):
        raise ValueError("dataset row count does not match manifest")
    return events


def decompose_cost(
    gross_move_bps: Decimal,
    spread_bps: Decimal,
    profile: ExecutionProfile,
) -> CostBreakdown:
    spread = spread_bps * profile.spread_multiplier
    explicit = (
        spread
        + profile.fee_bps
        + profile.slippage_bps
        + profile.latency_bps
        + profile.adverse_selection_bps
        + profile.funding_bps
        + profile.impact_bps
    )
    partial_effect = max(Decimal(0), gross_move_bps - explicit) * (
        Decimal(1) - profile.partial_fill_ratio
    )
    total = explicit + partial_effect
    return CostBreakdown(
        execution_profile_id=profile.profile_id,
        execution_profile_version=profile.version,
        gross_move_bps=gross_move_bps,
        spread_cost_bps=spread,
        fee_cost_bps=profile.fee_bps,
        slippage_cost_bps=profile.slippage_bps,
        latency_cost_bps=profile.latency_bps,
        adverse_selection_bps=profile.adverse_selection_bps,
        funding_cost_bps=profile.funding_bps,
        impact_bps=profile.impact_bps,
        missed_partial_fill_effect_bps=partial_effect,
        total_cost_bps=total,
        net_edge_bps=gross_move_bps - total,
        fill_probability=profile.fill_probability,
        partial_fill_ratio=profile.partial_fill_ratio,
    )


def apply_realizable_edge_gate(
    candidate: SignalCandidate,
    profile: ExecutionProfile,
    spread_bps: Decimal,
    minimum_required_net_edge_bps: Decimal,
) -> SignalCandidate:
    costs = decompose_cost(candidate.expected_move_bps, spread_bps, profile)
    reasons = list(candidate.all_rejection_reasons)
    if costs.net_edge_bps <= minimum_required_net_edge_bps:
        reasons.append("REALIZABLE_EDGE_BELOW_HURDLE")
    unique = tuple(dict.fromkeys(reasons))
    return replace(
        candidate,
        cost=costs,
        expected_edge_bps=costs.net_edge_bps,
        decision="REJECTED" if unique else "ACCEPTED",
        primary_rejection_reason=unique[0] if unique else None,
        all_rejection_reasons=unique,
    )


def replay_strategy(
    events: Sequence[DatasetEvent],
    strategy: StrategyProtocol,
    profile: ExecutionProfile,
    *,
    minimum_required_net_edge_bps: Decimal = Decimal(0),
) -> list[SignalCandidate]:
    candidates: list[SignalCandidate] = []
    for event in events:
        candidate = strategy.on_market_event(event)
        if candidate is None:
            continue
        if candidate.source_event_id != event.source_event_id:
            raise ValueError("strategy candidate source identity does not match current event")
        if (
            candidate.dataset_position != event.dataset_position
            or candidate.timestamp > event.event_time
        ):
            raise ValueError("strategy candidate attempted to use a future dataset position")
        candidates.append(
            apply_realizable_edge_gate(
                candidate,
                profile,
                event.spread_bps,
                minimum_required_net_edge_bps,
            )
        )
    return candidates


def _deterministic_fill(candidate_id: str, seed: int, probability: Decimal) -> bool:
    sample = int(_sha256(f"{candidate_id}|{seed}".encode())[:16], 16)
    ratio = Decimal(sample) / Decimal(16**16 - 1)
    return ratio <= probability


def resolve_counterfactuals(
    candidates: Sequence[SignalCandidate],
    events: Sequence[DatasetEvent],
    horizons_seconds: Sequence[int],
    *,
    seed: int,
) -> list[dict[str, object]]:
    timestamps = [event.event_time for event in events]
    outcomes: list[dict[str, object]] = []
    for candidate in candidates:
        if candidate.direction not in {"LONG", "SHORT"} or candidate.cost is None:
            continue
        start = candidate.dataset_position - 1
        if start < 0 or start >= len(events):
            raise ValueError("candidate dataset position is invalid")
        terminal_time = candidate.timestamp + timedelta(seconds=max(horizons_seconds))
        stop = bisect.bisect_right(timestamps, terminal_time, lo=start)
        future = events[start:stop]
        if not future:
            continue
        sign = Decimal(1) if candidate.direction == "LONG" else Decimal(-1)
        excursions = [
            (event.last / candidate.reference_entry_price - 1) * Decimal(10_000) * sign
            for event in future
        ]
        maximum_favorable = max(excursions)
        maximum_adverse = min(excursions)
        mfe_index = excursions.index(maximum_favorable)
        mae_index = excursions.index(maximum_adverse)
        horizon_results: dict[str, object] = {}
        observed: list[tuple[int, Decimal]] = []
        for horizon in horizons_seconds:
            target = candidate.timestamp + timedelta(seconds=horizon)
            index = bisect.bisect_left(timestamps, target, lo=start, hi=len(events))
            if index >= len(events):
                horizon_results[str(horizon)] = None
                continue
            event = events[index]
            gross = (
                (event.last / candidate.reference_entry_price - 1) * Decimal(10_000) * sign
            )
            net = gross - candidate.cost.total_cost_bps
            observed.append((horizon, net))
            horizon_results[str(horizon)] = {
                "outcome_event_id": event.source_event_id,
                "observed_at": event.event_time.isoformat().replace("+00:00", "Z"),
                "gross_return_bps": _float(gross, 6),
                "net_return_bps": _float(net, 6),
            }
        terminal = observed[-1] if observed else None
        best = max(observed, key=lambda value: value[1]) if observed else None
        filled = _deterministic_fill(
            candidate.candidate_id, seed, candidate.cost.fill_probability
        )
        terminal_net = terminal[1] if terminal is not None else None
        simulated = (
            terminal_net * candidate.cost.partial_fill_ratio
            if filled and terminal_net is not None
            else Decimal(0)
            if terminal_net is not None
            else None
        )
        outcomes.append(
            {
                "candidate_id": candidate.candidate_id,
                "strategy_id": candidate.strategy_id,
                "timestamp": candidate.timestamp.isoformat().replace("+00:00", "Z"),
                "direction": candidate.direction,
                "regime": candidate.regime,
                "decision": candidate.decision,
                "primary_rejection_reason": candidate.primary_rejection_reason,
                "all_rejection_reasons": list(candidate.all_rejection_reasons),
                "entry_reference": _float(candidate.reference_entry_price),
                "horizons": horizon_results,
                "mfe_bps": _float(maximum_favorable, 6),
                "mae_bps": _float(maximum_adverse, 6),
                "time_to_mfe_seconds": round(
                    (future[mfe_index].event_time - candidate.timestamp).total_seconds(), 3
                ),
                "time_to_mae_seconds": round(
                    (future[mae_index].event_time - candidate.timestamp).total_seconds(), 3
                ),
                "best_observed_horizon_seconds": best[0] if best else None,
                "best_observed_net_bps": _float(best[1], 6) if best else None,
                "best_exit_policy": "ORACLE_DIAGNOSTIC_NOT_FOR_SELECTION",
                "terminal_horizon_seconds": terminal[0] if terminal else None,
                "terminal_net_bps": _float(terminal_net, 6),
                "simulated_fill": "FILLED" if filled else "MISSED_FILL",
                "simulated_net_bps": _float(simulated, 6),
                "cost": candidate.cost.record(),
                "lookahead": "OUTCOMES_APPENDED_AFTER_CANDIDATE",
            }
        )
    return outcomes


def counterfactual_summary(outcomes: Sequence[Mapping[str, object]]) -> dict[str, object]:
    accepted = [row for row in outcomes if row.get("decision") == "ACCEPTED"]
    rejected = [row for row in outcomes if row.get("decision") == "REJECTED"]
    avoided = [
        abs(float(cast(float, row["terminal_net_bps"])))
        for row in rejected
        if row.get("terminal_net_bps") is not None
        and float(cast(float, row["terminal_net_bps"])) <= 0
    ]
    missed = [
        float(cast(float, row["terminal_net_bps"]))
        for row in rejected
        if row.get("terminal_net_bps") is not None
        and float(cast(float, row["terminal_net_bps"])) > 0
    ]
    chronological = sorted(
        (row for row in outcomes if row.get("timestamp")),
        key=lambda row: _parse_utc(row["timestamp"]),
    )
    no_trade_seconds = 0.0
    for current, following in zip(chronological, chronological[1:], strict=False):
        if current.get("decision") == "REJECTED":
            no_trade_seconds += max(
                0.0,
                (
                    _parse_utc(following["timestamp"])
                    - _parse_utc(current["timestamp"])
                ).total_seconds(),
            )

    def grouped(field: str) -> dict[str, dict[str, object]]:
        values: dict[str, list[float]] = defaultdict(list)
        for row in rejected:
            if row.get("terminal_net_bps") is not None:
                values[str(row.get(field, "UNKNOWN"))].append(
                    _as_float(row["terminal_net_bps"])
                )
        return {
            key: {
                "sample_size": len(group),
                "avoided_loss_bps": sum(abs(value) for value in group if value <= 0),
                "missed_profit_bps": sum(value for value in group if value > 0),
                "net_filter_benefit_bps": -sum(group),
            }
            for key, group in sorted(values.items())
        }

    return {
        "total_distinct_candidates": len(outcomes),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "correct_rejection_count": len(avoided),
        "avoided_losses": len(avoided),
        "missed_profitable_trades": len(missed),
        "average_avoided_loss_bps": statistics.fmean(avoided) if avoided else None,
        "median_avoided_loss_bps": statistics.median(avoided) if avoided else None,
        "average_missed_profit_bps": statistics.fmean(missed) if missed else None,
        "median_missed_profit_bps": statistics.median(missed) if missed else None,
        "gross_filtering_benefit_bps": sum(avoided),
        "missed_opportunity_cost_bps": sum(missed),
        "net_filter_benefit_bps": sum(avoided) - sum(missed),
        "candidate_acceptance_rate": len(accepted) / len(outcomes) if outcomes else None,
        "candidate_rejection_rate": len(rejected) / len(outcomes) if outcomes else None,
        "time_in_no_trade_seconds": no_trade_seconds,
        "no_trade_time_method": "SUM_OF_INTERVALS_AFTER_REJECTED_DIRECTIONAL_CANDIDATES",
        "per_regime_filter_benefit": grouped("regime"),
        "per_direction_filter_benefit": grouped("direction"),
        "unit": "BPS_PER_REFERENCE_NOTIONAL; NOT USDT PNL",
    }


DEFAULT_GATE_ORDER = (
    "DATA_FRESHNESS",
    "QUALITY",
    "LIQUIDITY",
    "SPREAD",
    "REFERENCE_HEALTH",
    "REGIME",
    "REGIME_PERSISTENCE",
    "SLOPE",
    "HYSTERESIS",
    "EXTENSION",
    "EXPECTED_EDGE",
    "COST",
    "SIGNAL_STRENGTH",
    "RISK",
    "FINAL_ACCEPTANCE",
)

REASON_GATE = {
    "STALE_DATA": "DATA_FRESHNESS",
    "WARMING_UP": "QUALITY",
    "NO_DIRECTION": "QUALITY",
    "DATA_QUALITY": "QUALITY",
    "L2_NOT_SYNCHRONIZED": "QUALITY",
    "STALE_OR_MISSING_BOOK": "DATA_FRESHNESS",
    "WAITING_FOR_SNAPSHOT": "QUALITY",
    "LIQUIDITY_LIMIT": "LIQUIDITY",
    "SPREAD_LIMIT": "SPREAD",
    "REFERENCE_UNAVAILABLE": "REFERENCE_HEALTH",
    "REFERENCE_ABNORMAL": "REFERENCE_HEALTH",
    "REFERENCE_NOT_HEALTHY": "REFERENCE_HEALTH",
    "REGIME_MISMATCH": "REGIME",
    "CHOP_REGIME": "REGIME",
    "REGIME_NOT_PERMITTED": "REGIME",
    "TREND_DISAGREES_WITH_FAIR_VALUE": "REGIME",
    "REGIME_NOT_PERSISTENT": "REGIME_PERSISTENCE",
    "SLOPE_TOO_FLAT": "SLOPE",
    "HYSTERESIS_BAND": "HYSTERESIS",
    "CHASE_TOO_EXTENDED": "EXTENSION",
    "EXTENDED_PRICE": "EXTENSION",
    "EXPECTED_NET_EDGE_BELOW_HURDLE": "EXPECTED_EDGE",
    "DISLOCATION_WITHIN_REFERENCE_UNCERTAINTY": "EXPECTED_EDGE",
    "REALIZABLE_EDGE_BELOW_HURDLE": "COST",
    "LOW_SIGNAL_STRENGTH": "SIGNAL_STRENGTH",
    "WHIPSAW_DIRECTION_FLIP": "QUALITY",
    "WAITING_FOR_PULLBACK": "QUALITY",
    "WAITING_FOR_REACCELERATION": "QUALITY",
    "IMPULSE_OBSERVATION_REQUIRED": "QUALITY",
    "STRUCTURAL_REVERSAL": "QUALITY",
    "MICROPRICE_DIVERGENCE": "QUALITY",
    "FLOW_DECELERATING": "QUALITY",
}


def entry_funnel(
    candidates: Sequence[SignalCandidate] | Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    records = [
        candidate.record() if isinstance(candidate, SignalCandidate) else dict(candidate)
        for candidate in candidates
    ]
    outcome_map = {str(row["candidate_id"]): row for row in outcomes}
    assigned = Counter[str]()
    contributions: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for record in records:
        reason = record.get("primary_rejection_reason") or record.get("rejection_reason")
        gate = REASON_GATE.get(str(reason), "FINAL_ACCEPTANCE")
        if str(record.get("decision")) in {"REJECTED", "REJECT"}:
            assigned[gate] += 1
            outcome = outcome_map.get(str(record.get("candidate_id")))
            if outcome is not None and outcome.get("terminal_net_bps") is not None:
                contributions[gate].append(outcome)
    total = len(records)
    survival = total
    rows: list[dict[str, object]] = []
    for gate in DEFAULT_GATE_ORDER:
        rejected = assigned[gate]
        input_count = survival
        survival = max(0, survival - rejected)
        gate_outcomes = contributions[gate]
        values = [_as_float(outcome["terminal_net_bps"]) for outcome in gate_outcomes]
        mfe_values = [
            _as_float(outcome["mfe_bps"])
            for outcome in gate_outcomes
            if outcome.get("mfe_bps") is not None
        ]
        mae_values = [
            _as_float(outcome["mae_bps"])
            for outcome in gate_outcomes
            if outcome.get("mae_bps") is not None
        ]
        avoided = sum(abs(value) for value in values if value <= 0)
        missed = sum(value for value in values if value > 0)
        rows.append(
            {
                "gate": gate,
                "input_count": input_count,
                "pass_count": survival,
                "reject_count": rejected,
                "incremental_reject_pct": rejected / input_count if input_count else 0.0,
                "cumulative_survival_pct": survival / total if total else 0.0,
                "counterfactual_rejected_net_bps": sum(values) if values else None,
                "average_rejected_mfe_bps": statistics.fmean(mfe_values)
                if mfe_values
                else None,
                "average_rejected_mae_bps": statistics.fmean(mae_values)
                if mae_values
                else None,
                "missed_opportunity_bps": missed if values else None,
                "avoided_loss_bps": avoided if values else None,
                "net_filter_contribution_bps": avoided - missed if values else None,
            }
        )
    reasons = Counter(
        str(record.get("primary_rejection_reason") or record.get("rejection_reason"))
        for record in records
        if str(record.get("decision")) in {"REJECTED", "REJECT"}
    )
    return {
        "generated": total,
        "accepted": sum(
            1 for record in records if str(record.get("decision")) in {"ACCEPTED", "ALLOW"}
        ),
        "rejected": sum(
            1 for record in records if str(record.get("decision")) in {"REJECTED", "REJECT"}
        ),
        "gates": rows,
        "rejection_reasons": dict(reasons.most_common()),
        "binding_gates": [
            {"gate": gate, "reject_count": count}
            for gate, count in assigned.most_common(5)
        ],
    }


def _pnl_metrics(values: Sequence[float]) -> dict[str, object]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "sample_size": len(values),
        "net_pnl_bps": sum(values),
        "net_expectancy_bps": statistics.fmean(values) if values else None,
        "win_rate": len(wins) / len(values) if values else None,
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "maximum_drawdown_bps": max_drawdown,
    }


def replay_metrics(
    candidates: Sequence[SignalCandidate], outcomes: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    accepted_ids = {
        candidate.candidate_id for candidate in candidates if candidate.decision == "ACCEPTED"
    }
    values = [
        float(cast(float, row["simulated_net_bps"]))
        for row in outcomes
        if row.get("candidate_id") in accepted_ids and row.get("simulated_net_bps") is not None
    ]
    directional = [
        candidate for candidate in candidates if candidate.direction in {"LONG", "SHORT"}
    ]
    gross_values = [
        float(cast(float, row["terminal_net_bps"]))
        + _as_float(cast(dict[str, object], row["cost"])["total_cost_bps"])
        for row in outcomes
        if row.get("candidate_id") in accepted_ids and row.get("terminal_net_bps") is not None
    ]
    costs = [
        _as_float(cast(dict[str, object], row["cost"])["total_cost_bps"])
        for row in outcomes
        if row.get("candidate_id") in accepted_ids and row.get("terminal_net_bps") is not None
    ]
    result = _pnl_metrics(values)
    result.update(
        {
            "candidate_count": len(candidates),
            "directional_candidate_count": len(directional),
            "accepted_count": len(accepted_ids),
            "rejected_count": len(candidates) - len(accepted_ids),
            "acceptance_rate": len(accepted_ids) / len(candidates) if candidates else None,
            "gross_pnl_bps": sum(gross_values),
            "gross_expectancy_bps": statistics.fmean(gross_values) if gross_values else None,
            "total_cost_bps": sum(costs),
            "average_mfe_bps": statistics.fmean(
                float(cast(float, row["mfe_bps"]))
                for row in outcomes
                if row.get("candidate_id") in accepted_ids
            )
            if accepted_ids
            else None,
            "average_mae_bps": statistics.fmean(
                float(cast(float, row["mae_bps"]))
                for row in outcomes
                if row.get("candidate_id") in accepted_ids
            )
            if accepted_ids
            else None,
            "unit": "BPS_PER_REFERENCE_NOTIONAL; NO CAPITAL SIZING",
        }
    )
    return result


def validation_plan(
    events: Sequence[DatasetEvent], settings: ResearchSettings
) -> dict[str, object]:
    if len(events) < 4:
        raise ValueError("at least four chronological events are required")
    names = ("SELECTION", "VALIDATION", "TEST", FINAL_HOLDOUT)
    fractions = settings.stage_fractions
    boundaries = [0]
    cumulative = Decimal(0)
    for fraction in fractions[:-1]:
        cumulative += fraction
        boundaries.append(min(len(events) - 1, int(Decimal(len(events)) * cumulative)))
    boundaries.append(len(events))
    stages: dict[str, object] = {}
    for index, name in enumerate(names):
        raw_start = boundaries[index]
        raw_stop = boundaries[index + 1]
        start_time = events[raw_start].event_time
        stop_time = events[raw_stop - 1].event_time
        effective_start = (
            start_time + timedelta(seconds=settings.embargo_seconds) if index else start_time
        )
        effective_stop = (
            stop_time - timedelta(seconds=settings.purge_seconds)
            if index < len(names) - 1
            else stop_time
        )
        stages[name] = {
            "raw_first_position": raw_start + 1,
            "raw_last_position": raw_stop,
            "raw_start": start_time.isoformat().replace("+00:00", "Z"),
            "raw_end": stop_time.isoformat().replace("+00:00", "Z"),
            "effective_start": effective_start.isoformat().replace("+00:00", "Z"),
            "effective_end": effective_stop.isoformat().replace("+00:00", "Z"),
            "purge_seconds": settings.purge_seconds if index < len(names) - 1 else 0,
            "embargo_seconds": settings.embargo_seconds if index else 0,
            "sealed": name == FINAL_HOLDOUT,
        }
    block = max(1, len(events) // 5)
    anchored: list[dict[str, int]] = []
    rolling: list[dict[str, int]] = []
    for fold in range(1, 4):
        validation_start = min(len(events), block * (fold + 1))
        validation_end = min(len(events), validation_start + block)
        if validation_start >= validation_end:
            continue
        anchored.append(
            {
                "fold": fold,
                "train_first_position": 1,
                "train_last_position": max(1, validation_start - 1),
                "validate_first_position": validation_start + 1,
                "validate_last_position": validation_end,
            }
        )
        rolling.append(
            {
                "fold": fold,
                "train_first_position": max(1, validation_start - block + 1),
                "train_last_position": max(1, validation_start - 1),
                "validate_first_position": validation_start + 1,
                "validate_last_position": validation_end,
            }
        )
    return {
        "method": "CHRONOLOGICAL_50_20_15_15",
        "shuffled": False,
        "stages": stages,
        "anchored_walk_forward": anchored,
        "rolling_walk_forward": rolling,
        "holdout_policy": "SEALED_UNTIL_STRATEGY_AND_PARAMETER_HASHES_ARE_FROZEN",
    }


def seal_final_holdout(
    experiment_path: Path,
    dataset_id: str,
    strategy_hash: str,
    parameter_hash: str,
) -> dict[str, object]:
    path = experiment_path / "final-holdout-seal.json"
    identity = {
        "dataset_id": dataset_id,
        "strategy_hash": strategy_hash,
        "parameter_hash": parameter_hash,
    }
    seal: dict[str, object] = {
        **identity,
        "sealed_at": _utc_now(),
        "seal_hash": _sha256(_canonical(identity)),
    }
    if path.exists():
        existing = _json_object(path)
        if any(existing.get(key) != value for key, value in identity.items()):
            raise ValueError("final holdout is sealed to a different strategy identity")
        return existing
    _write_new(path, _canonical(seal) + b"\n")
    return seal


def stage_metrics(
    outcomes: Sequence[Mapping[str, object]], validation: Mapping[str, object]
) -> dict[str, object]:
    stages = cast(dict[str, dict[str, object]], validation["stages"])
    result: dict[str, object] = {}
    for name, stage in stages.items():
        start = _parse_utc(stage["effective_start"])
        end = _parse_utc(stage["effective_end"])
        values = [
            float(cast(float, row["simulated_net_bps"]))
            for row in outcomes
            if row.get("decision") == "ACCEPTED"
            and row.get("simulated_net_bps") is not None
            and start <= _parse_utc(row["timestamp"]) <= end
        ]
        result[name] = _pnl_metrics(values)
    return result


def evaluate_promotion(
    metrics: Mapping[str, object],
    stages: Mapping[str, object],
    stressed_metrics: Mapping[str, object],
    settings: ResearchSettings,
    *,
    temporal_windows: int,
    regimes: int,
    parameter_stability: str,
    data_integrity: str,
) -> dict[str, object]:
    reasons: list[str] = []
    sample = _as_int(metrics.get("sample_size", 0))
    if sample < settings.minimum_promotion_trades:
        reasons.append("INSUFFICIENT_INDEPENDENT_SAMPLE")
    if _as_float(metrics.get("gross_expectancy_bps") or 0) <= 0:
        reasons.append("NON_POSITIVE_GROSS_EXPECTANCY")
    if _as_float(metrics.get("net_expectancy_bps") or 0) <= 0:
        reasons.append("NON_POSITIVE_NET_EXPECTANCY")
    profit_factor = metrics.get("profit_factor")
    if profit_factor is None or Decimal(str(profit_factor)) < settings.minimum_profit_factor:
        reasons.append("PROFIT_FACTOR_BELOW_FLOOR")
    for stage_name in ("TEST", FINAL_HOLDOUT):
        stage = cast(Mapping[str, object], stages.get(stage_name, {}))
        if _as_int(stage.get("sample_size", 0)) == 0:
            reasons.append(f"{stage_name}_INSUFFICIENT_EVIDENCE")
        elif _as_float(stage.get("net_expectancy_bps") or 0) <= 0:
            reasons.append(f"{stage_name}_FAILED")
    if _as_float(stressed_metrics.get("net_expectancy_bps") or 0) <= 0:
        reasons.append("STRESSED_EXECUTION_FAILED")
    if _as_float(metrics.get("maximum_drawdown_bps") or 0) / 100 > float(
        settings.maximum_drawdown_pct
    ):
        reasons.append("MAXIMUM_DRAWDOWN_EXCEEDED")
    if temporal_windows < settings.minimum_temporal_windows:
        reasons.append("INSUFFICIENT_TEMPORAL_WINDOWS")
    if regimes < settings.minimum_regimes:
        reasons.append("INSUFFICIENT_REGIME_COVERAGE")
    if parameter_stability != "PASS":
        reasons.append("PARAMETER_STABILITY_NOT_PROVEN")
    if data_integrity != "VALID":
        reasons.append("DATA_INTEGRITY_FAILED")
    return {
        "promotion_eligible": not reasons,
        "automatic_promotion": False,
        "operator_approval_required": True,
        "paper_activation_required_separately": True,
        "live_transition_available": False,
        "reasons": reasons,
    }


def calibration_diagnostics(
    outcomes: Sequence[Mapping[str, object]], *, minimum_sample: int = 200
) -> dict[str, object]:
    accepted = [row for row in outcomes if row.get("decision") == "ACCEPTED"]
    return {
        "status": "UNCALIBRATED",
        "sample_size": len(accepted),
        "minimum_sample": minimum_sample,
        "calibrated_probability": None,
        "brier_score": None,
        "expected_calibration_error": None,
        "reason": (
            "INSUFFICIENT_OUT_OF_SAMPLE_ACCEPTED_CANDIDATES"
            if len(accepted) < minimum_sample
            else "SIGNAL_STRENGTH_IS_HEURISTIC_NOT_A_PROBABILITY"
        ),
    }


def _hash_chain_records(records: Iterable[Mapping[str, object]], event_type: str) -> bytes:
    lines: list[bytes] = []
    previous = ZERO_HASH
    for sequence, payload in enumerate(records, 1):
        envelope: dict[str, object] = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "sequence": sequence,
            "event_type": event_type,
            "previous_hash": previous,
            "payload": dict(payload),
        }
        record_hash = _sha256(previous.encode() + _canonical(envelope))
        envelope["record_hash"] = record_hash
        lines.append(_canonical(envelope) + b"\n")
        previous = record_hash
    return b"".join(lines)


def write_candidate_ledger(path: Path, candidates: Sequence[SignalCandidate]) -> dict[str, object]:
    data = _hash_chain_records((candidate.record() for candidate in candidates), "candidate")
    _write_new(path, data)
    return {"path": str(path), "sha256": _sha256(data), "records": len(candidates)}


def write_counterfactual_ledger(
    path: Path, outcomes: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    data = _hash_chain_records(outcomes, "counterfactual_outcome")
    _write_new(path, data)
    return {"path": str(path), "sha256": _sha256(data), "records": len(outcomes)}


def verify_hash_chain(path: Path) -> list[dict[str, object]]:
    previous = ZERO_HASH
    records: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"{path} line {line_number} is not an object")
            record = cast(dict[str, object], raw)
            stored = str(record.pop("record_hash", ""))
            if record.get("previous_hash") != previous:
                raise ValueError(f"{path} line {line_number} previous hash mismatch")
            expected = _sha256(previous.encode() + _canonical(record))
            if stored != expected:
                raise ValueError(f"{path} line {line_number} record hash mismatch")
            if _as_int(record.get("sequence", 0)) != line_number:
                raise ValueError(f"{path} line {line_number} sequence mismatch")
            record["record_hash"] = stored
            records.append(record)
            previous = stored
    return records


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"research registry is locked: {path}") from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode())
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


class AppendOnlyRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def records(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        return verify_hash_chain(self.path)

    def append(self, event_type: str, payload: Mapping[str, object]) -> dict[str, object]:
        with _exclusive_lock(self.lock_path):
            existing = self.records()
            sequence = len(existing) + 1
            previous = str(existing[-1]["record_hash"]) if existing else ZERO_HASH
            envelope: dict[str, object] = {
                "schema_version": REGISTRY_SCHEMA_VERSION,
                "sequence": sequence,
                "event_type": event_type,
                "recorded_at_utc": _utc_now(),
                "previous_hash": previous,
                "payload": dict(payload),
            }
            record_hash = _sha256(previous.encode() + _canonical(envelope))
            envelope["record_hash"] = record_hash
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as stream:
                stream.write(_canonical(envelope) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            return envelope


class LifecycleState(StrEnum):
    EXPERIMENTAL = "EXPERIMENTAL"
    OBSERVING = "OBSERVING"
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    PAPER_ACTIVE = "PAPER_ACTIVE"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"
    FAILED = "FAILED"


INITIAL_LIFECYCLE: tuple[dict[str, object], ...] = (
    {
        "strategy_id": "REST_MOMENTUM_TOURNAMENT_V2",
        "strategy_version": "2",
        "state": "RETIRED",
        "reason": "NEGATIVE_GROSS_AND_NET_EDGE_ACROSS_CHRONOLOGICAL_SEGMENTS",
        "reconsider_only_if": "MATERIAL_SIGNAL_LOGIC_CHANGE_WITH_NEW_VERSION",
    },
    {
        "strategy_id": "XAU_KAMA20_RAW_V2",
        "strategy_version": "2",
        "state": "QUARANTINED",
        "reason": "COST_DOMINATED_NEGATIVE_NET_EVIDENCE",
    },
    {
        "strategy_id": "XAU_KAMA20_FILTERED_V2",
        "strategy_version": "2",
        "state": "OBSERVING",
        "reason": "INSUFFICIENT_ACCEPTED_SAMPLE",
    },
    {
        "strategy_id": "MICROSTRUCTURE_ENTRY_V3",
        "strategy_version": "3",
        "state": "EXPERIMENTAL",
        "reason": "SHADOW_ONLY_ZERO_ACCEPTED_OUTCOMES",
    },
    {
        "strategy_id": "XAU_FAIR_VALUE_V1",
        "strategy_version": "1",
        "state": "EXPERIMENTAL",
        "reason": "RESEARCH_ONLY",
    },
    {
        "strategy_id": "XAU_COMBINED_V1",
        "strategy_version": "1",
        "state": "EXPERIMENTAL",
        "reason": "RESEARCH_ONLY",
    },
)


class LifecycleRegistry:
    def __init__(self, research_root: Path) -> None:
        self.research_root = research_root
        self.store = AppendOnlyRegistry(research_root / "lifecycle.jsonl")

    def initialize(self, *, git_commit: str) -> None:
        if self.store.records():
            return
        for item in INITIAL_LIFECYCLE:
            self.store.append("lifecycle_initialized", {**item, "git_commit": git_commit})
        baseline = INITIAL_LIFECYCLE[0]
        retired = self.research_root / "retired" / "REST_MOMENTUM_TOURNAMENT_V2-v2.json"
        if not retired.exists():
            _write_new(
                retired,
                _canonical(
                    {
                        **baseline,
                        "retired_at": _utc_now(),
                        "sample_size": 427,
                        "metrics": {
                            "gross_pnl_usdt": -12.17927339,
                            "net_pnl_usdt": -24.84421854,
                            "profit_factor": 0.5062,
                            "expectancy_usdt": -0.05818318,
                        },
                        "git_commit": git_commit,
                        "source": "AUDIT_REPORT_2026-09-23_AUTOTRADE_7_AMA_CONTROL",
                    }
                )
                + b"\n",
            )

    def latest(self) -> dict[str, dict[str, object]]:
        latest: dict[str, dict[str, object]] = {}
        for record in self.store.records():
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            key = f"{payload.get('strategy_id')}:{payload.get('strategy_version')}"
            latest[key] = cast(dict[str, object], payload)
        return latest

    def operator_transition(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        target: LifecycleState,
        operator: str,
        reason: str,
        promotion_evidence: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if not operator.strip() or not reason.strip():
            raise ValueError("operator and reason are required")
        if target not in {LifecycleState.PAPER_ELIGIBLE, LifecycleState.PAPER_ACTIVE}:
            raise ValueError("this command only performs explicit PAPER approval transitions")
        current = self.latest().get(f"{strategy_id}:{strategy_version}")
        if current is None:
            raise ValueError("strategy lifecycle identity is unknown")
        current_state = str(current.get("state"))
        if target == LifecycleState.PAPER_ELIGIBLE:
            if current_state != LifecycleState.VALIDATED:
                raise ValueError("only a VALIDATED strategy can become PAPER_ELIGIBLE")
            if not promotion_evidence or promotion_evidence.get("promotion_eligible") is not True:
                raise ValueError("promotion evidence does not permit PAPER eligibility")
        elif current_state != LifecycleState.PAPER_ELIGIBLE:
            raise ValueError("only a PAPER_ELIGIBLE strategy can become PAPER_ACTIVE")
        payload = {
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "previous_state": current_state,
            "state": target.value,
            "operator": operator.strip(),
            "reason": reason.strip(),
            "manual_approval": True,
            "live_permission": False,
            "promotion_evidence_hash": _sha256(_canonical(promotion_evidence or {})),
        }
        self.store.append("manual_paper_transition", payload)
        return payload


def experiment_identity(
    *,
    dataset_manifest: Mapping[str, object],
    strategy: StrategyProtocol,
    profile: ExecutionProfile,
    git_commit: str,
    config_hash: str,
    seed: int,
) -> dict[str, object]:
    identity = {
        "dataset_id": dataset_manifest["dataset_id"],
        "dataset_hash": dataset_manifest["identity_hash"],
        "strategy_id": strategy.strategy_id,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "parameter_hash": strategy.parameter_hash,
        "execution_profile": profile.profile_id,
        "execution_profile_version": profile.version,
        "git_commit": git_commit,
        "config_hash": config_hash,
        "random_seed": seed,
    }
    return {"experiment_id": _sha256(_canonical(identity))[:32], **identity}


def _snapshot_jsonl(path: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    prefix = _prefix_bytes(path)
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(prefix.splitlines(), 1):
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path} line {line_number} is not an object")
        rows.append(cast(dict[str, object], value))
    return rows, {
        "path": str(path.resolve()),
        "prefix_bytes": len(prefix),
        "prefix_sha256": _sha256(prefix),
        "record_count": len(rows),
    }


def analyze_ama_evidence(source_path: Path) -> dict[str, object]:
    rows, source = _snapshot_jsonl(source_path)
    _verify_source_chain(rows)
    decisions: list[dict[str, object]] = []
    outcomes_by_id: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row.get("event_type") == "decision" and payload.get("strategy_id") == (
            "XAU_KAMA20_FILTERED_V2"
        ):
            decisions.append(cast(dict[str, object], payload))
        elif row.get("event_type") == "counterfactual_outcome" and payload.get(
            "strategy_id"
        ) == "XAU_KAMA20_FILTERED_V2":
            outcomes_by_id[str(payload.get("decision_id"))].append(
                cast(dict[str, object], payload)
            )
    candidates: list[dict[str, object]] = []
    normalized_outcomes: list[dict[str, object]] = []
    for decision in decisions:
        candidate_id = str(decision["decision_id"])
        reason = decision.get("rejection_reason")
        candidate = {
            "candidate_id": candidate_id,
            "timestamp": decision.get("observed_at_utc"),
            "direction": decision.get("candidate_direction"),
            "decision": "ACCEPTED" if decision.get("decision") == "ALLOW" else "REJECTED",
            "primary_rejection_reason": reason,
            "all_rejection_reasons": [reason] if reason else [],
        }
        candidates.append(candidate)
        horizon_rows = sorted(
            outcomes_by_id.get(candidate_id, []),
            key=lambda row: _as_int(row.get("horizon_seconds", 0)),
        )
        if not horizon_rows:
            continue
        terminal = horizon_rows[-1]
        normalized_outcomes.append(
            {
                "candidate_id": candidate_id,
                "timestamp": decision.get("observed_at_utc"),
                "direction": decision.get("candidate_direction"),
                "regime": decision.get("regime", "UNKNOWN"),
                "decision": candidate["decision"],
                "terminal_net_bps": terminal.get("modeled_net_return_bps"),
                "mfe_bps": max(_as_float(row.get("mfe_bps", 0)) for row in horizon_rows),
                "mae_bps": min(_as_float(row.get("mae_bps", 0)) for row in horizon_rows),
            }
        )
    return {
        "strategy_id": "XAU_KAMA20_FILTERED_V2",
        "source": source,
        "distinct_candidate_count": len(candidates),
        "resolved_candidate_count": len(normalized_outcomes),
        "funnel": entry_funnel(candidates, normalized_outcomes),
        "counterfactual": counterfactual_summary(normalized_outcomes),
        "limitations": [
            "The source stores only the first binding rejection reason, not every failed gate.",
            "Receive time is the observation clock.",
            "Modeled outcomes are not executable fills.",
        ],
    }


def analyze_entry_v3_evidence(source_path: Path | Sequence[Path]) -> dict[str, object]:
    paths = (source_path,) if isinstance(source_path, Path) else tuple(source_path)
    if not paths:
        raise ValueError("at least one Entry V3 capture is required")
    normalized: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    candidate_ids: set[str] = set()
    no_trade_seconds = 0.0
    for path in paths:
        rows, source = _snapshot_jsonl(path)
        sources.append(source)
        timestamps: list[int] = []
        for row in rows:
            if row.get("event_type") != "entry_candidate":
                continue
            candidate_id = str(row.get("candidate_id") or "")
            if not candidate_id:
                raise ValueError(f"{path} contains an Entry V3 candidate without identity")
            if candidate_id in candidate_ids:
                raise ValueError(f"duplicate Entry V3 candidate identity: {candidate_id}")
            candidate_ids.add(candidate_id)
            if row.get("decision_timestamp") is not None:
                timestamps.append(_as_int(row["decision_timestamp"]))
            normalized.append(
                {
                    "candidate_id": candidate_id,
                    "timestamp": row.get("decision_timestamp"),
                    "direction": row.get("direction"),
                    "decision": row.get("decision"),
                    "primary_rejection_reason": row.get("rejection_reason"),
                    "all_rejection_reasons": [row["rejection_reason"]]
                    if row.get("rejection_reason")
                    else [],
                }
            )
        if len(timestamps) > 1:
            no_trade_seconds += max(timestamps) / 1000 - min(timestamps) / 1000
    source = {
        "capture_count": len(sources),
        "prefix_bytes": sum(_as_int(item["prefix_bytes"]) for item in sources),
        "record_count": sum(_as_int(item["record_count"]) for item in sources),
        "identity_hash": _sha256(_canonical(sources)),
        "captures": sources,
    }
    return {
        "strategy_id": "MICROSTRUCTURE_ENTRY_V3",
        "source": source,
        "distinct_candidate_count": len(normalized),
        "funnel": entry_funnel(normalized),
        "counterfactual": {
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": "NO_ACCEPTED_TRADES_OR_FORWARD_OUTCOMES_IN_ENTRY_V3_DECISION_LEDGER",
            "time_in_no_trade_seconds": no_trade_seconds,
            "time_method": "SUM_OF_CAPTURE_INTERVALS_FIRST_TO_LAST_CANDIDATE",
        },
        "limitations": [
            "These are live shadow capture prefixes, not a frozen same-timeline replay.",
            "Operational gaps between capture files are excluded from no-trade time.",
            "Only the first binding rejection reason is present.",
            "No accepted candidates exist, so threshold loosening is not justified.",
        ],
    }


def unsupported_strategy_results() -> list[dict[str, object]]:
    return [
        {
            "strategy_id": "REST_MOMENTUM_TOURNAMENT_V2",
            "strategy_version": "2",
            "status": "NOT_RECONSTRUCTABLE",
            "reason": "XAU dataset lacks the original multi-symbol momentum timeline.",
            "lifecycle_state": "RETIRED",
            "promotion_eligible": False,
        },
        {
            "strategy_id": "MICROSTRUCTURE_ENTRY_V3",
            "strategy_version": "3",
            "status": "NOT_RECONSTRUCTABLE",
            "reason": "XAU dataset lacks L2, trades, exchange timestamps, and synchronized books.",
            "lifecycle_state": "EXPERIMENTAL",
            "promotion_eligible": False,
        },
    ]


def write_json(path: Path, value: object, *, replace_existing: bool = False) -> None:
    data = _canonical(value) + b"\n"
    if replace_existing:
        _atomic_replace(path, data)
    else:
        _write_new(path, data)
