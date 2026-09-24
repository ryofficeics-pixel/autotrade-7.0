from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import cast

from autotrade.research import ExecutionProfile
from autotrade.wide_crypto_data import Candle


class StrategyFamily(StrEnum):
    CS_MOMENTUM_ONLY_V1 = "CS_MOMENTUM_ONLY_V1"
    CTA_BREAKOUT_ONLY_V1 = "CTA_BREAKOUT_ONLY_V1"
    CROSS_SECTIONAL_BREAKOUT_V1 = "CROSS_SECTIONAL_BREAKOUT_V1"


@dataclass(frozen=True)
class WideStrategySettings:
    momentum_hours: tuple[int, int, int]
    momentum_weights: tuple[Decimal, Decimal, Decimal]
    tail_fraction: Decimal
    donchian_hours: int
    atr_hours: int
    volatility_baseline_hours: int
    volume_baseline_hours: int
    minimum_volume_ratio: Decimal
    minimum_volatility_ratio: Decimal
    maximum_breakout_extension_atr: Decimal
    edge_cost_ratio: Decimal
    stop_atr: Decimal
    profit_atr: Decimal
    trail_activation_r: Decimal
    trail_atr: Decimal
    time_stop_hours: int
    btc_ema_fast_4h: int
    btc_ema_slow_4h: int
    btc_slope_periods_4h: int
    minimum_universe_size: int
    minimum_rolling_24h_quote_volume_usdt: Decimal


@dataclass(frozen=True)
class InstrumentFeatures:
    timestamp: int
    symbol: str
    close: Decimal
    momentum_12h: Decimal
    momentum_24h: Decimal
    momentum_72h: Decimal
    normalized_momentum: Decimal
    rank: Decimal
    percentile: Decimal
    realized_volatility_bps: Decimal
    atr: Decimal
    atr_bps: Decimal
    volatility_ratio: Decimal
    volume_ratio: Decimal
    rolling_quote_volume_usdt: Decimal
    prior_donchian_high: Decimal
    prior_donchian_low: Decimal
    long_breakout_bps: Decimal
    short_breakout_bps: Decimal
    liquidity_regime: str
    volatility_regime: str


@dataclass(frozen=True)
class ResearchCandidate:
    candidate_id: str
    strategy_id: str
    strategy_version: str
    parameter_version: str
    dataset_id: str
    config_hash: str
    timestamp: int
    symbol: str
    direction: str
    rank: Decimal
    percentile: Decimal
    momentum_12h: Decimal
    momentum_24h: Decimal
    momentum_72h: Decimal
    normalized_momentum: Decimal
    realized_volatility_bps: Decimal
    atr: Decimal
    donchian_level: Decimal
    breakout_distance_bps: Decimal
    volume_ratio: Decimal
    volatility_ratio: Decimal
    btc_regime: str
    expected_move_bps: Decimal
    execution_cost_bps: Decimal
    edge_cost_ratio: Decimal
    movement_budget_bps: Decimal
    candidate_quality_score: Decimal
    decision: str
    gates: Mapping[str, bool]
    first_rejection_reason: str | None
    all_rejection_reasons: tuple[str, ...]

    def record(self) -> dict[str, object]:
        return cast(dict[str, object], _serialize(asdict(self)))


@dataclass(frozen=True)
class SimulatedTrade:
    trade_id: str
    candidate_id: str
    strategy_id: str
    symbol: str
    direction: str
    entry_timestamp: int
    exit_timestamp: int
    decision_price: Decimal
    simulated_fill_price: Decimal
    exit_price: Decimal
    exit_reason: str
    holding_hours: int
    btc_regime: str
    volatility_regime: str
    liquidity_regime: str
    momentum_percentile: Decimal
    breakout_strength_bps: Decimal
    expected_move_bps: Decimal
    execution_cost_bps: Decimal
    edge_cost_ratio: Decimal
    gross_pnl_bps: Decimal
    net_pnl_bps: Decimal
    mfe_bps: Decimal
    mae_bps: Decimal
    mfe_capture: Decimal | None
    latency_model: str
    cost_model: str

    def record(self) -> dict[str, object]:
        return cast(dict[str, object], _serialize(asdict(self)))


def _serialize(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serialize(item) for item in value]
    return value


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _serialize(value), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def cross_sectional_zscores(values: Mapping[str, Decimal]) -> dict[str, Decimal]:
    if not values:
        return {}
    mean = sum(values.values(), Decimal(0)) / Decimal(len(values))
    variance = sum((value - mean) ** 2 for value in values.values()) / Decimal(len(values))
    if variance == 0:
        return {symbol: Decimal(0) for symbol in values}
    deviation = variance.sqrt()
    return {symbol: (value - mean) / deviation for symbol, value in values.items()}


def average_ranks(values: Mapping[str, Decimal]) -> dict[str, tuple[Decimal, Decimal]]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    result: dict[str, tuple[Decimal, Decimal]] = {}
    cursor = 0
    count = len(ordered)
    while cursor < count:
        stop = cursor + 1
        while stop < count and ordered[stop][1] == ordered[cursor][1]:
            stop += 1
        rank = (Decimal(cursor + 1) + Decimal(stop)) / Decimal(2)
        percentile = (Decimal(count) - rank) / Decimal(count - 1) if count > 1 else Decimal("0.5")
        for symbol, _ in ordered[cursor:stop]:
            result[symbol] = (rank, percentile)
        cursor = stop
    return result


def prior_donchian(candles: Sequence[Candle], index: int, lookback: int) -> tuple[Decimal, Decimal]:
    if index < lookback:
        raise ValueError("insufficient prior bars for Donchian channel")
    prior = candles[index - lookback : index]
    return max(row.high for row in prior), min(row.low for row in prior)


def _true_range(current: Candle, previous: Candle) -> Decimal:
    return max(
        current.high - current.low,
        abs(current.high - previous.close),
        abs(current.low - previous.close),
    )


def _median(values: Sequence[Decimal]) -> Decimal:
    return Decimal(str(statistics.median(values)))


def _ema(values: Sequence[Decimal], period: int) -> list[Decimal]:
    if not values:
        return []
    alpha = Decimal(2) / Decimal(period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (Decimal(1) - alpha) * result[-1])
    return result


def btc_regime_at(
    btc_four_hour: Sequence[Candle], timestamp: int, settings: WideStrategySettings
) -> str:
    available = [row for row in btc_four_hour if row.timestamp + 14_400 <= timestamp]
    required = settings.btc_ema_slow_4h + settings.btc_slope_periods_4h
    if len(available) < required:
        return "UNAVAILABLE"
    closes = [row.close for row in available]
    fast = _ema(closes, settings.btc_ema_fast_4h)
    slow = _ema(closes, settings.btc_ema_slow_4h)
    slope = fast[-1] - fast[-1 - settings.btc_slope_periods_4h]
    if fast[-1] > slow[-1] and slope > 0:
        return "BULL"
    if fast[-1] < slow[-1] and slope < 0:
        return "BEAR"
    return "CHOP"


def _base_features(
    candles: Sequence[Candle], index: int, settings: WideStrategySettings
) -> dict[str, Decimal] | None:
    warmup = max(
        max(settings.momentum_hours),
        settings.donchian_hours,
        settings.volatility_baseline_hours,
        settings.volume_baseline_hours,
        settings.atr_hours,
    )
    if index < warmup:
        return None
    current = candles[index]
    momentum = [
        (current.close / candles[index - hours].close - Decimal(1)) * Decimal(10_000)
        for hours in settings.momentum_hours
    ]
    range_start = max(1, index - settings.volatility_baseline_hours + 1)
    ranges = [
        _true_range(candles[position], candles[position - 1])
        for position in range(range_start, index + 1)
    ]
    atr = sum(ranges[-settings.atr_hours :], Decimal(0)) / Decimal(settings.atr_hours)
    baseline_range = _median(ranges[-settings.volatility_baseline_hours :])
    returns = [
        Decimal(str(math.log(float(candles[position].close / candles[position - 1].close))))
        for position in range(index - 23, index + 1)
    ]
    realized = Decimal(str(statistics.pstdev(float(value) for value in returns))) * Decimal(10_000)
    prior_high, prior_low = prior_donchian(candles, index, settings.donchian_hours)
    volumes = [
        row.quote_volume_usdt for row in candles[index - settings.volume_baseline_hours : index]
    ]
    median_volume = _median(volumes)
    rolling_volume = sum(
        (row.quote_volume_usdt for row in candles[index - 23 : index + 1]), Decimal(0)
    )
    return {
        "momentum_12h": momentum[0],
        "momentum_24h": momentum[1],
        "momentum_72h": momentum[2],
        "atr": atr,
        "atr_bps": atr / current.close * Decimal(10_000),
        "realized_volatility_bps": realized,
        "volatility_ratio": atr / baseline_range if baseline_range > 0 else Decimal(0),
        "volume_ratio": current.quote_volume_usdt / median_volume
        if median_volume > 0
        else Decimal(0),
        "rolling_quote_volume_usdt": rolling_volume,
        "prior_donchian_high": prior_high,
        "prior_donchian_low": prior_low,
        "long_breakout_bps": (current.close / prior_high - Decimal(1)) * Decimal(10_000),
        "short_breakout_bps": (prior_low / current.close - Decimal(1)) * Decimal(10_000),
    }


def features_at(
    candles_by_symbol: Mapping[str, Sequence[Candle]],
    timestamp: int,
    settings: WideStrategySettings,
    indices_by_symbol: Mapping[str, Mapping[int, int]] | None = None,
) -> tuple[dict[str, InstrumentFeatures], dict[str, str]]:
    raw: dict[str, dict[str, Decimal]] = {}
    rejected: dict[str, str] = {}
    for symbol, candles in candles_by_symbol.items():
        index_by_time = (
            indices_by_symbol[symbol]
            if indices_by_symbol is not None
            else {row.timestamp: index for index, row in enumerate(candles)}
        )
        index = index_by_time.get(timestamp)
        if index is None:
            rejected[symbol] = "STALE_DATA"
            continue
        values = _base_features(candles, index, settings)
        if values is None:
            rejected[symbol] = "INSUFFICIENT_HISTORY"
            continue
        if values["rolling_quote_volume_usdt"] < settings.minimum_rolling_24h_quote_volume_usdt:
            rejected[symbol] = "LOW_LIQUIDITY"
            continue
        raw[symbol] = values
    scores: dict[str, Decimal] = {symbol: Decimal(0) for symbol in raw}
    for field, weight in zip(
        ("momentum_12h", "momentum_24h", "momentum_72h"),
        settings.momentum_weights,
        strict=True,
    ):
        zscores = cross_sectional_zscores({symbol: values[field] for symbol, values in raw.items()})
        for symbol, zscore in zscores.items():
            scores[symbol] += weight * zscore
    ranks = average_ranks(scores)
    features: dict[str, InstrumentFeatures] = {}
    for symbol, values in raw.items():
        candles = candles_by_symbol[symbol]
        current = candles[index_by_time[timestamp]]
        rank, percentile = ranks[symbol]
        rolling_volumes = [item["rolling_quote_volume_usdt"] for item in raw.values()]
        median_liquidity = _median(rolling_volumes)
        features[symbol] = InstrumentFeatures(
            timestamp=timestamp,
            symbol=symbol,
            close=current.close,
            momentum_12h=values["momentum_12h"],
            momentum_24h=values["momentum_24h"],
            momentum_72h=values["momentum_72h"],
            normalized_momentum=scores[symbol],
            rank=rank,
            percentile=percentile,
            realized_volatility_bps=values["realized_volatility_bps"],
            atr=values["atr"],
            atr_bps=values["atr_bps"],
            volatility_ratio=values["volatility_ratio"],
            volume_ratio=values["volume_ratio"],
            rolling_quote_volume_usdt=values["rolling_quote_volume_usdt"],
            prior_donchian_high=values["prior_donchian_high"],
            prior_donchian_low=values["prior_donchian_low"],
            long_breakout_bps=values["long_breakout_bps"],
            short_breakout_bps=values["short_breakout_bps"],
            liquidity_regime=(
                "HIGH" if values["rolling_quote_volume_usdt"] >= median_liquidity else "NORMAL"
            ),
            volatility_regime=(
                "EXPANDING" if values["volatility_ratio"] >= Decimal(1) else "CONTRACTING"
            ),
        )
    return features, rejected


def prepare_feature_timeline(
    candles_by_symbol: Mapping[str, Sequence[Candle]],
    timestamps: Sequence[int],
    settings: WideStrategySettings,
) -> dict[int, tuple[dict[str, InstrumentFeatures], dict[str, str]]]:
    indices = {
        symbol: {row.timestamp: index for index, row in enumerate(rows)}
        for symbol, rows in candles_by_symbol.items()
    }
    return {
        timestamp: features_at(candles_by_symbol, timestamp, settings, indices)
        for timestamp in timestamps
    }


def prepare_btc_regimes(
    btc_four_hour: Sequence[Candle],
    timestamps: Sequence[int],
    settings: WideStrategySettings,
) -> dict[int, str]:
    return {
        timestamp: btc_regime_at(btc_four_hour, timestamp, settings) for timestamp in timestamps
    }


def roundtrip_cost_bps(profile: ExecutionProfile, snapshot_spread_bps: Decimal) -> Decimal:
    return (
        profile.fee_bps
        + snapshot_spread_bps * profile.spread_multiplier
        + profile.slippage_bps
        + profile.latency_bps
        + profile.adverse_selection_bps
        + profile.funding_bps
        + profile.impact_bps
    )


def build_candidates(
    family: StrategyFamily,
    features: Mapping[str, InstrumentFeatures],
    *,
    dataset_id: str,
    config_hash: str,
    parameter_version: str,
    btc_regime: str,
    profile: ExecutionProfile,
    snapshot_spreads: Mapping[str, Decimal],
    settings: WideStrategySettings,
) -> list[ResearchCandidate]:
    candidates: list[ResearchCandidate] = []
    minimum_rank_count = max(1, math.ceil(len(features) * float(settings.tail_fraction)))
    for feature in features.values():
        long_tail = feature.rank <= minimum_rank_count
        short_tail = feature.rank > len(features) - minimum_rank_count
        long_breakout = feature.long_breakout_bps > 0
        short_breakout = feature.short_breakout_bps > 0
        direction = "WAIT"
        if family == StrategyFamily.CS_MOMENTUM_ONLY_V1:
            direction = "LONG" if long_tail else "SHORT" if short_tail else "WAIT"
        elif family == StrategyFamily.CTA_BREAKOUT_ONLY_V1:
            direction = "LONG" if long_breakout else "SHORT" if short_breakout else "WAIT"
        elif long_tail and long_breakout:
            direction = "LONG"
        elif short_tail and short_breakout:
            direction = "SHORT"
        if direction == "WAIT":
            continue
        breakout = feature.long_breakout_bps if direction == "LONG" else feature.short_breakout_bps
        spread = snapshot_spreads.get(feature.symbol)
        cost = roundtrip_cost_bps(profile, spread if spread is not None else Decimal(9999))
        expected_move = feature.atr_bps * settings.profit_atr
        edge_ratio = expected_move / cost if cost > 0 else Decimal(0)
        movement_budget = cost * settings.edge_cost_ratio
        regime_ok = (direction == "LONG" and btc_regime == "BULL") or (
            direction == "SHORT" and btc_regime == "BEAR"
        )
        tail_ok = long_tail if direction == "LONG" else short_tail
        breakout_ok = long_breakout if direction == "LONG" else short_breakout
        extension_ok = breakout <= feature.atr_bps * settings.maximum_breakout_extension_atr
        gates = {
            "UNIVERSE": len(features) >= settings.minimum_universe_size,
            "MOMENTUM_TAIL": tail_ok,
            "BREAKOUT": breakout_ok,
            "VOLATILITY": feature.volatility_ratio >= settings.minimum_volatility_ratio,
            "VOLUME": feature.volume_ratio >= settings.minimum_volume_ratio,
            "EXTENSION": extension_ok,
            "BTC_REGIME": regime_ok,
            "COST_AVAILABLE": spread is not None,
            "EDGE_COST_RATIO": edge_ratio >= settings.edge_cost_ratio,
            "MOVEMENT_BUDGET": expected_move >= movement_budget,
        }
        required = ["UNIVERSE", "COST_AVAILABLE", "EDGE_COST_RATIO", "MOVEMENT_BUDGET"]
        if family in {
            StrategyFamily.CS_MOMENTUM_ONLY_V1,
            StrategyFamily.CROSS_SECTIONAL_BREAKOUT_V1,
        }:
            required.append("MOMENTUM_TAIL")
        if family in {
            StrategyFamily.CTA_BREAKOUT_ONLY_V1,
            StrategyFamily.CROSS_SECTIONAL_BREAKOUT_V1,
        }:
            required.extend(["BREAKOUT", "VOLATILITY", "VOLUME", "EXTENSION"])
        if family == StrategyFamily.CROSS_SECTIONAL_BREAKOUT_V1:
            required.append("BTC_REGIME")
        reasons = tuple(name for name in required if not gates[name])
        quality = (
            abs(feature.normalized_momentum) * Decimal("0.35")
            + max(Decimal(0), breakout / max(feature.atr_bps, Decimal("0.0001"))) * Decimal("0.20")
            + min(feature.volume_ratio, Decimal(3)) * Decimal("0.15")
            + min(feature.volatility_ratio, Decimal(3)) * Decimal("0.10")
            + min(edge_ratio, Decimal(6)) * Decimal("0.20")
        )
        identity = {
            "dataset": dataset_id,
            "strategy": family.value,
            "parameter": parameter_version,
            "timestamp": feature.timestamp,
            "symbol": feature.symbol,
            "direction": direction,
        }
        candidates.append(
            ResearchCandidate(
                candidate_id=_hash(identity)[:32],
                strategy_id=family.value,
                strategy_version="1",
                parameter_version=parameter_version,
                dataset_id=dataset_id,
                config_hash=config_hash,
                timestamp=feature.timestamp,
                symbol=feature.symbol,
                direction=direction,
                rank=feature.rank,
                percentile=feature.percentile,
                momentum_12h=feature.momentum_12h,
                momentum_24h=feature.momentum_24h,
                momentum_72h=feature.momentum_72h,
                normalized_momentum=feature.normalized_momentum,
                realized_volatility_bps=feature.realized_volatility_bps,
                atr=feature.atr,
                donchian_level=(
                    feature.prior_donchian_high
                    if direction == "LONG"
                    else feature.prior_donchian_low
                ),
                breakout_distance_bps=breakout,
                volume_ratio=feature.volume_ratio,
                volatility_ratio=feature.volatility_ratio,
                btc_regime=btc_regime,
                expected_move_bps=expected_move,
                execution_cost_bps=cost,
                edge_cost_ratio=edge_ratio,
                movement_budget_bps=movement_budget,
                candidate_quality_score=quality,
                decision="ACCEPTED" if not reasons else "REJECTED",
                gates=gates,
                first_rejection_reason=reasons[0] if reasons else None,
                all_rejection_reasons=reasons,
            )
        )
    return candidates


def _simulate_trade(
    candidate: ResearchCandidate,
    feature: InstrumentFeatures,
    candles: Sequence[Candle],
    settings: WideStrategySettings,
) -> SimulatedTrade | None:
    indices = {row.timestamp: index for index, row in enumerate(candles)}
    decision_index = indices[candidate.timestamp]
    entry_index = decision_index + 1
    if entry_index >= len(candles):
        return None
    entry = candles[entry_index]
    direction = Decimal(1) if candidate.direction == "LONG" else Decimal(-1)
    stop_distance = feature.atr * settings.stop_atr
    target_distance = feature.atr * settings.profit_atr
    stop = entry.open - direction * stop_distance
    target = entry.open + direction * target_distance
    best = entry.open
    worst = entry.open
    exit_price = entry.close
    exit_reason = "TIME_STOP"
    exit_timestamp = entry.timestamp
    max_index = min(len(candles) - 1, entry_index + settings.time_stop_hours - 1)
    for index in range(entry_index, max_index + 1):
        bar = candles[index]
        if candidate.direction == "LONG":
            best = max(best, bar.high)
            worst = min(worst, bar.low)
            if best >= entry.open + stop_distance * settings.trail_activation_r:
                stop = max(stop, best - feature.atr * settings.trail_atr)
            if bar.low <= stop:
                exit_price, exit_reason = stop, "STOP_OR_TRAIL"
                exit_timestamp = bar.timestamp
                break
            if bar.high >= target:
                exit_price, exit_reason = target, "PROFIT_OBJECTIVE"
                exit_timestamp = bar.timestamp
                break
        else:
            best = min(best, bar.low)
            worst = max(worst, bar.high)
            if best <= entry.open - stop_distance * settings.trail_activation_r:
                stop = min(stop, best + feature.atr * settings.trail_atr)
            if bar.high >= stop:
                exit_price, exit_reason = stop, "STOP_OR_TRAIL"
                exit_timestamp = bar.timestamp
                break
            if bar.low <= target:
                exit_price, exit_reason = target, "PROFIT_OBJECTIVE"
                exit_timestamp = bar.timestamp
                break
        exit_price = bar.close
        exit_timestamp = bar.timestamp
    gross = direction * (exit_price / entry.open - Decimal(1)) * Decimal(10_000)
    mfe = (
        (best / entry.open - Decimal(1)) * Decimal(10_000)
        if candidate.direction == "LONG"
        else (entry.open / best - Decimal(1)) * Decimal(10_000)
    )
    mae = (
        (worst / entry.open - Decimal(1)) * Decimal(10_000)
        if candidate.direction == "LONG"
        else (entry.open / worst - Decimal(1)) * Decimal(10_000)
    )
    net = gross - candidate.execution_cost_bps
    capture = gross / mfe if mfe > 0 else None
    trade_identity = {"candidate_id": candidate.candidate_id, "entry": entry.timestamp}
    return SimulatedTrade(
        trade_id=_hash(trade_identity)[:32],
        candidate_id=candidate.candidate_id,
        strategy_id=candidate.strategy_id,
        symbol=candidate.symbol,
        direction=candidate.direction,
        entry_timestamp=entry.timestamp,
        exit_timestamp=exit_timestamp,
        decision_price=feature.close,
        simulated_fill_price=entry.open,
        exit_price=exit_price,
        exit_reason=exit_reason,
        holding_hours=(exit_timestamp - entry.timestamp) // 3600 + 1,
        btc_regime=candidate.btc_regime,
        volatility_regime=feature.volatility_regime,
        liquidity_regime=feature.liquidity_regime,
        momentum_percentile=feature.percentile,
        breakout_strength_bps=candidate.breakout_distance_bps,
        expected_move_bps=candidate.expected_move_bps,
        execution_cost_bps=candidate.execution_cost_bps,
        edge_cost_ratio=candidate.edge_cost_ratio,
        gross_pnl_bps=gross,
        net_pnl_bps=net,
        mfe_bps=mfe,
        mae_bps=mae,
        mfe_capture=capture,
        latency_model="ONE_BAR_ENTRY_LAG",
        cost_model="VERSIONED_PROFILE_WITH_SNAPSHOT_SPREAD_PROXY",
    )


def replay_family(
    family: StrategyFamily,
    candles_by_symbol: Mapping[str, Sequence[Candle]],
    btc_four_hour: Sequence[Candle],
    *,
    dataset_id: str,
    config_hash: str,
    parameter_version: str,
    profile: ExecutionProfile,
    snapshot_spreads: Mapping[str, Decimal],
    settings: WideStrategySettings,
    start_timestamp: int | None = None,
    end_timestamp: int | None = None,
    prepared_features: Mapping[int, tuple[dict[str, InstrumentFeatures], dict[str, str]]]
    | None = None,
    prepared_regimes: Mapping[int, str] | None = None,
) -> tuple[list[ResearchCandidate], list[SimulatedTrade], dict[str, int]]:
    btc = candles_by_symbol["BTC_USDT"]
    timeline = [
        row.timestamp
        for row in btc
        if (start_timestamp is None or row.timestamp >= start_timestamp)
        and (end_timestamp is None or row.timestamp <= end_timestamp)
    ]
    candidates: list[ResearchCandidate] = []
    trades: list[SimulatedTrade] = []
    universe_rejections: dict[str, int] = {}
    busy_until = -1
    feature_timeline = prepared_features or prepare_feature_timeline(
        candles_by_symbol, timeline, settings
    )
    regimes = prepared_regimes or prepare_btc_regimes(btc_four_hour, timeline, settings)
    for timestamp in timeline:
        feature_map, rejected = feature_timeline[timestamp]
        for reason in rejected.values():
            universe_rejections[reason] = universe_rejections.get(reason, 0) + 1
        regime = regimes[timestamp]
        batch = build_candidates(
            family,
            feature_map,
            dataset_id=dataset_id,
            config_hash=config_hash,
            parameter_version=parameter_version,
            btc_regime=regime,
            profile=profile,
            snapshot_spreads=snapshot_spreads,
            settings=settings,
        )
        if timestamp <= busy_until:
            batch = [
                replace(
                    item,
                    decision="REJECTED",
                    first_rejection_reason="POSITION_SLOT_OCCUPIED",
                    all_rejection_reasons=tuple(
                        dict.fromkeys((*item.all_rejection_reasons, "POSITION_SLOT_OCCUPIED"))
                    ),
                )
                for item in batch
            ]
            candidates.extend(batch)
            continue
        accepted = [item for item in batch if item.decision == "ACCEPTED"]
        if accepted:
            winner = max(
                accepted,
                key=lambda item: (item.candidate_quality_score, item.symbol, item.direction),
            )
            batch = [
                item
                if item.candidate_id == winner.candidate_id or item.decision != "ACCEPTED"
                else replace(
                    item,
                    decision="REJECTED",
                    first_rejection_reason="WEAKER_THAN_SELECTED_CANDIDATE",
                    all_rejection_reasons=("WEAKER_THAN_SELECTED_CANDIDATE",),
                )
                for item in batch
            ]
            feature = feature_map[winner.symbol]
            trade = _simulate_trade(winner, feature, candles_by_symbol[winner.symbol], settings)
            if trade is not None:
                trades.append(trade)
                busy_until = trade.exit_timestamp
        candidates.extend(batch)
    if any(
        left.exit_timestamp >= right.entry_timestamp
        for left, right in zip(trades, trades[1:], strict=False)
    ):
        raise ValueError("replay violated the one-position invariant")
    return candidates, trades, universe_rejections


def _drawdown(values: Sequence[Decimal]) -> Decimal:
    equity = peak = drawdown = Decimal(0)
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def trade_metrics(trades: Sequence[SimulatedTrade], *, total_hours: int) -> dict[str, object]:
    gross = [trade.gross_pnl_bps for trade in trades]
    net = [trade.net_pnl_bps for trade in trades]
    wins = [value for value in net if value > 0]
    losses = [value for value in net if value < 0]
    gross_wins = sum(wins, Decimal(0))
    gross_losses = abs(sum(losses, Decimal(0)))
    mean = sum(net, Decimal(0)) / Decimal(len(net)) if net else None
    stdev = Decimal(str(statistics.pstdev(float(value) for value in net))) if len(net) > 1 else None
    downside = (
        Decimal(str(math.sqrt(statistics.fmean(float(value**2) for value in losses))))
        if losses
        else None
    )
    max_drawdown = _drawdown(net)
    result: dict[str, object] = {
        "sample_size": len(trades),
        "gross_pnl_bps": float(sum(gross, Decimal(0))),
        "net_pnl_bps": float(sum(net, Decimal(0))),
        "gross_expectancy_bps": float(sum(gross, Decimal(0)) / Decimal(len(gross)))
        if gross
        else None,
        "net_expectancy_bps": float(mean) if mean is not None else None,
        "median_trade_bps": float(_median(net)) if net else None,
        "win_rate": len(wins) / len(net) if net else None,
        "average_winner_bps": float(sum(wins, Decimal(0)) / Decimal(len(wins))) if wins else None,
        "average_loser_bps": float(sum(losses, Decimal(0)) / Decimal(len(losses)))
        if losses
        else None,
        "payoff_ratio": (
            float(
                (sum(wins, Decimal(0)) / Decimal(len(wins)))
                / abs(sum(losses, Decimal(0)) / Decimal(len(losses)))
            )
            if wins and losses
            else None
        ),
        "profit_factor": float(gross_wins / gross_losses) if gross_losses else None,
        "maximum_drawdown_bps": float(max_drawdown),
        "recovery_factor": float(sum(net, Decimal(0)) / max_drawdown) if max_drawdown else None,
        "sharpe_per_trade": float(mean / stdev * Decimal(str(math.sqrt(len(net)))))
        if mean is not None and stdev
        else None,
        "sortino_per_trade": float(mean / downside * Decimal(str(math.sqrt(len(net)))))
        if mean is not None and downside
        else None,
        "exposure_fraction": (
            sum(trade.holding_hours for trade in trades) / total_hours if total_hours > 0 else None
        ),
        "turnover_roundtrips": len(trades),
        "trades_per_day": len(trades) / (total_hours / 24) if total_hours > 0 else None,
        "average_holding_hours": statistics.fmean(trade.holding_hours for trade in trades)
        if trades
        else None,
        "average_mae_bps": statistics.fmean(float(trade.mae_bps) for trade in trades)
        if trades
        else None,
        "average_mfe_bps": statistics.fmean(float(trade.mfe_bps) for trade in trades)
        if trades
        else None,
        "average_mfe_capture": statistics.fmean(
            float(trade.mfe_capture) for trade in trades if trade.mfe_capture is not None
        )
        if any(trade.mfe_capture is not None for trade in trades)
        else None,
        "average_gross_edge_bps": statistics.fmean(float(trade.gross_pnl_bps) for trade in trades)
        if trades
        else None,
        "average_execution_cost_bps": statistics.fmean(
            float(trade.execution_cost_bps) for trade in trades
        )
        if trades
        else None,
        "average_net_edge_bps": statistics.fmean(float(trade.net_pnl_bps) for trade in trades)
        if trades
        else None,
        "average_edge_cost_ratio": statistics.fmean(
            float(trade.edge_cost_ratio) for trade in trades
        )
        if trades
        else None,
    }
    return result


def segmented_metrics(trades: Sequence[SimulatedTrade], *, total_hours: int) -> dict[str, object]:
    fields = {
        "direction": lambda trade: trade.direction,
        "instrument": lambda trade: trade.symbol,
        "btc_regime": lambda trade: trade.btc_regime,
        "volatility_regime": lambda trade: trade.volatility_regime,
        "liquidity_regime": lambda trade: trade.liquidity_regime,
        "momentum_rank": lambda trade: (
            "TOP" if trade.momentum_percentile >= Decimal("0.9") else "BOTTOM"
        ),
        "breakout_strength": lambda trade: (
            "STRONG" if trade.breakout_strength_bps >= Decimal(10) else "WEAK"
        ),
        "time_period": lambda trade: datetime.fromtimestamp(trade.entry_timestamp, UTC).strftime(
            "%Y-%m"
        ),
    }
    result: dict[str, object] = {}
    for name, classifier in fields.items():
        grouped: dict[str, list[SimulatedTrade]] = {}
        for trade in trades:
            grouped.setdefault(classifier(trade), []).append(trade)
        result[name] = {
            key: trade_metrics(values, total_hours=total_hours)
            for key, values in sorted(grouped.items())
        }
    return result
