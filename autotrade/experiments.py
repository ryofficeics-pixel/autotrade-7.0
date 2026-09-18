from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from autotrade.time_utils import parse_timestamp

WIB = timezone(timedelta(hours=7), name="WIB")


@dataclass(frozen=True)
class SizingResult:
    quantity: Decimal
    intended_risk_usdt: Decimal
    entry_notional_usdt: Decimal
    reason: str | None = None


def risk_adjusted_quantity(
    *,
    entry_price: Decimal,
    stop_distance_bps: Decimal,
    risk_budget_usdt: Decimal,
    fee_buffer_bps: Decimal,
    slippage_buffer_bps: Decimal,
    maximum_position_notional_usdt: Decimal,
    maximum_symbol_exposure_usdt: Decimal,
    liquidity_cap_usdt: Decimal,
    size_increment: Decimal,
    minimum_quantity: Decimal,
) -> SizingResult:
    values = (
        entry_price,
        stop_distance_bps,
        risk_budget_usdt,
        fee_buffer_bps,
        slippage_buffer_bps,
        maximum_position_notional_usdt,
        maximum_symbol_exposure_usdt,
        liquidity_cap_usdt,
        size_increment,
        minimum_quantity,
    )
    if not all(value.is_finite() for value in values):
        return SizingResult(Decimal(0), Decimal(0), Decimal(0), "NON_FINITE_SIZING_INPUT")
    if entry_price <= 0 or stop_distance_bps <= 0 or risk_budget_usdt <= 0:
        return SizingResult(Decimal(0), Decimal(0), Decimal(0), "INVALID_RISK_INPUT")
    if size_increment <= 0 or minimum_quantity <= 0:
        return SizingResult(Decimal(0), Decimal(0), Decimal(0), "INVALID_SIZE_INCREMENT")
    total_risk_bps = stop_distance_bps + fee_buffer_bps + slippage_buffer_bps
    if total_risk_bps <= 0:
        return SizingResult(Decimal(0), Decimal(0), Decimal(0), "INVALID_RISK_DISTANCE")
    risk_quantity = risk_budget_usdt / (entry_price * total_risk_bps / Decimal(10_000))
    caps = [
        cap
        for cap in (
            maximum_position_notional_usdt,
            maximum_symbol_exposure_usdt,
            liquidity_cap_usdt,
        )
        if cap > 0
    ]
    if not caps:
        return SizingResult(Decimal(0), Decimal(0), Decimal(0), "NO_NOTIONAL_CAP")
    quantity = min(risk_quantity, min(caps) / entry_price)
    quantity = (quantity / size_increment).to_integral_value(rounding=ROUND_DOWN) * size_increment
    if quantity < minimum_quantity:
        return SizingResult(Decimal(0), Decimal(0), Decimal(0), "BELOW_MINIMUM_QUANTITY")
    notional = quantity * entry_price
    intended_risk = notional * total_risk_bps / Decimal(10_000)
    if intended_risk <= 0 or notional <= 0:
        return SizingResult(Decimal(0), Decimal(0), Decimal(0), "ZERO_SAFE_QUANTITY")
    return SizingResult(quantity, intended_risk, notional)


def capped_notional_quantity(
    *, entry_price: Decimal, notional_cap_usdt: Decimal, size_increment: Decimal
) -> SizingResult:
    return risk_adjusted_quantity(
        entry_price=entry_price,
        stop_distance_bps=Decimal("1"),
        risk_budget_usdt=notional_cap_usdt / Decimal(10_000),
        fee_buffer_bps=Decimal(0),
        slippage_buffer_bps=Decimal(0),
        maximum_position_notional_usdt=notional_cap_usdt,
        maximum_symbol_exposure_usdt=notional_cap_usdt,
        liquidity_cap_usdt=notional_cap_usdt,
        size_increment=size_increment,
        minimum_quantity=size_increment,
    )


def deterministic_candidate_ranking(
    candidates: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    normalized = [dict(candidate) for candidate in candidates]
    return sorted(
        normalized,
        key=lambda item: (
            -float(str(item.get("confidence", 0))),
            -float(str(item.get("expected_net_bps", 0))),
            float(str(item.get("spread_bps", 1_000_000))),
            str(item.get("symbol", "")),
        ),
    )


def cost_gate_reason(
    expected_movement_bps: Decimal,
    estimated_round_trip_cost_bps: Decimal,
    multiplier: Decimal,
) -> str | None:
    if multiplier <= 0 or estimated_round_trip_cost_bps < 0:
        return "INVALID_COST_GATE"
    if expected_movement_bps < multiplier * estimated_round_trip_cost_bps:
        return "COST_TO_EDGE_REJECTED"
    return None


def market_quality_reasons(
    market: Mapping[str, object],
    *,
    maximum_spread_bps: Decimal,
    minimum_quote_volume: Decimal,
    minimum_depth_usdt: Decimal,
    maximum_one_bar_volatility_bps: Decimal,
    maximum_price_gap_bps: Decimal,
    require_order_book: bool,
    stale_after_seconds: Decimal,
) -> list[str]:
    reasons: list[str] = []

    def number(name: str) -> Decimal | None:
        try:
            value = Decimal(str(market[name]))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return None
        return value if value.is_finite() else None

    spread = number("spread_bps")
    volume = number("volume_quote")
    depth = number("depth_usdt")
    volatility = number("one_bar_volatility_bps")
    gap = number("price_gap_bps")
    age = number("age_seconds")
    if spread is None or spread > maximum_spread_bps:
        reasons.append("SPREAD_LIMIT")
    if volume is None or volume < minimum_quote_volume:
        reasons.append("TURNOVER_LIMIT")
    if minimum_depth_usdt > 0 and (depth is None or depth < minimum_depth_usdt):
        reasons.append("DEPTH_LIMIT")
    if maximum_one_bar_volatility_bps > 0 and (
        volatility is None or volatility > maximum_one_bar_volatility_bps
    ):
        reasons.append("ABNORMAL_VOLATILITY")
    if maximum_price_gap_bps > 0 and (gap is None or gap > maximum_price_gap_bps):
        reasons.append("PRICE_GAP")
    if age is not None and age > stale_after_seconds:
        reasons.append("STALE_MARKET_DATA")
    if require_order_book and market.get("order_book_complete") is not True:
        reasons.append("INCOMPLETE_ORDER_BOOK")
    return reasons


def churn_block_reason(
    prior_trades: Iterable[Mapping[str, object]],
    *,
    symbol: str,
    now_utc: str | datetime,
    signal_reset_required: bool,
    signal_has_reset: bool,
    maximum_attempts: int,
    attempt_window_seconds: int,
    maximum_consecutive_losses: int,
    maximum_utc_day_loss_usdt: Decimal,
    maximum_wib_day_loss_usdt: Decimal,
) -> str | None:
    now = parse_timestamp(now_utc)
    matching = [
        trade
        for trade in prior_trades
        if str(trade.get("symbol")) == symbol
        and str(trade.get("classification", "NORMAL")) == "NORMAL"
    ]
    if signal_reset_required and not signal_has_reset:
        return "SIGNAL_RESET_REQUIRED"
    if maximum_attempts > 0 and attempt_window_seconds > 0:
        cutoff = now - timedelta(seconds=attempt_window_seconds)
        attempts = sum(
            1
            for trade in matching
            if parse_timestamp(str(trade.get("closed_at", trade.get("timestamp_utc")))) >= cutoff
        )
        if attempts >= maximum_attempts:
            return "SYMBOL_ATTEMPT_LIMIT"
    if maximum_consecutive_losses > 0:
        consecutive = 0
        for trade in sorted(
            matching,
            key=lambda item: parse_timestamp(str(item.get("closed_at", item.get("timestamp_utc")))),
            reverse=True,
        ):
            if Decimal(str(trade.get("net_pnl_usdt", trade.get("realized_pnl_usdt", 0)))) >= 0:
                break
            consecutive += 1
        if consecutive >= maximum_consecutive_losses:
            return "SYMBOL_CONSECUTIVE_LOSS_LIMIT"
    utc_day = now.date()
    wib_day = now.astimezone(WIB).date()
    utc_loss = Decimal(0)
    wib_loss = Decimal(0)
    for trade in matching:
        closed = parse_timestamp(str(trade.get("closed_at", trade.get("timestamp_utc"))))
        pnl = Decimal(str(trade.get("net_pnl_usdt", trade.get("realized_pnl_usdt", 0))))
        if closed.date() == utc_day:
            utc_loss += min(pnl, Decimal(0))
        if closed.astimezone(WIB).date() == wib_day:
            wib_loss += min(pnl, Decimal(0))
    if maximum_utc_day_loss_usdt > 0 and utc_loss <= -maximum_utc_day_loss_usdt:
        return "SYMBOL_UTC_DAY_LOSS_LIMIT"
    if maximum_wib_day_loss_usdt > 0 and wib_loss <= -maximum_wib_day_loss_usdt:
        return "SYMBOL_WIB_DAY_LOSS_LIMIT"
    return None


def partial_runner_quantities(
    entry_quantity: Decimal, initial_fraction: Decimal, size_increment: Decimal
) -> tuple[Decimal, Decimal]:
    if (
        entry_quantity <= 0
        or not Decimal(0) < initial_fraction <= Decimal(1)
        or size_increment <= 0
    ):
        raise ValueError("invalid partial-runner inputs")
    initial = (entry_quantity * initial_fraction / size_increment).to_integral_value(
        rounding=ROUND_DOWN
    ) * size_increment
    if initial <= 0:
        raise ValueError("partial exit rounds to zero")
    runner = entry_quantity - initial
    if runner < 0 or initial + runner != entry_quantity:
        raise ValueError("partial exit quantity conservation failed")
    return initial, runner


@dataclass(frozen=True)
class ProtectiveOrder:
    client_order_id: str
    position_id: str
    kind: str
    quantity: Decimal
    trigger_price: Decimal
    reduce_only: bool = True
    state: str = "WORKING"


class PaperProtectiveOrderBook:
    """Deterministic PAPER-only protective-order state with idempotent identifiers."""

    def __init__(self) -> None:
        self._orders: dict[str, ProtectiveOrder] = {}

    def place(self, order: ProtectiveOrder) -> ProtectiveOrder:
        if not order.client_order_id or order.quantity <= 0 or order.trigger_price <= 0:
            raise ValueError("invalid protective order")
        if not order.reduce_only or order.kind not in {"STOP", "TAKE_PROFIT"}:
            raise ValueError("protective orders must be reduce-only stop or take-profit")
        existing = self._orders.get(order.client_order_id)
        if existing is not None and existing != order:
            raise ValueError("client order identifier collision")
        self._orders[order.client_order_id] = order
        return order

    def cancel(self, client_order_id: str) -> ProtectiveOrder:
        order = self._orders[client_order_id]
        if order.state == "CANCELLED":
            return order
        cancelled = ProtectiveOrder(**{**order.__dict__, "state": "CANCELLED"})
        self._orders[client_order_id] = cancelled
        return cancelled

    def reconcile(self, position_id: str, open_quantity: Decimal) -> tuple[ProtectiveOrder, ...]:
        working = tuple(
            order
            for order in self._orders.values()
            if order.position_id == position_id and order.state == "WORKING"
        )
        if open_quantity > 0 and not any(order.kind == "STOP" for order in working):
            raise RuntimeError("FAIL_CLOSED_MISSING_PROTECTIVE_STOP")
        if any(order.quantity > open_quantity for order in working):
            raise RuntimeError("FAIL_CLOSED_PROTECTIVE_QUANTITY_MISMATCH")
        return working
