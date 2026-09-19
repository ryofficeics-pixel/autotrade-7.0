from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from statistics import median
from time import monotonic
from typing import cast
from uuid import uuid4

SCHEMA_VERSION = 2
STRATEGY_RAW = "XAU_KAMA20_RAW_V2"
STRATEGY_FILTERED = "XAU_KAMA20_FILTERED_V2"
CONTROL_NAME = "XAU_KAMA20_CONTROL_V2"
HORIZONS_SECONDS = (10, 30, 60, 180, 300, 900)
ZERO_HASH = "0" * 64


class AmaRegime(StrEnum):
    STRONG_UPTREND = "STRONG_UPTREND"
    UPTREND = "UPTREND"
    STRONG_DOWNTREND = "STRONG_DOWNTREND"
    DOWNTREND = "DOWNTREND"
    CHOP = "CHOP"
    TRANSITION = "TRANSITION"
    UNKNOWN = "UNKNOWN"


class QualityDecision(StrEnum):
    ALLOW = "ALLOW"
    WARMING_UP = "WARMING_UP"
    NO_DIRECTION = "NO_DIRECTION"
    STALE_DATA = "STALE_DATA"
    SPREAD_LIMIT = "SPREAD_LIMIT"
    REFERENCE_UNAVAILABLE = "REFERENCE_UNAVAILABLE"
    REFERENCE_ABNORMAL = "REFERENCE_ABNORMAL"
    REGIME_MISMATCH = "REGIME_MISMATCH"
    REGIME_NOT_PERSISTENT = "REGIME_NOT_PERSISTENT"
    HYSTERESIS_BAND = "HYSTERESIS_BAND"
    SLOPE_TOO_FLAT = "SLOPE_TOO_FLAT"
    EXTENDED_PRICE = "EXTENDED_PRICE"
    INSUFFICIENT_NET_EDGE = "INSUFFICIENT_NET_EDGE"
    LIQUIDITY_LIMIT = "LIQUIDITY_LIMIT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    RISK_CONSTRAINT = "RISK_CONSTRAINT"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True)
class AmaControlSettings:
    enabled: bool
    execution_enabled: bool
    fast_period: int
    control_period: int
    slow_period: int
    kama_fast: int
    kama_slow: int
    atr_proxy_window: int
    atr_multiplier: Decimal
    minimum_hysteresis_bps: Decimal
    minimum_slope_bps: Decimal
    maximum_distance_bps: Decimal
    minimum_net_edge_bps: Decimal
    minimum_confidence: Decimal
    minimum_regime_observations: int
    maximum_spread_bps: Decimal
    minimum_quote_volume_usdt: Decimal
    maximum_reference_dispersion_bps: Decimal
    maximum_reference_dislocation_bps: Decimal
    reference_required: bool
    notional_usdt: Decimal
    maximum_holding_seconds: int
    round_trip_fee_bps: Decimal
    slippage_bps_per_side: Decimal
    funding_buffer_bps: Decimal
    stressed_cost_multiplier: Decimal
    minimum_comparison_trades: int
    quarantine_minimum_trades: int
    quarantine_profit_factor: Decimal
    decision_interval_seconds: int
    report_interval_seconds: int


@dataclass
class _ShadowPosition:
    direction: str
    regime: str
    entry_price: Decimal
    entry_time: float
    entry_utc: str
    observation_id: str
    maximum_favorable_bps: Decimal = Decimal(0)
    maximum_adverse_bps: Decimal = Decimal(0)


@dataclass
class _PendingCounterfactual:
    decision_id: str
    strategy_id: str
    direction: str
    entry_price: Decimal
    observed_at_monotonic: float
    observed_at_utc: str
    rejection_reason: str | None
    cost_bps: Decimal
    maximum_favorable_bps: Decimal = Decimal(0)
    maximum_adverse_bps: Decimal = Decimal(0)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _number(value: Decimal | None, digits: int = 8) -> float | None:
    return None if value is None else round(float(value), digits)


def _canonical(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode()


class XauReferenceEngine:
    def __init__(self, settings: AmaControlSettings) -> None:
        self._settings = settings

    def evaluate(self, markets: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
        prices: dict[str, Decimal] = {}
        for symbol in ("XAU_USDT", "XAUT_USDT", "PAXG_USDT"):
            market = markets.get(symbol)
            if market is None:
                continue
            try:
                price = Decimal(str(market["last"]))
            except (KeyError, InvalidOperation, ValueError):
                continue
            if price.is_finite() and price > 0:
                prices[symbol] = price

        reference_prices = [
            prices[symbol] for symbol in ("XAUT_USDT", "PAXG_USDT") if symbol in prices
        ]
        if not reference_prices:
            return {
                "status": "UNAVAILABLE",
                "fair_value": None,
                "dispersion_bps": None,
                "dislocation_bps": None,
                "sources": {symbol: _number(value) for symbol, value in prices.items()},
                "reason": "NO_REFERENCE_PRICE",
            }

        fair_value = Decimal(str(median(reference_prices)))
        dispersion = (
            (max(reference_prices) - min(reference_prices)) / fair_value * Decimal(10_000)
            if len(reference_prices) > 1
            else Decimal(0)
        )
        primary = prices.get("XAU_USDT")
        dislocation = (
            (primary - fair_value) / fair_value * Decimal(10_000) if primary is not None else None
        )
        abnormal = dispersion > self._settings.maximum_reference_dispersion_bps or (
            dislocation is not None
            and abs(dislocation) > self._settings.maximum_reference_dislocation_bps
        )
        status = "ABNORMAL" if abnormal else "HEALTHY" if len(reference_prices) == 2 else "DEGRADED"
        reason = (
            "REFERENCE_DIVERGENCE"
            if abnormal
            else "ONE_REFERENCE_ONLY"
            if len(reference_prices) == 1
            else "WITHIN_LIMITS"
        )
        return {
            "status": status,
            "fair_value": _number(fair_value),
            "dispersion_bps": _number(dispersion, 4),
            "dislocation_bps": _number(dislocation, 4),
            "sources": {symbol: _number(value) for symbol, value in prices.items()},
            "reason": reason,
        }


class EntryQualityGate:
    def __init__(self, settings: AmaControlSettings) -> None:
        self._settings = settings

    def decide(
        self,
        *,
        ready: bool,
        direction: str,
        regime: AmaRegime,
        distance_bps: Decimal,
        slope_bps: Decimal,
        hysteresis_bps: Decimal,
        net_edge_bps: Decimal,
        confidence: Decimal,
        spread_bps: Decimal,
        quote_volume_usdt: Decimal,
        reference_status: str,
        stale: bool,
        quarantined: bool,
        regime_persistent: bool,
        risk_valid: bool,
    ) -> QualityDecision:
        if quarantined:
            return QualityDecision.QUARANTINED
        if not ready:
            return QualityDecision.WARMING_UP
        if stale:
            return QualityDecision.STALE_DATA
        if not risk_valid:
            return QualityDecision.RISK_CONSTRAINT
        if direction == "WAIT":
            return QualityDecision.NO_DIRECTION
        if spread_bps > self._settings.maximum_spread_bps:
            return QualityDecision.SPREAD_LIMIT
        if quote_volume_usdt < self._settings.minimum_quote_volume_usdt:
            return QualityDecision.LIQUIDITY_LIMIT
        if self._settings.reference_required and reference_status in {"UNAVAILABLE", "DEGRADED"}:
            return QualityDecision.REFERENCE_UNAVAILABLE
        if reference_status == "ABNORMAL":
            return QualityDecision.REFERENCE_ABNORMAL
        aligned = (
            direction == "LONG" and regime in {AmaRegime.STRONG_UPTREND, AmaRegime.UPTREND}
        ) or (direction == "SHORT" and regime in {AmaRegime.STRONG_DOWNTREND, AmaRegime.DOWNTREND})
        if not aligned:
            return QualityDecision.REGIME_MISMATCH
        if not regime_persistent:
            return QualityDecision.REGIME_NOT_PERSISTENT
        if abs(slope_bps) < self._settings.minimum_slope_bps:
            return QualityDecision.SLOPE_TOO_FLAT
        if distance_bps < hysteresis_bps:
            return QualityDecision.HYSTERESIS_BAND
        if distance_bps > self._settings.maximum_distance_bps:
            return QualityDecision.EXTENDED_PRICE
        if net_edge_bps < self._settings.minimum_net_edge_bps:
            return QualityDecision.INSUFFICIENT_NET_EDGE
        if confidence < self._settings.minimum_confidence:
            return QualityDecision.LOW_CONFIDENCE
        return QualityDecision.ALLOW


class AmaControlEngine:
    """Independent, execution-disabled XAU KAMA control and evidence recorder."""

    def __init__(
        self,
        settings: AmaControlSettings,
        *,
        project_root: Path,
        run_id: str,
        session_id: str,
    ) -> None:
        if settings.execution_enabled:
            raise ValueError("AMA control execution_enabled must remain false")
        self.settings = settings
        self.run_id = run_id
        self.session_id = session_id
        self.parameter_id = hashlib.sha256(_canonical(asdict(settings))).hexdigest()[:16]
        self._prices: deque[tuple[float, Decimal]] = deque(
            maxlen=max(512, settings.slow_period * 4)
        )
        self._kama: dict[int, Decimal] = {}
        self._previous_kama: dict[int, Decimal] = {}
        self._last_regime = AmaRegime.UNKNOWN
        self._regime_observations = 0
        self._positions: dict[str, _ShadowPosition | None] = {
            STRATEGY_RAW: None,
            STRATEGY_FILTERED: None,
        }
        self._external_benchmarks: dict[str, dict[str, object]] = {}
        self._trades: dict[str, list[dict[str, object]]] = defaultdict(list)
        self._pending: list[_PendingCounterfactual] = []
        self._resolved: set[tuple[str, int]] = set()
        self._observation_count = 0
        self._decision_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"records": 0, "candidates": 0, "allowed": 0, "rejected": 0}
        )
        self._rejection_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self._counterfactual_total = 0
        self._counterfactual_net_bps = Decimal(0)
        self._counterfactual_classifications: dict[str, int] = defaultdict(int)
        self._counterfactual_horizons: dict[int, int] = defaultdict(int)
        self._reference = XauReferenceEngine(settings)
        self._gate = EntryQualityGate(settings)
        self._sequence = 0
        self._previous_hash = ZERO_HASH
        self._integrity = "VALID"
        self._integrity_error: str | None = None
        self._last_report_at = 0.0
        self._last_decision_at: float | None = None
        self._last_decision_signature: tuple[str, ...] | None = None
        self._last_snapshot = self._empty_snapshot()
        self._path = project_root / "data" / "runs" / run_id / "ama-control-v2.jsonl"
        self._reports = project_root / "reports"
        self._load_evidence()
        self._last_snapshot = self._empty_snapshot()
        raw_metrics = self._metrics(STRATEGY_RAW)
        filtered_metrics = self._metrics(STRATEGY_FILTERED)
        strategies = cast(dict[str, object], self._last_snapshot["strategies"])
        strategies[STRATEGY_RAW] = raw_metrics
        strategies[STRATEGY_FILTERED] = filtered_metrics
        self._last_snapshot["comparison"] = self._comparison(raw_metrics, filtered_metrics)
        self._last_snapshot["quarantine"] = self._quarantine_state(STRATEGY_FILTERED)
        self._last_snapshot["counterfactual_pending"] = len(self._pending)

    def update(
        self,
        markets: Mapping[str, Mapping[str, object]],
        *,
        observed_at: float | None = None,
        observed_at_utc: str | None = None,
    ) -> dict[str, object]:
        now = monotonic() if observed_at is None else observed_at
        timestamp = observed_at_utc or _utc_now()
        market = markets.get("XAU_USDT")
        if not self.settings.enabled:
            self._last_snapshot = self._empty_snapshot(status="DISABLED")
            return self.snapshot()
        if market is None:
            self._last_snapshot = self._empty_snapshot(status="WAITING_MARKET")
            return self.snapshot()
        try:
            price = Decimal(str(market["last"]))
            spread = Decimal(str(market.get("spread_bps", "1000000")))
            quote_volume = Decimal(str(market.get("volume_quote", 0)))
        except (KeyError, InvalidOperation, ValueError):
            self._last_snapshot = self._empty_snapshot(status="INVALID_MARKET_DATA")
            return self.snapshot()
        if (
            not price.is_finite()
            or price <= 0
            or not spread.is_finite()
            or spread < 0
            or not quote_volume.is_finite()
            or quote_volume < 0
        ):
            self._last_snapshot = self._empty_snapshot(status="INVALID_MARKET_DATA")
            return self.snapshot()

        self._prices.append((now, price))
        self._update_kama(price)
        reference = self._reference.evaluate(markets)
        ready = all(period in self._kama for period in self._periods)
        kama20 = self._kama.get(self.settings.control_period)
        kama_slopes = {
            str(period): _number(self._kama_slope_bps(period), 4) for period in self._periods
        }
        kama20_slope = self._kama_slope_bps(self.settings.control_period)
        distance = abs(price / kama20 - 1) * Decimal(10_000) if kama20 else Decimal(0)
        atr_proxy = self._atr_proxy_bps()
        cost = self._baseline_cost_bps(spread)
        hysteresis = max(
            self.settings.minimum_hysteresis_bps,
            atr_proxy * self.settings.atr_multiplier,
            cost,
        )
        net_edge = distance - cost
        raw_direction = (
            "WAIT"
            if kama20 is None or kama20_slope == 0
            else "LONG"
            if price > kama20 and kama20_slope > 0
            else "SHORT"
            if price < kama20 and kama20_slope < 0
            else "WAIT"
        )
        regime = self._regime(price, atr_proxy)
        if regime == self._last_regime:
            self._regime_observations += 1
        else:
            self._last_regime = regime
            self._regime_observations = 1
        confidence = min(Decimal(1), distance / max(hysteresis * 2, Decimal("0.000001")))
        stale = len(self._prices) > 1 and now - self._prices[-2][0] > 3 * 15
        quarantine = self._quarantine_state(STRATEGY_FILTERED)
        quality = self._gate.decide(
            ready=ready,
            direction=raw_direction,
            regime=regime,
            distance_bps=distance,
            slope_bps=kama20_slope,
            hysteresis_bps=hysteresis,
            net_edge_bps=net_edge,
            confidence=confidence,
            spread_bps=spread,
            quote_volume_usdt=quote_volume,
            reference_status=str(reference["status"]),
            stale=stale,
            quarantined=bool(quarantine["quarantined"]),
            regime_persistent=(
                self._regime_observations >= self.settings.minimum_regime_observations
            ),
            risk_valid=(self.settings.notional_usdt > 0),
        )
        filtered_direction = raw_direction if quality == QualityDecision.ALLOW else "WAIT"
        filtered_action = filtered_direction
        filtered_position = self._positions[STRATEGY_FILTERED]
        if kama20 is not None and filtered_position is not None and distance >= hysteresis:
            invalidated = (
                filtered_position.direction == "LONG" and price < kama20
            ) or (filtered_position.direction == "SHORT" and price > kama20)
            if invalidated:
                filtered_action = "EXIT"
        observation_id = str(uuid4())
        common = {
            "observation_id": observation_id,
            "observed_at_utc": timestamp,
            "symbol": "XAU_USDT",
            "price": _number(price),
            "spread_bps": _number(spread, 4),
            "quote_volume_usdt": _number(quote_volume, 2),
            "kama10": _number(self._kama.get(self.settings.fast_period)),
            "kama20": _number(kama20),
            "kama50": _number(self._kama.get(self.settings.slow_period)),
            "kama_slopes_bps": kama_slopes,
            "distance_from_kama20_bps": _number(distance, 4),
            "atr_proxy_bps": _number(atr_proxy, 4),
            "hysteresis_bps": _number(hysteresis, 4),
            "regime": regime.value,
            "regime_observations": self._regime_observations,
            "raw_direction": raw_direction,
            "confidence": _number(confidence, 4),
            "reference": reference,
            "execution_influence": "NONE",
        }
        raw_decision = self._decision_record(
            common,
            STRATEGY_RAW,
            raw_direction,
            None
            if ready and raw_direction != "WAIT"
            else QualityDecision.WARMING_UP.value
            if not ready
            else QualityDecision.NO_DIRECTION.value,
            distance,
            cost,
        )
        filtered_decision = self._decision_record(
            common,
            STRATEGY_FILTERED,
            filtered_direction,
            None if quality == QualityDecision.ALLOW else quality.value,
            distance,
            cost,
        )
        if self._append("observation", common):
            self._observation_count += 1
        self._resolve_counterfactuals(price, now, timestamp)
        signature = (
            raw_direction,
            filtered_direction,
            quality.value,
            regime.value,
            str(reference["status"]),
        )
        decision_due = (
            self._last_decision_at is None
            or now - self._last_decision_at >= self.settings.decision_interval_seconds
            or signature != self._last_decision_signature
        )
        if decision_due:
            if self._append("decision", raw_decision):
                self._register_decision(raw_decision)
                self._queue_counterfactual(raw_decision, price, now, timestamp)
            if self._append("decision", filtered_decision):
                self._register_decision(filtered_decision)
                self._queue_counterfactual(filtered_decision, price, now, timestamp)
            self._last_decision_at = now
            self._last_decision_signature = signature
        self._advance_shadow(
            STRATEGY_RAW,
            raw_direction if ready else "WAIT",
            regime.value,
            price,
            now,
            timestamp,
            observation_id,
            spread,
        )
        self._advance_shadow(
            STRATEGY_FILTERED,
            filtered_action,
            regime.value,
            price,
            now,
            timestamp,
            observation_id,
            spread,
        )

        raw_metrics = self._metrics(STRATEGY_RAW)
        filtered_metrics = self._metrics(STRATEGY_FILTERED)
        comparison = self._comparison(raw_metrics, filtered_metrics)
        self._last_snapshot = {
            "name": CONTROL_NAME,
            "status": "COLLECTING" if ready else "WARMING_UP",
            "execution_enabled": False,
            "execution_influence": "NONE",
            "schema_version": SCHEMA_VERSION,
            "parameter_id": self.parameter_id,
            "evidence_integrity": self._integrity,
            "evidence_error": self._integrity_error,
            "evidence_path": str(self._path.relative_to(self._path.parents[3])).replace("\\", "/"),
            "observations": self._observation_count,
            "observed_at_utc": timestamp,
            "price": _number(price),
            "kama": {
                "10": _number(self._kama.get(self.settings.fast_period)),
                "20": _number(kama20),
                "50": _number(self._kama.get(self.settings.slow_period)),
            },
            "kama_slopes_bps": kama_slopes,
            "distance_from_kama20_bps": _number(distance, 4),
            "atr_proxy_bps": _number(atr_proxy, 4),
            "hysteresis_bps": _number(hysteresis, 4),
            "regime": regime.value,
            "raw_signal": raw_direction,
            "filtered_signal": filtered_direction,
            "filtered_action": filtered_action,
            "quality_decision": quality.value,
            "confidence": _number(confidence, 4),
            "confidence_semantics": "HEURISTIC_NOT_CALIBRATED",
            "reference": reference,
            "strategies": {
                "CURRENT_PAPER_BASELINE": {
                    "status": "RUN_LEDGER_NOT_SAME_TIMELINE",
                    "trades": 0,
                    "sample_status": "NOT_COMPARABLE",
                },
                **self._external_benchmarks,
                STRATEGY_RAW: raw_metrics,
                STRATEGY_FILTERED: filtered_metrics,
                "XAU_FAIR_VALUE_V1": {"status": "REFERENCE_ONLY", "trades": 0},
                "XAU_COMBINED_V1": {"status": "NOT_IMPLEMENTED", "trades": 0},
                "ENTRY_V3": {"status": "SEPARATE_CAPTURE_TIMELINE", "trades": 0},
            },
            "comparison": comparison,
            "candidates": self._candidate_summary(),
            "quarantine": quarantine,
            "counterfactual_pending": len(self._pending),
            "counterfactual": self._counterfactual_summary(),
        }
        if now - self._last_report_at >= self.settings.report_interval_seconds:
            self.write_reports()
            self._last_report_at = now
        return self.snapshot()

    @property
    def _periods(self) -> tuple[int, int, int]:
        return (self.settings.fast_period, self.settings.control_period, self.settings.slow_period)

    def snapshot(self) -> dict[str, object]:
        return json.loads(json.dumps(self._last_snapshot, allow_nan=False))

    def set_external_benchmark(self, strategy_id: str, metrics: Mapping[str, object]) -> None:
        benchmark = {**metrics, "execution_influence": "NONE"}
        self._external_benchmarks[strategy_id] = benchmark
        strategies = self._last_snapshot.get("strategies")
        if isinstance(strategies, dict):
            strategies[strategy_id] = benchmark

    def write_reports(self) -> None:
        try:
            self._reports.mkdir(parents=True, exist_ok=True)
            snapshot = self.snapshot()
            json_files = {
                "strategy_comparison.json": snapshot,
                "parameter_audit.json": {
                    "control": CONTROL_NAME,
                    "parameter_id": self.parameter_id,
                    "parameters": asdict(self.settings),
                    "selection_method": "FIXED_A_PRIORI",
                    "optimization_status": "NOT_RUN",
                },
                "execution_realism.json": {
                    "profiles": self._execution_profiles(Decimal(0)),
                    "modeled": [
                        "fees",
                        "observed_spread",
                        "fixed_slippage_buffer",
                        "funding_buffer",
                    ],
                    "not_modeled": [
                        "latency",
                        "queue_position",
                        "partial_fills",
                        "market_impact",
                        "adverse_selection",
                    ],
                    "claim_limit": "SHADOW_COUNTERFACTUAL_ONLY",
                },
                "strategy_health.json": {
                    "control": CONTROL_NAME,
                    "evidence_integrity": self._integrity,
                    "quarantine": snapshot.get("quarantine", {}),
                    "comparison": snapshot.get("comparison", {}),
                },
                "reference_impact.json": {
                    "reference": snapshot.get("reference", {}),
                    "filtered_quality_decision": snapshot.get("quality_decision"),
                    "execution_influence": "NONE",
                },
            }
            for name, payload in json_files.items():
                self._atomic_write(
                    self._reports / name,
                    json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                )
            for name, content in self._markdown_reports(snapshot).items():
                self._atomic_write(self._reports / name, content)
        except OSError as exc:
            self._integrity_error = f"report write failed: {exc}"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)

    def _markdown_reports(self, snapshot: Mapping[str, object]) -> dict[str, str]:
        strategies = cast(Mapping[str, Mapping[str, object]], snapshot["strategies"])
        raw = strategies[STRATEGY_RAW]
        filtered = strategies[STRATEGY_FILTERED]
        current = strategies["CURRENT_PAPER_BASELINE"]
        candidate_families = cast(Mapping[str, Mapping[str, object]], snapshot["candidates"])
        raw_candidates = candidate_families[STRATEGY_RAW]
        filtered_candidates = candidate_families[STRATEGY_FILTERED]
        counterfactual = cast(Mapping[str, object], snapshot["counterfactual"])
        reference = cast(Mapping[str, object], snapshot.get("reference", {}))
        comparison = cast(Mapping[str, object], snapshot["comparison"])
        cutoff = str(snapshot.get("observed_at_utc") or _utc_now())

        def shown(value: object, digits: int = 6) -> str:
            if value is None:
                return "N/A"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return f"{value:.{digits}f}" if isinstance(value, float) else str(value)
            return str(value)

        def metric(row: Mapping[str, object], key: str, digits: int = 6) -> str:
            return shown(row.get(key), digits)

        def rejection_text() -> str:
            reasons = cast(Mapping[str, object], filtered_candidates.get("rejection_reasons", {}))
            return ", ".join(f"{key}={value}" for key, value in reasons.items()) or "none"

        def regime_extreme(highest: bool) -> str:
            regimes = cast(Mapping[str, Mapping[str, object]], raw.get("by_regime", {}))
            eligible = [
                (name, float(str(row["expectancy_usdt"])))
                for name, row in regimes.items()
                if row.get("expectancy_usdt") is not None
            ]
            if not eligible:
                return "N/A"
            name, expectancy = (max if highest else min)(eligible, key=lambda item: item[1])
            return f"{name} ({expectancy:.6f} USDT/trade; descriptive only)"

        report_header = (
            f"Generated from append-only PAPER shadow evidence at `{cutoff}`.  \n"
            f"Control: `{CONTROL_NAME}` · parameter set `{self.parameter_id}` · "
            "execution influence `NONE`.\n\n"
        )
        raw_profiles = cast(Mapping[str, Mapping[str, object]], raw.get("execution_profiles", {}))
        filtered_profiles = cast(
            Mapping[str, Mapping[str, object]], filtered.get("execution_profiles", {})
        )
        profile_rows = []
        execution_profiles = self._execution_profiles(Decimal(0))
        for profile in ("IDEALIZED", "BASELINE", "STRESSED"):
            assumptions = execution_profiles[profile]
            profile_rows.append(
                "| {profile} | {cost} | {raw_net} | {filtered_net} |".format(
                    profile=profile,
                    cost=shown(assumptions["round_trip_cost_bps"], 4),
                    raw_net=metric(raw_profiles.get(profile, {}), "net_pnl_usdt"),
                    filtered_net=metric(filtered_profiles.get(profile, {}), "net_pnl_usdt"),
                )
            )

        def report(title: str, lines: list[str]) -> str:
            return f"# {title}\n\n{report_header}" + "\n".join(lines) + "\n"

        health_rows = []
        for label, row in (
            ("Current PAPER baseline", current),
            ("KAMA20 raw", raw),
            ("KAMA20 filtered", filtered),
        ):
            health_rows.append(
                "| {label} | {status} | {sample} | {pf} | {expectancy} | {net} | {dd} |".format(
                    label=label,
                    status=metric(row, "status"),
                    sample=metric(row, "trades", 0),
                    pf=metric(row, "profit_factor", 4),
                    expectancy=metric(row, "expectancy_usdt"),
                    net=metric(row, "net_pnl_usdt"),
                    dd=metric(row, "max_drawdown_usdt"),
                )
            )
        health = report(
            "Strategy Health Latest",
            [
                "| Family | Status | Sample | PF | Expectancy | Net | Max DD |",
                "|---|---:|---:|---:|---:|---:|---:|",
                *health_rows,
                "",
                "Current PAPER baseline is run-ledger evidence, not the same XAU timeline. "
                "No row is promotion evidence.",
            ],
        )
        ama = report(
            "AMA20 Control Latest",
            [
                f"1. Raw candidates: **{metric(raw_candidates, 'candidates', 0)}**.",
                f"2. Passed filters: **{metric(filtered_candidates, 'allowed', 0)}**.",
                f"3. Rejected candidates: **{metric(filtered_candidates, 'rejected', 0)}**.",
                f"4. Rejection reasons: {rejection_text()}.",
                "5. Raw expectancy: "
                f"{metric(raw, 'expectancy_usdt')} USDT/trade ({metric(raw, 'sample_status')}).",
                "6. Filtered expectancy: "
                f"{metric(filtered, 'expectancy_usdt')} USDT/trade "
                f"({metric(filtered, 'sample_status')}).",
                "7. Modeled baseline costs: "
                f"raw {metric(raw, 'costs_usdt')} USDT; "
                f"filtered {metric(filtered, 'costs_usdt')} USDT.",
                f"8. Best observed raw regime: {regime_extreme(True)}.",
                f"9. Worst observed raw regime: {regime_extreme(False)}.",
                "10. Is hysteresis reducing whipsaw? **INSUFFICIENT EVIDENCE**; "
                "the filtered sample is below the configured minimum.",
                "11. Is filtering improving expectancy? **INSUFFICIENT EVIDENCE**.",
                "12. Is filtering missing too much opportunity? **INSUFFICIENT EVIDENCE**; "
                f"counterfactual outcomes={metric(counterfactual, 'outcomes', 0)}.",
                "13. Is the current main strategy outperforming AMA? **NOT COMPARABLE**; "
                "it is not a same-timeline XAU benchmark.",
                "14. Is improvement statistically meaningful? **NO CLAIM**; "
                "no valid significance test exists at this sample.",
                "15. Does the complex strategy have better drawdown behavior? "
                "**NOT COMPARABLE**.",
                "16. Is the difference robust under stressed execution? "
                "**NOT TESTED ON EQUIVALENT TIMELINES**.",
                "17. Does the difference survive holdout? **NOT RUN**.",
            ],
        )
        complexity = report(
            "Complexity Audit Latest",
            [
                "## Is the complex AUTOTRADE strategy demonstrably adding value over AMA20?",
                "",
                "**INSUFFICIENT EVIDENCE**",
                "",
                f"Raw closes={metric(raw, 'trades', 0)}, "
                f"filtered closes={metric(filtered, 'trades', 0)}, "
                "minimum per family="
                f"{metric(comparison, 'minimum_trades_per_family', 0)}. "
                "The current strategy is not on the same XAU timeline, and holdout/walk-forward "
                "validation has not run. Complexity remains unearned.",
            ],
        )
        kama_values = cast(Mapping[str, object], snapshot.get("kama", {}))
        xau = report(
            "XAU Research Latest",
            [
                f"- Observations: {shown(snapshot.get('observations'), 0)}",
                "- Price / KAMA20: "
                f"{shown(snapshot.get('price'))} / {shown(kama_values.get('20'))}",
                f"- Regime: {shown(snapshot.get('regime'))}",
                "- Raw / filtered: "
                f"{shown(snapshot.get('raw_signal'))} / {shown(snapshot.get('filtered_signal'))}",
                f"- Reference: {shown(reference.get('status'))}; "
                f"fair value={shown(reference.get('fair_value'))}; "
                f"dispersion={shown(reference.get('dispersion_bps'), 4)} bps; "
                f"dislocation={shown(reference.get('dislocation_bps'), 4)} bps",
                "- Reference and control have no execution influence.",
            ],
        )
        entry = report(
            "Entry Quality Latest",
            [
                f"- Raw candidate records: {metric(raw_candidates, 'candidates', 0)}",
                f"- Filtered allowed: {metric(filtered_candidates, 'allowed', 0)}",
                f"- Filtered rejected: {metric(filtered_candidates, 'rejected', 0)}",
                f"- Current decision: {shown(snapshot.get('quality_decision'))}",
                f"- Rejection taxonomy: {rejection_text()}",
                "- Confidence is heuristic, not calibrated. No rejection or acceptance routes "
                "to orders.",
            ],
        )
        counter = report(
            "Counterfactual Latest",
            [
                f"- Resolved horizons: {metric(counterfactual, 'outcomes', 0)}",
                f"- Pending decisions: {shown(snapshot.get('counterfactual_pending'), 0)}",
                "- Mean modeled net return: "
                f"{metric(counterfactual, 'mean_modeled_net_return_bps', 4)} bps",
                f"- Classifications: {shown(counterfactual.get('classifications'))}",
                f"- Horizons: {shown(counterfactual.get('horizons'))}",
                "Each outcome uses the stored decision-time cost and only later observations for "
                "the stated horizon. These are modeled counterfactuals, not fills.",
            ],
        )
        execution = report(
            "Execution Realism Latest",
            [
                "| Profile | Assumed round-trip cost bps at zero spread | Raw net USDT | "
                "Filtered net USDT |",
                "|---|---:|---:|---:|",
                *profile_rows,
                "",
                "Modeled: fees, observed spread at close, fixed slippage buffer, funding buffer.",
                "",
                "Not modeled: latency, queue position, partial fills, missed fills, market impact, "
                "adverse selection. Results are shadow counterfactuals only.",
            ],
        )
        walk_forward = report(
            "Walk-Forward Latest",
            [
                "**Status: NOT RUN — INSUFFICIENT EVIDENCE**",
                "",
                "No chronological TRAIN → VALIDATION → TEST → FINAL HOLDOUT result exists for "
                "this V2 control. Random splitting is prohibited. Do not tune parameters or "
                "promote a strategy from the current partial run.",
            ],
        )
        return {
            "strategy_health_latest.md": health,
            "ama20_control_latest.md": ama,
            "complexity_audit_latest.md": complexity,
            "xau_research_latest.md": xau,
            "entry_quality_latest.md": entry,
            "counterfactual_latest.md": counter,
            "execution_realism_latest.md": execution,
            "walk_forward_latest.md": walk_forward,
        }

    def _update_kama(self, price: Decimal) -> None:
        values = [item[1] for item in self._prices]
        self._previous_kama = dict(self._kama)
        fast_sc = Decimal(2) / Decimal(self.settings.kama_fast + 1)
        slow_sc = Decimal(2) / Decimal(self.settings.kama_slow + 1)
        for period in self._periods:
            if len(values) <= period:
                continue
            change = abs(values[-1] - values[-period - 1])
            volatility = sum(
                (
                    abs(current - previous)
                    for previous, current in zip(
                        values[-period - 1 : -1], values[-period:], strict=True
                    )
                ),
                Decimal(0),
            )
            efficiency = change / volatility if volatility > 0 else Decimal(0)
            smoothing = (efficiency * (fast_sc - slow_sc) + slow_sc) ** 2
            previous_kama = self._kama.get(period, values[-period - 1])
            self._kama[period] = previous_kama + smoothing * (price - previous_kama)

    def _kama_slope_bps(self, period: int) -> Decimal:
        current = self._kama.get(period)
        previous = self._previous_kama.get(period)
        if current is None or previous is None or previous <= 0:
            return Decimal(0)
        return (current / previous - 1) * Decimal(10_000)

    def _atr_proxy_bps(self) -> Decimal:
        values = [item[1] for item in self._prices]
        if len(values) < 2:
            return Decimal(0)
        window = values[-self.settings.atr_proxy_window - 1 :]
        moves = [
            abs(current / previous - 1) * Decimal(10_000)
            for previous, current in zip(window, window[1:], strict=False)
            if previous > 0
        ]
        return sum(moves, Decimal(0)) / Decimal(len(moves)) if moves else Decimal(0)

    def _regime(self, price: Decimal, atr_proxy: Decimal) -> AmaRegime:
        if not all(period in self._kama for period in self._periods):
            return AmaRegime.UNKNOWN
        fast = self._kama[self.settings.fast_period]
        control = self._kama[self.settings.control_period]
        slow = self._kama[self.settings.slow_period]
        previous = self._previous_kama.get(self.settings.control_period, control)
        slope = (control / previous - 1) * Decimal(10_000) if previous > 0 else Decimal(0)
        separation = abs(fast / slow - 1) * Decimal(10_000)
        strong = max(self.settings.minimum_hysteresis_bps, atr_proxy * self.settings.atr_multiplier)
        if price > fast > control > slow and slope > 0:
            return AmaRegime.STRONG_UPTREND if separation >= strong else AmaRegime.UPTREND
        if price < fast < control < slow and slope < 0:
            return AmaRegime.STRONG_DOWNTREND if separation >= strong else AmaRegime.DOWNTREND
        if separation <= self.settings.minimum_hysteresis_bps and abs(slope) <= atr_proxy:
            return AmaRegime.CHOP
        return AmaRegime.TRANSITION

    def _baseline_cost_bps(self, spread: Decimal) -> Decimal:
        return (
            self.settings.round_trip_fee_bps
            + spread
            + self.settings.slippage_bps_per_side * 2
            + self.settings.funding_buffer_bps
        )

    def _execution_profiles(self, spread: Decimal) -> dict[str, dict[str, object]]:
        idealized = self.settings.round_trip_fee_bps
        baseline = self._baseline_cost_bps(spread)
        stressed = baseline * self.settings.stressed_cost_multiplier
        return {
            "IDEALIZED": {"round_trip_cost_bps": _number(idealized, 4)},
            "BASELINE": {"round_trip_cost_bps": _number(baseline, 4)},
            "STRESSED": {"round_trip_cost_bps": _number(stressed, 4)},
        }

    def _decision_record(
        self,
        common: Mapping[str, object],
        strategy_id: str,
        direction: str,
        rejection_reason: str | None,
        gross_edge_bps: Decimal,
        cost_bps: Decimal,
    ) -> dict[str, object]:
        return {
            "decision_id": str(uuid4()),
            "observation_id": common["observation_id"],
            "run_id": self.run_id,
            "session_id": self.session_id,
            "strategy_id": strategy_id,
            "strategy_version": "2",
            "feature_schema_version": SCHEMA_VERSION,
            "execution_model_version": "AMA_SHADOW_COST_V1",
            "parameter_id": self.parameter_id,
            "observed_at_utc": common["observed_at_utc"],
            "symbol": "XAU_USDT",
            "market_scope": "CONTINUOUS_RESEARCH",
            "market": {
                "price": common["price"],
                "spread_bps": common["spread_bps"],
                "quote_volume_usdt": common["quote_volume_usdt"],
                "kama10": common["kama10"],
                "kama20": common["kama20"],
                "kama50": common["kama50"],
                "kama_slopes_bps": common["kama_slopes_bps"],
                "distance_from_kama20_bps": common["distance_from_kama20_bps"],
                "atr_proxy_bps": common["atr_proxy_bps"],
                "hysteresis_bps": common["hysteresis_bps"],
                "regime_observations": common["regime_observations"],
            },
            "direction": direction,
            "candidate_direction": common["raw_direction"],
            "regime": common["regime"],
            "gross_edge_bps": _number(gross_edge_bps, 4),
            "cost_bps": _number(cost_bps, 4),
            "net_edge_bps": _number(gross_edge_bps - cost_bps, 4),
            "threshold_bps": _number(self.settings.minimum_net_edge_bps, 4),
            "confidence": common["confidence"],
            "reference": common["reference"],
            "decision": "REJECT" if rejection_reason else "ALLOW",
            "rejection_reason": rejection_reason,
            "rejection_severity": "BLOCK" if rejection_reason else "NONE",
            "risk": {"notional_usdt": _number(self.settings.notional_usdt), "leverage": 1},
            "execution": {
                "enabled": False,
                "influence": "NONE",
                "simulated_fill": None,
                "latency": "NOT_MODELED",
                "fill_probability": "NOT_MODELED",
                "partial_fill_status": "NOT_MODELED",
                "profiles": self._execution_profiles(Decimal(str(common["spread_bps"]))),
            },
            "outcome": None,
        }

    def _queue_counterfactual(
        self,
        decision: Mapping[str, object],
        price: Decimal,
        now: float,
        timestamp: str,
    ) -> None:
        direction = str(decision["direction"])
        if direction == "WAIT" and str(decision["strategy_id"]) == STRATEGY_RAW:
            return
        raw_direction = str(decision.get("candidate_direction", direction))
        if raw_direction not in {"LONG", "SHORT"}:
            return
        self._pending.append(
            _PendingCounterfactual(
                decision_id=str(decision["decision_id"]),
                strategy_id=str(decision["strategy_id"]),
                direction=raw_direction,
                entry_price=price,
                observed_at_monotonic=now,
                observed_at_utc=timestamp,
                rejection_reason=(
                    str(decision["rejection_reason"]) if decision.get("rejection_reason") else None
                ),
                cost_bps=Decimal(str(decision["cost_bps"])),
            )
        )

    def _resolve_counterfactuals(self, price: Decimal, now: float, timestamp: str) -> None:
        keep: list[_PendingCounterfactual] = []
        for pending in self._pending:
            age = now - pending.observed_at_monotonic
            signed_return = (price / pending.entry_price - 1) * Decimal(10_000)
            if pending.direction == "SHORT":
                signed_return = -signed_return
            pending.maximum_favorable_bps = max(pending.maximum_favorable_bps, signed_return)
            pending.maximum_adverse_bps = min(pending.maximum_adverse_bps, signed_return)
            for horizon in HORIZONS_SECONDS:
                key = (pending.decision_id, horizon)
                if key in self._resolved or age < horizon:
                    continue
                net = signed_return - pending.cost_bps
                outcome = {
                    "decision_id": pending.decision_id,
                    "strategy_id": pending.strategy_id,
                    "candidate_observed_at_utc": pending.observed_at_utc,
                    "outcome_observed_at_utc": timestamp,
                    "horizon_seconds": horizon,
                    "direction": pending.direction,
                    "entry_price": _number(pending.entry_price),
                    "outcome_price": _number(price),
                    "gross_return_bps": _number(signed_return, 4),
                    "modeled_cost_bps": _number(pending.cost_bps, 4),
                    "modeled_net_return_bps": _number(net, 4),
                    "mfe_bps": _number(pending.maximum_favorable_bps, 4),
                    "mae_bps": _number(pending.maximum_adverse_bps, 4),
                    "rejection_reason": pending.rejection_reason,
                    "classification": "MISSED_OPPORTUNITY"
                    if pending.rejection_reason and net > 0
                    else "AVOIDED_LOSS"
                    if pending.rejection_reason and net < 0
                    else "OBSERVED",
                    "lookahead": "NONE",
                }
                if self._append(
                    "counterfactual_outcome",
                    outcome,
                ):
                    self._register_counterfactual(outcome)
                    self._resolved.add(key)
            if age < max(HORIZONS_SECONDS):
                keep.append(pending)
        self._pending = keep

    def _advance_shadow(
        self,
        strategy_id: str,
        direction: str,
        regime: str,
        price: Decimal,
        now: float,
        timestamp: str,
        observation_id: str,
        spread: Decimal,
    ) -> None:
        position = self._positions[strategy_id]
        if position is not None:
            signed = (price / position.entry_price - 1) * Decimal(10_000)
            if position.direction == "SHORT":
                signed = -signed
            position.maximum_favorable_bps = max(position.maximum_favorable_bps, signed)
            position.maximum_adverse_bps = min(position.maximum_adverse_bps, signed)
            expired = now - position.entry_time >= self.settings.maximum_holding_seconds
            opposite = direction in {"LONG", "SHORT"} and direction != position.direction
            invalidated = direction == "EXIT"
            if expired or opposite or invalidated:
                closed = self._close_shadow(
                    strategy_id,
                    position,
                    price,
                    timestamp,
                    spread,
                    "MAX_HOLD"
                    if expired
                    else "HYSTERESIS_INVALIDATION"
                    if invalidated
                    else "SIGNAL_REVERSAL",
                )
                if closed:
                    self._positions[strategy_id] = None
                    position = None
        if position is None and direction in {"LONG", "SHORT"}:
            opened = _ShadowPosition(direction, regime, price, now, timestamp, observation_id)
            if self._append(
                "shadow_open",
                {
                    "strategy_id": strategy_id,
                    "observation_id": observation_id,
                    "direction": direction,
                    "regime": regime,
                    "entry_price": _number(price),
                    "entry_at_utc": timestamp,
                    "notional_usdt": _number(self.settings.notional_usdt),
                    "execution_profile": "BASELINE",
                },
            ):
                self._positions[strategy_id] = opened

    def _close_shadow(
        self,
        strategy_id: str,
        position: _ShadowPosition,
        price: Decimal,
        timestamp: str,
        spread: Decimal,
        reason: str,
    ) -> bool:
        gross_bps = (price / position.entry_price - 1) * Decimal(10_000)
        if position.direction == "SHORT":
            gross_bps = -gross_bps
        profiles = self._execution_profiles(spread)
        profile_net: dict[str, float] = {}
        for name, profile in profiles.items():
            cost_bps = Decimal(str(profile["round_trip_cost_bps"]))
            profile_net[name] = float(
                self.settings.notional_usdt * (gross_bps - cost_bps) / Decimal(10_000)
            )
        trade: dict[str, object] = {
            "strategy_id": strategy_id,
            "direction": position.direction,
            "regime": position.regime,
            "entry_at_utc": position.entry_utc,
            "closed_at_utc": timestamp,
            "entry_price": _number(position.entry_price),
            "close_price": _number(price),
            "notional_usdt": _number(self.settings.notional_usdt),
            "gross_return_bps": _number(gross_bps, 4),
            "net_pnl_by_profile_usdt": {key: round(value, 8) for key, value in profile_net.items()},
            "mae_bps": _number(position.maximum_adverse_bps, 4),
            "mfe_bps": _number(position.maximum_favorable_bps, 4),
            "reason": reason,
        }
        if not self._append("shadow_close", trade):
            return False
        self._trades[strategy_id].append(trade)
        return True

    def _metrics(self, strategy_id: str) -> dict[str, object]:
        trades = self._trades[strategy_id]
        position = self._positions[strategy_id]
        gross = [
            Decimal(str(trade["gross_return_bps"])) * self.settings.notional_usdt / Decimal(10_000)
            for trade in trades
        ]
        profile_metrics: dict[str, dict[str, object]] = {}
        profile_nets: dict[str, list[Decimal]] = {}
        for profile_name in ("IDEALIZED", "BASELINE", "STRESSED"):
            values = [
                Decimal(
                    str(
                        cast(Mapping[str, object], trade["net_pnl_by_profile_usdt"])[
                            profile_name
                        ]
                    )
                )
                for trade in trades
            ]
            profile_nets[profile_name] = values
            profile_wins = [value for value in values if value > 0]
            profile_losses = [value for value in values if value < 0]
            equity = Decimal(0)
            peak = Decimal(0)
            drawdown = Decimal(0)
            for value in values:
                equity += value
                peak = max(peak, equity)
                drawdown = max(drawdown, peak - equity)
            profile_metrics[profile_name] = {
                "net_pnl_usdt": _number(sum(values, Decimal(0))),
                "expectancy_usdt": _number(sum(values, Decimal(0)) / Decimal(len(values)))
                if values
                else None,
                "profit_factor": _number(
                    sum(profile_wins, Decimal(0)) / abs(sum(profile_losses, Decimal(0))), 4
                )
                if profile_losses
                else None,
                "max_drawdown_usdt": _number(drawdown),
            }
        nets = profile_nets["BASELINE"]
        wins = [value for value in nets if value > 0]
        losses = [value for value in nets if value < 0]
        equity = Decimal(0)
        peak = Decimal(0)
        max_drawdown = Decimal(0)
        for value in nets:
            equity += value
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        costs = sum(gross, Decimal(0)) - sum(nets, Decimal(0))

        def breakdown(key: str) -> dict[str, dict[str, object]]:
            values: dict[str, list[Decimal]] = defaultdict(list)
            for trade, net in zip(trades, nets, strict=True):
                values[str(trade.get(key, "UNKNOWN"))].append(net)
            return {
                name: {
                    "trades": len(items),
                    "net_pnl_usdt": _number(sum(items, Decimal(0))),
                    "expectancy_usdt": _number(sum(items, Decimal(0)) / Decimal(len(items))),
                }
                for name, items in sorted(values.items())
            }

        return {
            "status": "QUARANTINED"
            if self._quarantine_state(strategy_id)["quarantined"]
            else "SHADOW",
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades), 4) if trades else None,
            "gross_pnl_usdt": _number(sum(gross, Decimal(0))),
            "costs_usdt": _number(costs),
            "net_pnl_usdt": _number(sum(nets, Decimal(0))),
            "expectancy_usdt": _number(sum(nets, Decimal(0)) / Decimal(len(nets)))
            if nets
            else None,
            "profit_factor": _number(sum(wins, Decimal(0)) / abs(sum(losses, Decimal(0))), 4)
            if losses
            else None,
            "max_drawdown_usdt": _number(max_drawdown),
            "average_mae_bps": round(
                sum(float(str(trade["mae_bps"])) for trade in trades) / len(trades), 4
            )
            if trades
            else None,
            "average_mfe_bps": round(
                sum(float(str(trade["mfe_bps"])) for trade in trades) / len(trades), 4
            )
            if trades
            else None,
            "open_position": (position.direction if position is not None else None),
            "sample_status": "MEANINGFUL"
            if len(trades) >= self.settings.minimum_comparison_trades
            else "INSUFFICIENT_EVIDENCE",
            "execution_profiles": profile_metrics,
            "by_direction": breakdown("direction"),
            "by_regime": breakdown("regime"),
        }

    def _comparison(
        self, raw: Mapping[str, object], filtered: Mapping[str, object]
    ) -> dict[str, object]:
        raw_count = int(str(raw["trades"]))
        filtered_count = int(str(filtered["trades"]))
        if min(raw_count, filtered_count) < self.settings.minimum_comparison_trades:
            return {
                "status": "INSUFFICIENT_EVIDENCE",
                "minimum_trades_per_family": self.settings.minimum_comparison_trades,
                "raw_trades": raw_count,
                "filtered_trades": filtered_count,
                "complexity_alpha": "UNPROVEN",
                "promotion_eligible": False,
            }
        raw_net = Decimal(str(raw["net_pnl_usdt"]))
        filtered_net = Decimal(str(filtered["net_pnl_usdt"]))
        raw_dd = Decimal(str(raw["max_drawdown_usdt"]))
        filtered_dd = Decimal(str(filtered["max_drawdown_usdt"]))
        alpha = filtered_net - raw_net
        return {
            "status": "EVALUATED",
            "raw_trades": raw_count,
            "filtered_trades": filtered_count,
            "incremental_net_pnl_usdt": _number(alpha),
            "drawdown_change_usdt": _number(filtered_dd - raw_dd),
            "complexity_alpha": "SUPPORTED"
            if alpha > 0 and filtered_dd <= raw_dd
            else "NOT_SUPPORTED",
            "promotion_eligible": False,
            "manual_approval_required": True,
        }

    def _quarantine_state(self, strategy_id: str) -> dict[str, object]:
        trades = self._trades[strategy_id]
        if len(trades) < self.settings.quarantine_minimum_trades:
            return {"quarantined": False, "reason": None, "sample_size": len(trades)}
        nets = [
            Decimal(str(cast(Mapping[str, object], trade["net_pnl_by_profile_usdt"])["BASELINE"]))
            for trade in trades
        ]
        wins = sum((value for value in nets if value > 0), Decimal(0))
        losses = abs(sum((value for value in nets if value < 0), Decimal(0)))
        profit_factor = wins / losses if losses else Decimal("999")
        recent = sum(nets[-10:], Decimal(0))
        quarantined = profit_factor < self.settings.quarantine_profit_factor and recent < 0
        return {
            "quarantined": quarantined,
            "reason": "LOW_PROFIT_FACTOR_AND_NEGATIVE_RECENT_NET" if quarantined else None,
            "sample_size": len(trades),
            "profit_factor": _number(profit_factor, 4),
            "recent_10_net_usdt": _number(recent),
            "scope": "SHADOW_ONLY",
        }

    def _register_decision(self, decision: Mapping[str, object]) -> None:
        strategy_id = str(decision["strategy_id"])
        stats = self._decision_stats[strategy_id]
        stats["records"] += 1
        if str(decision.get("candidate_direction")) not in {"LONG", "SHORT"}:
            return
        stats["candidates"] += 1
        if decision.get("decision") == "ALLOW":
            stats["allowed"] += 1
            return
        stats["rejected"] += 1
        reason = str(decision.get("rejection_reason") or "UNSPECIFIED")
        self._rejection_counts[strategy_id][reason] += 1

    def _candidate_summary(self) -> dict[str, object]:
        return {
            strategy_id: {
                **self._decision_stats[strategy_id],
                "rejection_reasons": dict(sorted(self._rejection_counts[strategy_id].items())),
            }
            for strategy_id in (STRATEGY_RAW, STRATEGY_FILTERED)
        }

    def _register_counterfactual(self, outcome: Mapping[str, object]) -> None:
        self._counterfactual_total += 1
        self._counterfactual_net_bps += Decimal(str(outcome["modeled_net_return_bps"]))
        self._counterfactual_classifications[str(outcome["classification"])] += 1
        self._counterfactual_horizons[int(str(outcome["horizon_seconds"]))] += 1

    def _counterfactual_summary(self) -> dict[str, object]:
        return {
            "outcomes": self._counterfactual_total,
            "mean_modeled_net_return_bps": _number(
                self._counterfactual_net_bps / Decimal(self._counterfactual_total), 4
            )
            if self._counterfactual_total
            else None,
            "classifications": dict(sorted(self._counterfactual_classifications.items())),
            "horizons": {
                str(key): value for key, value in sorted(self._counterfactual_horizons.items())
            },
            "claim_limit": "NO_LOOKAHEAD_MODELED_COUNTERFACTUAL",
        }

    def _append(self, event_type: str, payload: Mapping[str, object]) -> bool:
        if self._integrity != "VALID":
            return False
        self._sequence += 1
        record: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "sequence": self._sequence,
            "event_type": event_type,
            "event_id": str(uuid4()),
            "recorded_at_utc": _utc_now(),
            "run_id": self.run_id,
            "session_id": self.session_id,
            "previous_hash": self._previous_hash,
            "payload": dict(payload),
        }
        record_hash = hashlib.sha256(self._previous_hash.encode() + _canonical(record)).hexdigest()
        record["record_hash"] = record_hash
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            self._sequence -= 1
            self._integrity = "WRITE_FAILED"
            self._integrity_error = str(exc)[:200]
            return False
        self._previous_hash = record_hash
        return True

    def _load_evidence(self) -> None:
        if not self._path.exists():
            return
        previous_hash = ZERO_HASH
        loaded_prices: deque[tuple[datetime, Decimal]] = deque(maxlen=self._prices.maxlen)
        recent_decisions: deque[dict[str, object]] = deque(maxlen=2_000)
        recent_outcomes: deque[dict[str, object]] = deque(maxlen=5_000)
        open_payloads: dict[str, dict[str, object]] = {}
        try:
            with self._path.open(encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError(f"line {line_number} is not an object")
                    stored_hash = str(record.pop("record_hash", ""))
                    if record.get("previous_hash") != previous_hash:
                        raise ValueError(f"line {line_number} previous hash mismatch")
                    expected = hashlib.sha256(
                        previous_hash.encode() + _canonical(record)
                    ).hexdigest()
                    if stored_hash != expected:
                        raise ValueError(f"line {line_number} record hash mismatch")
                    previous_hash = stored_hash
                    self._sequence = int(record["sequence"])
                    event_type = record.get("event_type")
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    if event_type == "observation" and payload.get("price") is not None:
                        self._observation_count += 1
                        loaded_prices.append(
                            (
                                _parse_utc(payload["observed_at_utc"]),
                                Decimal(str(payload["price"])),
                            )
                        )
                    elif event_type == "decision":
                        self._register_decision(payload)
                        recent_decisions.append(payload)
                    elif event_type == "shadow_open":
                        open_payloads[str(payload["strategy_id"])] = payload
                    elif event_type == "shadow_close":
                        self._trades[str(payload["strategy_id"])].append(payload)
                        open_payloads.pop(str(payload["strategy_id"]), None)
                    elif event_type == "counterfactual_outcome":
                        self._register_counterfactual(payload)
                        recent_outcomes.append(payload)
            self._previous_hash = previous_hash
            self._restore_runtime_state(
                loaded_prices,
                recent_decisions,
                recent_outcomes,
                open_payloads,
            )
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
            InvalidOperation,
        ) as exc:
            self._integrity = "INVALID"
            self._integrity_error = str(exc)[:200]

    def _restore_runtime_state(
        self,
        loaded_prices: deque[tuple[datetime, Decimal]],
        recent_decisions: deque[dict[str, object]],
        recent_outcomes: deque[dict[str, object]],
        open_payloads: Mapping[str, dict[str, object]],
    ) -> None:
        wall_now = datetime.now(UTC)
        monotonic_now = monotonic()
        for observed_at, price in loaded_prices:
            elapsed = max(0.0, (wall_now - observed_at).total_seconds())
            self._prices.append((monotonic_now - elapsed, price))
            self._update_kama(price)

        for strategy_id, payload in open_payloads.items():
            if strategy_id not in self._positions:
                continue
            entry_at = _parse_utc(payload["entry_at_utc"])
            elapsed = max(0.0, (wall_now - entry_at).total_seconds())
            position = _ShadowPosition(
                direction=str(payload["direction"]),
                regime=str(payload.get("regime", AmaRegime.UNKNOWN.value)),
                entry_price=Decimal(str(payload["entry_price"])),
                entry_time=monotonic_now - elapsed,
                entry_utc=str(payload["entry_at_utc"]),
                observation_id=str(payload["observation_id"]),
            )
            for observed_at, price in loaded_prices:
                if observed_at < entry_at:
                    continue
                signed = (price / position.entry_price - 1) * Decimal(10_000)
                if position.direction == "SHORT":
                    signed = -signed
                position.maximum_favorable_bps = max(position.maximum_favorable_bps, signed)
                position.maximum_adverse_bps = min(position.maximum_adverse_bps, signed)
            self._positions[strategy_id] = position

        for outcome in recent_outcomes:
            self._resolved.add((str(outcome["decision_id"]), int(str(outcome["horizon_seconds"]))))
        for decision in recent_decisions:
            observed_at = _parse_utc(decision["observed_at_utc"])
            elapsed = max(0.0, (wall_now - observed_at).total_seconds())
            if elapsed >= max(HORIZONS_SECONDS):
                continue
            market = cast(Mapping[str, object], decision["market"])
            direction = str(decision.get("candidate_direction", decision["direction"]))
            if direction not in {"LONG", "SHORT"}:
                continue
            decision_id = str(decision["decision_id"])
            if all((decision_id, horizon) in self._resolved for horizon in HORIZONS_SECONDS):
                continue
            pending = _PendingCounterfactual(
                decision_id=decision_id,
                strategy_id=str(decision["strategy_id"]),
                direction=direction,
                entry_price=Decimal(str(market["price"])),
                observed_at_monotonic=monotonic_now - elapsed,
                observed_at_utc=str(decision["observed_at_utc"]),
                rejection_reason=(
                    str(decision["rejection_reason"]) if decision.get("rejection_reason") else None
                ),
                cost_bps=Decimal(str(decision["cost_bps"])),
            )
            for price_time, price in loaded_prices:
                if price_time < observed_at:
                    continue
                signed = (price / pending.entry_price - 1) * Decimal(10_000)
                if pending.direction == "SHORT":
                    signed = -signed
                pending.maximum_favorable_bps = max(pending.maximum_favorable_bps, signed)
                pending.maximum_adverse_bps = min(pending.maximum_adverse_bps, signed)
            self._pending.append(pending)

    def _empty_snapshot(self, *, status: str = "WAITING_MARKET") -> dict[str, object]:
        empty_metrics = {
            "status": "SHADOW",
            "trades": 0,
            "net_pnl_usdt": 0.0,
            "profit_factor": None,
            "max_drawdown_usdt": 0.0,
            "sample_status": "INSUFFICIENT_EVIDENCE",
        }
        return {
            "name": CONTROL_NAME,
            "status": status,
            "execution_enabled": False,
            "execution_influence": "NONE",
            "schema_version": SCHEMA_VERSION,
            "parameter_id": self.parameter_id,
            "evidence_integrity": self._integrity,
            "evidence_error": self._integrity_error,
            "observations": self._observation_count,
            "kama": {"10": None, "20": None, "50": None},
            "kama_slopes_bps": {"10": None, "20": None, "50": None},
            "distance_from_kama20_bps": None,
            "regime": AmaRegime.UNKNOWN.value,
            "regime_observations": 0,
            "raw_signal": "WAIT",
            "filtered_signal": "WAIT",
            "filtered_action": "WAIT",
            "quality_decision": QualityDecision.WARMING_UP.value,
            "confidence": None,
            "confidence_semantics": "HEURISTIC_NOT_CALIBRATED",
            "reference": {"status": "UNAVAILABLE"},
            "strategies": {
                "CURRENT_PAPER_BASELINE": {
                    "status": "RUN_LEDGER_NOT_SAME_TIMELINE",
                    "trades": 0,
                    "sample_status": "NOT_COMPARABLE",
                },
                **self._external_benchmarks,
                STRATEGY_RAW: dict(empty_metrics),
                STRATEGY_FILTERED: dict(empty_metrics),
                "XAU_FAIR_VALUE_V1": {"status": "REFERENCE_ONLY", "trades": 0},
                "XAU_COMBINED_V1": {"status": "NOT_IMPLEMENTED", "trades": 0},
                "ENTRY_V3": {"status": "SEPARATE_CAPTURE_TIMELINE", "trades": 0},
            },
            "comparison": {
                "status": "INSUFFICIENT_EVIDENCE",
                "complexity_alpha": "UNPROVEN",
                "promotion_eligible": False,
            },
            "candidates": self._candidate_summary(),
            "quarantine": {"quarantined": False, "reason": None, "sample_size": 0},
            "counterfactual_pending": 0,
            "counterfactual": self._counterfactual_summary(),
        }
