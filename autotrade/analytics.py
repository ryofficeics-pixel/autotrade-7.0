from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from autotrade.time_utils import WIB, canonical_utc, parse_timestamp, utc_to_wib

ZERO = Decimal(0)


def _decimal(value: object, default: Decimal = ZERO) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default
    return result if result.is_finite() else default


def load_jsonl(path: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}: {exc}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"event at line {line_number} is not an object")
            events.append(event)
    return events


def classify_close(event: Mapping[str, object]) -> str:
    explicit = str(event.get("classification") or "").upper()
    if explicit in {"NORMAL", "MANUAL", "OUTAGE_HELD", "RECOVERY"}:
        return "OUTAGE_HELD" if explicit == "RECOVERY" else explicit
    event_type = str(event.get("event_type", event.get("event", "")))
    reason = str(event.get("reason", "")).upper()
    if event_type == "recovery_flatten" or reason == "RECOVERY_FLATTEN":
        return "OUTAGE_HELD"
    if "MANUAL" in reason:
        return "MANUAL"
    return "NORMAL"


def _fill_totals(fills: Sequence[Mapping[str, object]]) -> tuple[Decimal, Decimal, Decimal]:
    quantity = ZERO
    notional = ZERO
    fees = ZERO
    for fill in fills:
        fill_quantity = _decimal(fill.get("quantity"))
        fill_price = _decimal(fill.get("price"))
        quantity += fill_quantity
        notional += fill_quantity * fill_price
        fees += _decimal(fill.get("fee_amount", fill.get("fee_usdt", 0)))
    return quantity, notional, fees


def _sum_field(fills: Sequence[Mapping[str, object]], field: str) -> tuple[Decimal, bool]:
    known = [fill for fill in fills if fill.get(field) is not None]
    return sum((_decimal(fill[field]) for fill in known), ZERO), len(known) == len(fills) and bool(
        fills
    )


def reconstruct_trades(events: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    contexts: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, object]] = []
    for event in events:
        event_type = str(event.get("event_type", event.get("event", "")))
        position_id = str(event.get("position_id") or "")
        if event_type == "signal" and position_id:
            contexts[position_id] = {
                "signal": dict(event),
                "entry_fills": [],
                "exit_fills": [],
                "closing": False,
            }
            continue
        if not position_id:
            continue
        context = contexts.setdefault(
            position_id,
            {"signal": {}, "entry_fills": [], "exit_fills": [], "closing": False},
        )
        if event_type == "exit_signal":
            context["closing"] = True
            continue
        if event_type == "fill":
            key = "exit_fills" if context["closing"] else "entry_fills"
            fills = context[key]
            if isinstance(fills, list):
                fills.append(dict(event))
            continue
        if event_type not in {"position_closed", "recovery_flatten"}:
            continue

        signal = cast(
            dict[str, object],
            context.get("signal") if isinstance(context.get("signal"), dict) else {},
        )
        entry_fills = cast(
            list[Mapping[str, object]],
            context.get("entry_fills") if isinstance(context.get("entry_fills"), list) else [],
        )
        exit_fills = cast(
            list[Mapping[str, object]],
            context.get("exit_fills") if isinstance(context.get("exit_fills"), list) else [],
        )
        side = str(event.get("side") or signal.get("side") or "UNKNOWN").upper()
        quantity, entry_notional, entry_fee = _fill_totals(entry_fills)
        exit_quantity, exit_notional, exit_fee = _fill_totals(exit_fills)
        if event_type == "recovery_flatten":
            quantity = _decimal(event.get("quantity"), quantity)
            open_price = _decimal(event.get("open_price"))
            close_price = _decimal(event.get("close_price", event.get("price")))
            entry_notional = quantity * open_price
            exit_notional = quantity * close_price
            total_fee = _decimal(event.get("total_fee_usdt", event.get("fee_amount", 0)))
            exit_fee = _decimal(event.get("ledger_fee_usdt", event.get("fee_amount", 0)))
            entry_fee = max(ZERO, total_fee - exit_fee)
            exit_quantity = quantity
        else:
            quantity = quantity or _decimal(event.get("quantity"))
            open_price = (
                entry_notional / quantity if quantity else _decimal(event.get("open_price"))
            )
            close_price = (
                exit_notional / exit_quantity
                if exit_quantity
                else _decimal(event.get("close_price", event.get("price")))
            )
            if not entry_notional:
                entry_notional = quantity * open_price
            if not exit_notional:
                exit_notional = quantity * close_price
            total_fee = entry_fee + exit_fee
            if not total_fee:
                total_fee = _decimal(event.get("fee_amount", event.get("fee_usdt", 0)))
        gross = (
            (close_price - open_price) * quantity * (Decimal(1) if side == "LONG" else Decimal(-1))
        )
        net = _decimal(
            event.get("total_trade_pnl_usdt", event.get("realized_pnl_usdt")),
            gross - total_fee,
        )
        all_fills = [*entry_fills, *exit_fills]
        spread_cost, spread_known = _sum_field(all_fills, "spread_cost_usdt")
        slippage_cost, slippage_known = _sum_field(all_fills, "modeled_slippage_usdt")
        funding = _decimal(event.get("funding_usdt", 0))
        other = _decimal(event.get("other_execution_adjustments_usdt", 0))
        intended_risk = _decimal(signal.get("intended_risk_usdt"))
        holding_ms = event.get("holding_time_ms")
        opened_at = event.get("position_open_timestamp")
        closed_at = canonical_utc(str(event.get("timestamp_utc")))
        if holding_ms is None and opened_at:
            holding_ms = max(
                0,
                round(
                    (parse_timestamp(closed_at) - parse_timestamp(str(opened_at))).total_seconds()
                    * 1000
                ),
            )
        classification = classify_close(event)
        fee_rate = (
            total_fee / (entry_notional + exit_notional) if entry_notional + exit_notional else ZERO
        )
        trade_symbol = str(event.get("symbol", signal.get("symbol", "")))
        record: dict[str, object] = {
            "closed_at": closed_at,
            "closed_at_wib": utc_to_wib(closed_at),
            "symbol": trade_symbol,
            "side": side,
            "quantity": float(quantity),
            "open_price": float(open_price),
            "close_price": float(close_price),
            "entry_notional_usdt": float(entry_notional),
            "exit_notional_usdt": float(exit_notional),
            "gross_price_pnl_usdt": float(gross),
            "entry_fee_usdt": float(entry_fee),
            "exit_fee_usdt": float(exit_fee),
            "total_fee_usdt": float(total_fee),
            "fee_rate_on_turnover": float(fee_rate),
            "maker_taker": str(event.get("maker_taker") or "TAKER"),
            "spread_cost_usdt": float(spread_cost) if spread_known else None,
            "modeled_slippage_usdt": float(slippage_cost) if slippage_known else None,
            "slippage_embedded_in_fill_price": True,
            "funding_usdt": float(funding),
            "funding_status": str(event.get("funding_status") or "NOT_MODELED"),
            "other_execution_adjustments_usdt": float(other),
            "net_pnl_usdt": float(net),
            "realized_pnl_usdt": float(net),
            "pnl_pct": float(net / entry_notional * 100) if entry_notional else 0.0,
            "r_multiple": float(net / intended_risk) if intended_risk else None,
            "intended_risk_usdt": float(intended_risk) if intended_risk else None,
            "actual_realized_risk_usdt": float(-min(net, ZERO)),
            "holding_time_ms": int(str(holding_ms)) if holding_ms is not None else None,
            "reason": str(event.get("reason", "UNKNOWN")),
            "strategy_id": str(event.get("strategy_id", signal.get("strategy_id", "UNKNOWN"))),
            "market_scope": event.get("market_scope", signal.get("market_scope")),
            "market_class": event.get(
                "market_class",
                signal.get(
                    "market_class", "xau" if trade_symbol == "XAU_USDT" else "crypto"
                ),
            ),
            "strategy": event.get("strategy", signal.get("strategy")),
            "entry_reason": event.get("entry_reason", signal.get("entry_reason")),
            "regime": event.get("regime", signal.get("regime")),
            "confidence": event.get("confidence", signal.get("confidence")),
            "risk_profile": event.get("risk_profile", signal.get("risk_profile")),
            "mode_at_entry": event.get("mode_at_entry", signal.get("mode_at_entry")),
            "confirmation_state": event.get(
                "confirmation_state", signal.get("confirmation_state")
            ),
            "session": event.get("session", signal.get("session", "UNCLASSIFIED")),
            "signal_id": event.get("signal_id", signal.get("signal_id")),
            "position_id": position_id,
            "classification": classification,
            "signal_score": signal.get("confidence"),
            "candidate_rank": signal.get("candidate_rank"),
            "signal_direction": signal.get("direction", signal.get("side")),
            "market_regime": signal.get("market_regime", "UNAVAILABLE"),
            "volatility_regime": signal.get("volatility_regime", "UNAVAILABLE"),
            "liquidity_regime": signal.get("liquidity_regime", "UNAVAILABLE"),
            "entry_spread_bps": signal.get("entry_spread_bps"),
            "exit_spread_bps": event.get("exit_spread_bps"),
            "depth_usdt": signal.get("depth_usdt"),
            "expected_round_trip_cost_bps": signal.get("cost_bps"),
            "expected_movement_bps": signal.get("gross_edge_bps"),
            "expected_move_to_cost_ratio": (
                float(_decimal(signal.get("gross_edge_bps")) / _decimal(signal.get("cost_bps")))
                if _decimal(signal.get("cost_bps")) > 0
                else None
            ),
            "stop_distance_bps": signal.get("stop_distance_bps"),
            "mfe_bps": event.get("mfe_bps"),
            "mae_bps": event.get("mae_bps"),
            "time_to_mfe_ms": event.get("time_to_mfe_ms"),
            "time_to_mae_ms": event.get("time_to_mae_ms"),
            "post_exit_returns_bps": event.get("post_exit_returns_bps"),
        }
        trades.append(record)
        contexts.pop(position_id, None)

    prior_by_symbol: dict[str, dict[str, object]] = {}
    streak_by_symbol: dict[str, int] = defaultdict(int)
    for trade in sorted(trades, key=lambda item: parse_timestamp(str(item["closed_at"]))):
        symbol = str(trade["symbol"])
        prior = prior_by_symbol.get(symbol)
        trade["previous_symbol_trade_pnl_usdt"] = prior.get("net_pnl_usdt") if prior else None
        trade["time_since_previous_exit_ms"] = (
            round(
                (
                    parse_timestamp(str(trade["closed_at"]))
                    - parse_timestamp(str(prior["closed_at"]))
                ).total_seconds()
                * 1000
            )
            if prior
            else None
        )
        streak_by_symbol[symbol] += 1
        trade["reentry_sequence"] = streak_by_symbol[symbol]
        prior_by_symbol[symbol] = trade
    return sorted(trades, key=lambda item: parse_timestamp(str(item["closed_at"])))


def _drawdown(pnls: Sequence[Decimal], starting_equity: Decimal) -> tuple[Decimal, Decimal]:
    equity = starting_equity
    peak = starting_equity
    maximum_usdt = ZERO
    maximum_pct = ZERO
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = peak - equity
        maximum_usdt = max(maximum_usdt, drawdown)
        if peak:
            maximum_pct = max(maximum_pct, drawdown / peak * 100)
    return maximum_usdt, maximum_pct


def _bucket_summary(trades: Sequence[Mapping[str, object]]) -> dict[str, object]:
    net = [
        _decimal(trade.get("net_pnl_usdt", trade.get("realized_pnl_usdt", 0))) for trade in trades
    ]
    gross = [_decimal(trade.get("gross_price_pnl_usdt", 0)) for trade in trades]
    fees = [_decimal(trade.get("total_fee_usdt", trade.get("fee_usdt", 0))) for trade in trades]
    winners = [value for value in net if value > 0]
    losers = [value for value in net if value < 0]
    gross_profit = sum(winners, ZERO)
    gross_loss = -sum(losers, ZERO)
    average_win = gross_profit / len(winners) if winners else ZERO
    average_loss = gross_loss / len(losers) if losers else ZERO
    payoff = average_win / average_loss if average_loss else ZERO
    holding_times = [
        _decimal(trade.get("holding_time_ms"))
        for trade in trades
        if trade.get("holding_time_ms") is not None
    ]
    return {
        "trades": len(trades),
        "wins": len(winners),
        "losses": len(losers),
        "win_rate_pct": float(Decimal(len(winners)) / len(trades) * 100) if trades else 0.0,
        "gross_price_pnl_usdt": float(sum(gross, ZERO)),
        "fees_usdt": float(sum(fees, ZERO)),
        "net_pnl_usdt": float(sum(net, ZERO)),
        "expectancy_usdt": float(sum(net, ZERO) / len(net)) if net else 0.0,
        "average_hold_seconds": (
            float(sum(holding_times, ZERO) / len(holding_times) / Decimal(1000))
            if holding_times
            else None
        ),
        "average_winner_usdt": float(average_win),
        "average_loser_usdt": float(-average_loss) if average_loss else 0.0,
        "profit_factor": float(gross_profit / gross_loss) if gross_loss else None,
        "break_even_win_rate_pct": float(Decimal(1) / (Decimal(1) + payoff) * 100)
        if payoff
        else None,
        "turnover_usdt": float(
            sum(
                (
                    _decimal(trade.get("entry_notional_usdt", 0))
                    + _decimal(trade.get("exit_notional_usdt", 0))
                    for trade in trades
                ),
                ZERO,
            )
        ),
    }


def aggregate_trades(
    trades: Sequence[Mapping[str, object]],
    *,
    starting_equity: Decimal,
    active_hours: Decimal | None = None,
) -> dict[str, object]:
    normal = [trade for trade in trades if trade.get("classification") == "NORMAL"]
    recovery = [trade for trade in trades if trade.get("classification") == "OUTAGE_HELD"]
    manual = [trade for trade in trades if trade.get("classification") == "MANUAL"]
    normal_pnls = [_decimal(trade.get("net_pnl_usdt")) for trade in normal]
    total_pnls = [_decimal(trade.get("net_pnl_usdt")) for trade in trades]
    normal_dd, normal_dd_pct = _drawdown(normal_pnls, starting_equity)
    total_dd, total_dd_pct = _drawdown(total_pnls, starting_equity)
    known_slippage = [trade for trade in trades if trade.get("modeled_slippage_usdt") is not None]
    known_spread = [trade for trade in trades if trade.get("spread_cost_usdt") is not None]

    by_utc_day: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    by_wib_day: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for trade in trades:
        closed = parse_timestamp(str(trade["closed_at"]))
        by_utc_day[closed.date().isoformat()].append(trade)
        by_wib_day[closed.astimezone(WIB).date().isoformat()].append(trade)

    def breakdown(key: str) -> dict[str, object]:
        buckets: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for trade in normal:
            value: object
            if key == "hour_utc":
                value = parse_timestamp(str(trade["closed_at"])).hour
            elif key == "holding_bucket":
                seconds = int(str(trade.get("holding_time_ms") or 0)) / 1000
                value = "<1m" if seconds < 60 else "1-5m" if seconds <= 300 else ">5m"
            elif key == "score_bucket":
                score = trade.get("signal_score")
                score_floor = int(float(str(score)) * 10) * 10 if score is not None else None
                value = (
                    "UNAVAILABLE"
                    if score_floor is None
                    else f"{score_floor:02d}-{score_floor + 9:02d}%"
                )
            else:
                value = trade.get(key, "UNAVAILABLE")
            buckets[str(value)].append(trade)
        return {name: _bucket_summary(items) for name, items in sorted(buckets.items())}

    full = _bucket_summary(trades)
    gross_profit = sum(
        (max(_decimal(trade.get("gross_price_pnl_usdt")), ZERO) for trade in trades), ZERO
    )
    total_costs = sum((_decimal(trade.get("total_fee_usdt")) for trade in trades), ZERO)
    total_funding = sum((_decimal(trade.get("funding_usdt")) for trade in trades), ZERO)
    crypto_trades = [
        trade
        for trade in trades
        if trade.get("market_class", "xau" if trade.get("symbol") == "XAU_USDT" else "crypto")
        == "crypto"
    ]
    xau_trades = [trade for trade in trades if trade not in crypto_trades]

    def market_summary(items: Sequence[Mapping[str, object]]) -> dict[str, object]:
        summary = _bucket_summary(items)
        drawdown, drawdown_pct = _drawdown(
            [
                _decimal(item.get("net_pnl_usdt", item.get("realized_pnl_usdt", 0)))
                for item in items
            ],
            starting_equity,
        )
        summary.update(
            max_drawdown_usdt=float(drawdown),
            max_drawdown_pct=float(drawdown_pct),
            unrealized_pnl_usdt=0.0,
        )
        return summary
    return {
        "accounting_convention": (
            "NET_EQUALS_GROSS_PRICE_PNL_MINUS_FEES_PLUS_FUNDING_AND_ADJUSTMENTS; "
            "SPREAD_AND_SLIPPAGE_ARE_EMBEDDED_IN_FILL_PRICES"
        ),
        "normal": _bucket_summary(normal),
        "recovery": _bucket_summary(recovery),
        "manual": _bucket_summary(manual),
        "full_run": full,
        "market_classes": {
            "ALL": market_summary(trades),
            "CRYPTO": market_summary(crypto_trades),
            "XAU": {
                **market_summary(xau_trades),
                "by_direction": {
                    side: _bucket_summary(
                        [trade for trade in xau_trades if trade.get("side") == side]
                    )
                    for side in ("LONG", "SHORT")
                },
                "by_regime": {
                    regime: _bucket_summary(
                        [trade for trade in xau_trades if str(trade.get("regime")) == regime]
                    )
                    for regime in sorted(
                        {str(trade.get("regime", "UNAVAILABLE")) for trade in xau_trades}
                    )
                },
            },
        },
        "total_costs_usdt": float(total_costs),
        "cost_as_pct_of_gross_trading_profit": float(total_costs / gross_profit * 100)
        if gross_profit
        else None,
        "modeled_slippage_usdt": float(
            sum((_decimal(trade.get("modeled_slippage_usdt")) for trade in known_slippage), ZERO)
        )
        if known_slippage
        else None,
        "modeled_slippage_coverage": f"{len(known_slippage)}/{len(trades)}",
        "spread_cost_usdt": float(
            sum((_decimal(trade.get("spread_cost_usdt")) for trade in known_spread), ZERO)
        )
        if known_spread
        else None,
        "spread_cost_coverage": f"{len(known_spread)}/{len(trades)}",
        "funding_usdt": float(total_funding),
        "funding_status": "MODELED"
        if all(trade.get("funding_status") == "MODELED" for trade in trades) and trades
        else "NOT_MODELED",
        "normal_strategy_max_drawdown_usdt": float(normal_dd),
        "normal_strategy_max_drawdown_pct": float(normal_dd_pct),
        "total_accounting_max_drawdown_usdt": float(total_dd),
        "total_accounting_max_drawdown_pct": float(total_dd_pct),
        "trades_per_active_hour": float(Decimal(len(normal)) / active_hours)
        if active_hours and active_hours > 0
        else None,
        "active_hours": float(active_hours) if active_hours is not None else None,
        "daily_utc": {day: _bucket_summary(items) for day, items in sorted(by_utc_day.items())},
        "daily_wib": {day: _bucket_summary(items) for day, items in sorted(by_wib_day.items())},
        "breakdowns": {
            key: breakdown(key)
            for key in (
                "symbol",
                "side",
                "strategy_id",
                "reason",
                "reentry_sequence",
                "hour_utc",
                "volatility_regime",
                "liquidity_regime",
                "score_bucket",
                "holding_bucket",
            )
        },
    }


_LOG_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")


def dashboard_activity_intervals(
    path: Path,
    local_day: date,
    *,
    maximum_gap: timedelta = timedelta(minutes=2),
) -> list[tuple[datetime, datetime]]:
    timestamps: list[datetime] = []
    with path.open(encoding="utf-8") as file:
        for line in file:
            match = _LOG_TIMESTAMP.match(line)
            if not match:
                continue
            local = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=WIB)
            if local.date() == local_day:
                timestamps.append(local.astimezone(UTC))
    if not timestamps:
        return []
    intervals: list[tuple[datetime, datetime]] = []
    start = prior = timestamps[0]
    for current in timestamps[1:]:
        if current - prior > maximum_gap:
            intervals.append((start, prior))
            start = current
        prior = current
    intervals.append((start, prior))
    return intervals


def interval_hours(intervals: Iterable[tuple[datetime, datetime]]) -> Decimal:
    seconds = sum((max(0.0, (end - start).total_seconds()) for start, end in intervals), 0.0)
    return Decimal(str(seconds)) / Decimal(3600)


@dataclass(frozen=True)
class WalkForwardSplit:
    selection: range
    validation: range
    holdout: range


def chronological_split(count: int) -> WalkForwardSplit:
    if count < 5:
        raise ValueError("at least five observations are required")
    selection_end = max(1, int(count * 0.6))
    validation_end = max(selection_end + 1, int(count * 0.8))
    validation_end = min(validation_end, count - 1)
    return WalkForwardSplit(
        range(0, selection_end), range(selection_end, validation_end), range(validation_end, count)
    )


def bootstrap_expectancy_ci(
    values: Sequence[Decimal], *, samples: int = 5_000, seed: int = 20260915
) -> tuple[Decimal, Decimal] | None:
    if len(values) < 10 or samples < 100:
        return None
    generator = random.Random(seed)
    means = sorted(
        sum((generator.choice(values) for _ in values), ZERO) / len(values) for _ in range(samples)
    )
    return means[int(samples * 0.025)], means[min(samples - 1, int(samples * 0.975))]


def evaluate_outcomes(
    trades: Sequence[Mapping[str, object]], *, doubled_costs: bool = False
) -> dict[str, object]:
    outcomes: list[Decimal] = []
    by_symbol: dict[str, Decimal] = defaultdict(Decimal)
    for trade in trades:
        net = _decimal(trade.get("net_pnl_usdt"))
        if doubled_costs:
            net -= _decimal(trade.get("total_fee_usdt"))
        outcomes.append(net)
        by_symbol[str(trade.get("symbol", "UNKNOWN"))] += net
    summary = _bucket_summary(
        [
            {**dict(trade), "net_pnl_usdt": float(outcome)}
            for trade, outcome in zip(trades, outcomes, strict=True)
        ]
    )
    interval = bootstrap_expectancy_ci(outcomes)
    positive_total = sum((max(value, ZERO) for value in by_symbol.values()), ZERO)
    dominant_symbol_share = (
        max(by_symbol.values(), default=ZERO) / positive_total if positive_total else None
    )
    positive_trades = [value for value in outcomes if value > 0]
    dominant_trade_share = (
        max(positive_trades, default=ZERO) / sum(positive_trades, ZERO) if positive_trades else None
    )
    return {
        **summary,
        "expectancy_usdt": float(sum(outcomes, ZERO) / len(outcomes)) if outcomes else None,
        "bootstrap_95pct_ci_usdt": [float(interval[0]), float(interval[1])] if interval else None,
        "dominant_symbol_profit_share_pct": float(dominant_symbol_share * 100)
        if dominant_symbol_share is not None
        else None,
        "dominant_trade_profit_share_pct": float(dominant_trade_share * 100)
        if dominant_trade_share is not None
        else None,
        "evidence_status": "INSUFFICIENT EVIDENCE"
        if len(outcomes) < 100 or interval is None
        else "UNPROVEN",
    }
