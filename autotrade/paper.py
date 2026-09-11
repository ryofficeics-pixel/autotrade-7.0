from __future__ import annotations

import json
import logging
import math
import os
import shutil
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from time import monotonic, time_ns
from typing import Any

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import (
    BacktestEngineConfig,
    LoggingConfig,
    RiskEngineConfig,
    StrategyConfig,
)
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.events import OrderFilled, PositionClosed, PositionOpened
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from autotrade.config import Settings
from autotrade.integrity import (
    ACCOUNTING_TOLERANCE,
    CHECKPOINT_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    EXECUTION_MODEL_VERSION,
    MARKET_DATA_MODE,
    STRATEGY_VERSION,
    TRANSITION_PROTOCOL_VERSION,
    EventLedger,
    EventSpec,
    IntegrityError,
    commit_transition,
    create_new_run,
    new_identity,
    reconcile_checkpoint,
    recover_pending_transition,
    scan_ledger,
    utc_now,
)
from autotrade.runtime import RuntimeReport

TAKER_FEE = Decimal("0.0005")
SAFETY_BUFFER_BPS = 3.0
TRADE_HISTORY_HOURS = 48
STATE_SCHEMA_VERSION = CHECKPOINT_SCHEMA_VERSION
MIN_FREE_DISK_BYTES = 1_000_000_000


class QuoteValidationError(ValueError):
    pass


class MomentumConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: Decimal
    window: int
    persistence_ticks: int
    regime_window: int
    entry_threshold_bps: float
    minimum_net_edge_bps: float
    minimum_confidence: float
    stop_loss_bps: float
    take_profit_bps: float
    max_hold_ns: int
    cooldown_ns: int


class RestMomentumStrategy(Strategy):
    """Cost-aware REST-quote momentum challenger for paper execution only."""

    def __init__(self, config: MomentumConfig) -> None:
        super().__init__(config)
        self.entry_enabled = False
        self.status = "WARMING_UP"
        self.expected_gross_bps = 0.0
        self.expected_cost_bps = 0.0
        self.expected_net_bps = 0.0
        self.confidence = 0.0
        self.entry_candidate = False
        self.last_signal: str | None = None
        self.trades = 0
        self.fees_usdt = Decimal(0)
        self.entry_fee_usdt = Decimal(0)
        self._history: deque[float] = deque(maxlen=config.regime_window + 1)
        self._entry_price: float | None = None
        self._entry_quantity = Decimal(0)
        self._entry_notional = Decimal(0)
        self._exit_quantity = Decimal(0)
        self._exit_notional = Decimal(0)
        self._exit_fee_usdt = Decimal(0)
        self._entry_ts: int | None = None
        self._position_side: PositionSide | None = None
        self._exit_reason: str | None = None
        self._last_exit_ts = 0
        self._pending_order = False
        self._flatten_reason: str | None = None
        self._candidate_side: OrderSide | None = None
        self._regime_move_bps = 0.0
        self._prior_regime_move_bps = 0.0
        self._events: list[dict[str, object]] = []

    def on_start(self) -> None:
        self.subscribe_quote_ticks(self.config.instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        bid = tick.bid_price.as_double()
        ask = tick.ask_price.as_double()
        mid = (bid + ask) / 2
        self._history.append(mid)
        self.entry_candidate = False
        self.confidence = 0.0
        self._candidate_side = None

        if self._entry_price is not None and not self._pending_order:
            if self._flatten_reason is not None:
                self._close(self._flatten_reason)
                return
            direction = 1 if self._position_side == PositionSide.LONG else -1
            move_bps = (mid / self._entry_price - 1) * 10_000 * direction
            age_ns = tick.ts_event - (self._entry_ts or tick.ts_event)
            if move_bps <= -self.config.stop_loss_bps:
                self._close("STOP_LOSS")
            elif move_bps >= self.config.take_profit_bps:
                self._close("TAKE_PROFIT")
            elif age_ns >= self.config.max_hold_ns:
                self._close("TIME_EXIT")
            return

        if self._pending_order:
            self.status = "POSITION_OPEN" if self._entry_price is not None else "ORDER_PENDING"
            return
        if tick.ts_event < self._last_exit_ts + self.config.cooldown_ns:
            self.status = "COOLDOWN"
            return
        minimum_history = self.config.regime_window + 1
        if len(self._history) < minimum_history:
            self.status = f"WARMING_UP_{len(self._history)}/{minimum_history}"
            return

        history = tuple(self._history)
        signal_start = history[-self.config.window - 1]
        regime_start = history[0]
        move_bps = (mid / signal_start - 1) * 10_000
        prior_regime_move_bps = (signal_start / regime_start - 1) * 10_000
        regime_move_bps = (mid / regime_start - 1) * 10_000
        self._regime_move_bps = regime_move_bps
        self._prior_regime_move_bps = prior_regime_move_bps
        spread_bps = (ask - bid) / mid * 10_000
        cost_bps = 10.0 + spread_bps + SAFETY_BUFFER_BPS
        self.expected_gross_bps = abs(move_bps)
        self.expected_cost_bps = cost_bps
        self.expected_net_bps = self.expected_gross_bps - cost_bps
        self.confidence = min(
            1.0,
            max(0.0, self.expected_net_bps / max(self.config.entry_threshold_bps, 1.0)),
        )
        hurdle = max(self.config.entry_threshold_bps, cost_bps)
        if (
            abs(move_bps) <= hurdle
            or self.expected_net_bps < self.config.minimum_net_edge_bps
            or self.confidence < self.config.minimum_confidence
        ):
            self.status = "WAITING_EDGE"
            return

        side = OrderSide.BUY if move_bps > 0 else OrderSide.SELL
        direction = 1 if move_bps > 0 else -1
        persistence_prices = history[-self.config.persistence_ticks - 1 :]
        persistence_ok = all(
            (current - previous) * direction > 0
            for previous, current in zip(persistence_prices, persistence_prices[1:], strict=False)
        )
        prior_regime_min_bps = self.config.entry_threshold_bps / 2
        regime_ok = (
            prior_regime_move_bps * direction >= prior_regime_min_bps
            and regime_move_bps * direction >= self.config.entry_threshold_bps
        )
        if not persistence_ok or not regime_ok:
            self.status = "WAITING_CONFIRMATION"
            return

        self.entry_candidate = True
        self._candidate_side = side
        self.status = "ELIGIBLE" if self.entry_enabled else "ELIGIBLE_PAUSED"

    @property
    def has_position(self) -> bool:
        return self._entry_price is not None

    @property
    def has_pending_order(self) -> bool:
        return self._pending_order

    def submit_candidate(self) -> bool:
        side = self._candidate_side
        if (
            not self.entry_enabled
            or not self.entry_candidate
            or side is None
            or self._entry_price is not None
            or self._pending_order
        ):
            return False

        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=Quantity(self.config.trade_size, 6),
            time_in_force=TimeInForce.GTC,
        )
        self._pending_order = True
        self.status = "ENTRY_SUBMITTED"
        self.last_signal = "LONG" if side == OrderSide.BUY else "SHORT"
        self._event(
            "signal",
            direction=self.last_signal,
            gross_edge_bps=round(self.expected_gross_bps, 3),
            cost_bps=round(self.expected_cost_bps, 3),
            net_edge_bps=round(self.expected_net_bps, 3),
            confidence=round(self.confidence, 4),
            persistence_ticks=self.config.persistence_ticks,
            prior_regime_move_bps=round(self._prior_regime_move_bps, 3),
            regime_move_bps=round(self._regime_move_bps, 3),
        )
        self._event(
            "order_submitted",
            side=self.last_signal,
            quantity=str(self.config.trade_size),
            order_type="MARKET",
            client_order_id=str(order.client_order_id),
        )
        self.submit_order(order)
        return True

    def on_order_filled(self, event: OrderFilled) -> None:
        quantity = event.last_qty.as_decimal()
        price = event.last_px.as_decimal()
        fee = event.commission.as_decimal()
        self.fees_usdt += fee
        if self._exit_reason is None:
            self._entry_quantity += quantity
            self._entry_notional += quantity * price
            self.entry_fee_usdt += fee
            if self._entry_price is not None:
                self._entry_price = float(self._entry_notional / self._entry_quantity)
        else:
            self._exit_quantity += quantity
            self._exit_notional += quantity * price
            self._exit_fee_usdt += fee
        self._event(
            "fill",
            side=str(event.order_side),
            quantity=str(quantity),
            price=str(price),
            fee_usdt=str(fee),
            client_order_id=str(event.client_order_id),
            exchange_timestamp=_ns_to_utc(event.ts_event),
        )

    def on_position_opened(self, event: PositionOpened) -> None:
        self._entry_price = (
            float(self._entry_notional / self._entry_quantity)
            if self._entry_quantity
            else event.avg_px_open
        )
        self._entry_ts = event.ts_event
        self._position_side = event.side
        self._pending_order = False
        self.entry_candidate = False
        self.confidence = 0.0
        self._candidate_side = None
        self.status = f"POSITION_{event.side.name}"
        self._event(
            "position_opened",
            side=event.side.name,
            quantity=str(self._entry_quantity),
            price=str(self._entry_price),
            exchange_timestamp=_ns_to_utc(event.ts_event),
        )

    def on_position_closed(self, event: PositionClosed) -> None:
        realized = event.realized_pnl.as_decimal()
        close_price = (
            float(self._exit_notional / self._exit_quantity) if self._exit_quantity else None
        )
        self._event(
            "position_closed",
            side=self._position_side.name if self._position_side is not None else "UNKNOWN",
            quantity=str(self._entry_quantity),
            open_price=str(self._entry_price),
            close_price=str(close_price),
            price=str(close_price),
            realized_pnl_usdt=str(realized),
            pnl_pct=str(realized / self._entry_notional * 100) if self._entry_notional else "0",
            fee_usdt=str(self.entry_fee_usdt + self._exit_fee_usdt),
            reason=self._exit_reason or "ENGINE_CLOSE",
            exchange_timestamp=_ns_to_utc(event.ts_event),
        )
        self.trades += 1
        self._last_exit_ts = event.ts_event
        self._entry_price = None
        self._entry_quantity = Decimal(0)
        self._entry_notional = Decimal(0)
        self.entry_fee_usdt = Decimal(0)
        self._exit_quantity = Decimal(0)
        self._exit_notional = Decimal(0)
        self._exit_fee_usdt = Decimal(0)
        self._entry_ts = None
        self._position_side = None
        self._exit_reason = None
        self._pending_order = False
        self._flatten_reason = None
        self.entry_candidate = False
        self.confidence = 0.0
        self._candidate_side = None
        self.status = "COOLDOWN"

    def on_order_rejected(self, event: object) -> None:
        self._pending_order = False
        self.status = "ORDER_REJECTED"
        self._event("order_rejected", reason=str(event)[:300])

    def on_stop(self) -> None:
        self.unsubscribe_quote_ticks(self.config.instrument_id)

    def request_flatten(self, reason: str = "MANUAL_FLATTEN") -> bool:
        if self._entry_price is None:
            return False
        self._flatten_reason = reason
        return True

    def drain_events(self) -> list[dict[str, object]]:
        events, self._events = self._events, []
        return events

    def _close(self, reason: str) -> None:
        self._pending_order = True
        self.status = f"EXIT_{reason}"
        self._exit_reason = reason
        self._event(
            "exit_signal",
            side=self._position_side.name if self._position_side is not None else "UNKNOWN",
            quantity=str(self._entry_quantity),
            reason=reason,
        )
        self.close_all_positions(self.config.instrument_id, reduce_only=True)

    def _event(self, event_type: str, **values: object) -> None:
        self._events.append(
            {
                "event": event_type,
                "timestamp_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                **values,
            }
        )


@dataclass(frozen=True)
class PaperSnapshot:
    portfolio: dict[str, float | int]
    strategy: dict[str, object]
    orders: int
    positions: int
    risk_halted: bool
    alerts: list[str]
    open_trade: dict[str, object] | None = None
    trade_history: list[dict[str, object]] = field(default_factory=list)
    diagnostics: dict[str, object] = field(default_factory=dict)


class PaperTrader:
    """Owns the long-running Nautilus paper engine; the dashboard only reads it."""

    def __init__(self, report: RuntimeReport, settings: Settings, logger: logging.Logger) -> None:
        self._report = report
        self._settings = settings
        self._logger = logger
        self._engine: BacktestEngine | None = None
        self._engine_verified = False
        self._strategies: dict[str, RestMomentumStrategy] = {}
        self._instruments: dict[str, CryptoPerpetual] = {}
        self._last_ts = 0
        self._last_marks: dict[str, Price] = {}
        self._last_markets: dict[str, dict[str, object]] = {}
        self._last_market_monotonic: dict[str, float] = {}
        self._entry_enabled = False
        self._selected_symbol: str | None = None
        self._risk_halted = False
        self._risk_halt_reason: str | None = None
        self._peak_equity = settings.starting_balance_usdt
        self._project_root = (
            settings.log_directory.parent
            if settings.log_directory.name.lower() == "logs"
            else settings.log_directory
        )
        self._legacy_events_path = settings.log_directory / "paper-events.jsonl"
        self._events_path = self._legacy_events_path
        self._state_path = settings.log_directory / "paper-state.json"
        self._trade_history = self._load_trade_history()
        self._run_id: str | None = None
        self._session_id = new_identity()
        self._ledger: EventLedger | None = None
        self._pending_event_specs: list[EventSpec] = []
        self._metadata: dict[str, object] = {}
        self._checkpoint_sequence = 0
        self._starting_equity = settings.starting_balance_usdt
        self._cumulative_realized_pnl = Decimal(0)
        self._accounting_state = "INVALID"
        self._accounting_error: str | None = None
        self._rollover_review_required = False
        self._lifecycle: dict[str, dict[str, str | None]] = {}
        self._symbol_errors: dict[str, str] = {}
        self._initial_balance = settings.starting_balance_usdt
        self._restored_trades = 0
        self._restored_fees = Decimal(0)
        self._risk_day_utc = datetime.now(UTC).date().isoformat()
        self._day_start_equity = settings.starting_balance_usdt
        self._trades_at_day_start = 0
        self._recovery_position: dict[str, object] | None = None
        self._state_error: str | None = None
        self._load_state()
        if self._run_id is not None:
            self._trade_history = self._load_trade_history()

    @property
    def monitored_symbols(self) -> tuple[str, ...]:
        recovery_position = self._recovery_position
        if recovery_position is not None:
            return (str(recovery_position["symbol"]),)
        return tuple(self._strategies)

    def recheck_integrity(self) -> bool:
        """Re-read durable evidence; never reset a run or clear a live execution fault."""
        if self._state_error is not None and self._ledger is not None:
            return False
        try:
            if not self._state_path.is_file():
                raise IntegrityError("checkpoint is missing; automatic reset is forbidden")
            if self._engine is None:
                self._load_state()
            else:
                state = json.loads(self._state_path.read_text(encoding="utf-8"))
                summary = scan_ledger(self._events_path, str(self._run_id))
                if summary.legacy_count or self._ledger is None:
                    raise IntegrityError("current run has no attributable ledger")
                if summary.last_sequence != self._ledger.last_sequence:
                    raise IntegrityError("durable ledger changed outside the running engine")
                reconcile_checkpoint(state, summary)
                if self._state_error is None:
                    self._persist_state()
        except (OSError, ValueError, TypeError, KeyError, InvalidOperation) as exc:
            self._set_state_invalid(str(exc))
        self._logger.info(
            "Accounting recheck: %s; reason=%s", self._accounting_state, self._accounting_error
        )
        return self._accounting_state == "VALID" and self._state_error is None

    def process(self, markets: list[dict[str, object]], *, entry_enabled: bool) -> None:
        current_markets = {
            str(item["symbol"]): item for item in markets if isinstance(item.get("symbol"), str)
        }
        self._last_markets.update(current_markets)
        received = monotonic()
        self._last_market_monotonic.update(dict.fromkeys(current_markets, received))
        if (
            self._recovery_position is not None
            or self._state_error is not None
            or self._accounting_state != "VALID"
        ):
            return
        if self._engine is None:
            selected = [market for market in markets if bool(market.get("selected"))]
            if selected:
                self._initialize(selected[: self._settings.active_symbols])
        if self._engine is None or not self._strategies:
            return

        if shutil.disk_usage(self._settings.log_directory.parent).free < MIN_FREE_DISK_BYTES:
            self._risk_halted = True
            self._risk_halt_reason = "STORAGE_LOW"

        self._entry_enabled = (
            entry_enabled
            and self._settings.strategy_enabled
            and not self._risk_halted
            and self._accounting_state == "VALID"
        )
        quotes: list[QuoteTick] = []
        for symbol, strategy in self._strategies.items():
            market = current_markets.get(symbol)
            strategy.entry_enabled = bool(
                self._entry_enabled and market is not None and market.get("selected")
            )
            if market is None:
                strategy.status = "WAITING_MARKET"
                continue
            try:
                quote = self._quote(symbol, market)
            except (QuoteValidationError, InvalidOperation, ValueError) as exc:
                strategy.entry_enabled = False
                strategy.status = "QUARANTINED"
                self._symbol_errors[symbol] = str(exc)[:160]
                continue
            self._symbol_errors.pop(symbol, None)
            quotes.append(quote)
        if not quotes:
            if self._engine_verified:
                self._persist_state()
            return
        try:
            self._engine.add_data(quotes)
            self._engine.run(streaming=True)
            self._engine.clear_data()
            if not self._engine_verified:
                self._verify_engine_accounting()
                self._engine_verified = True

            for symbol, strategy in self._strategies.items():
                self._write_events(strategy.drain_events(), symbol)

            positions = self._engine.cache.positions_open()
            if len(positions) > 1:
                self._risk_halted = True
                self._risk_halt_reason = "POSITION_INVARIANT"
                raise RuntimeError("paper portfolio invariant failed: more than one open position")
            if not positions and not any(
                strategy.has_pending_order for strategy in self._strategies.values()
            ):
                candidates = [
                    (symbol, strategy)
                    for symbol, strategy in self._strategies.items()
                    if strategy.entry_candidate and strategy.entry_enabled
                ]
                if candidates:
                    symbol, winner = max(
                        candidates,
                        key=lambda item: (
                            item[1].confidence,
                            item[1].expected_net_bps,
                            item[0],
                        ),
                    )
                    self._selected_symbol = symbol
                    if winner.submit_candidate():
                        self._write_events(winner.drain_events(), symbol)
                        self._engine.add_data([self._quote(symbol, current_markets[symbol])])
                        self._engine.run(streaming=True)
                        self._engine.clear_data()
                        self._write_events(winner.drain_events(), symbol)

            self._apply_risk_limits()
            self._persist_state()
        except Exception:
            self._risk_halted = True
            self._risk_halt_reason = self._risk_halt_reason or "EXECUTION_INTEGRITY"
            self._entry_enabled = False
            for symbol, strategy in self._strategies.items():
                self._write_events(strategy.drain_events(), symbol)
            self._persist_state()
            raise

    def set_entry_enabled(self, enabled: bool) -> None:
        self._entry_enabled = (
            enabled and not self._risk_halted and self._accounting_state == "VALID"
        )
        for symbol, strategy in self._strategies.items():
            market = self._last_markets.get(symbol)
            strategy.entry_enabled = bool(
                self._entry_enabled and market is not None and market.get("selected")
            )

    def authorize_resume(self) -> bool:
        if self._accounting_state != "VALID" or self._recovery_position is not None:
            return False
        if self._rollover_review_required and self._risk_halt_reason == "DAILY_LOSS_REVIEW":
            self._rollover_review_required = False
            self._risk_halted = False
            self._risk_halt_reason = None
            self._persist_state()
        return not self._risk_halted

    def flatten(self) -> bool:
        if self._state_error is not None or self._accounting_state != "VALID":
            return False
        if self._recovery_position is not None:
            self._flatten_recovery()
            return True
        return any(strategy.request_flatten() for strategy in self._strategies.values())

    def snapshot(self) -> PaperSnapshot:
        if self._state_error is not None:
            return self._inactive_snapshot(
                status="STATE_INVALID",
                positions=0,
                alert=f"Persistent paper state is invalid: {self._state_error}",
            )
        if self._recovery_position is not None:
            return self._inactive_snapshot(
                status="RECOVERY_REQUIRED",
                positions=1,
                alert="A persisted paper position requires manual FLATTEN before resume.",
            )
        if self._engine is None or not self._strategies:
            return self._inactive_snapshot(
                status="WAITING_MARKET",
                positions=0,
                alert="Waiting for the first screened Gate market universe.",
            )

        account = self._engine.cache.account_for_venue(Venue(self._settings.venue))
        balance_money = account.balance_total(Currency.from_str("USDT")) if account else None
        balance = balance_money.as_decimal() if balance_money else self._initial_balance
        positions = self._engine.cache.positions_open()
        unrealized = Decimal(0)
        for position in positions:
            symbol = self._symbol_for_position(position)
            mark = self._last_marks.get(symbol)
            if mark is not None:
                unrealized += position.unrealized_pnl(mark).as_decimal()
        equity = balance + unrealized
        trades = self._restored_trades + sum(
            strategy.trades for strategy in self._strategies.values()
        )
        fees = self._restored_fees + sum(
            (strategy.fees_usdt for strategy in self._strategies.values()), Decimal(0)
        )
        candidates = self._candidate_snapshots()
        active_symbol = (
            self._symbol_for_position(positions[0])
            if positions
            else self._selected_symbol
            if self._selected_symbol in self._strategies
            else max(
                self._strategies,
                key=lambda symbol: (
                    self._strategies[symbol].confidence,
                    self._strategies[symbol].expected_net_bps,
                    symbol,
                ),
            )
        )
        active_strategy = self._strategies[active_symbol]
        armed = self._entry_enabled and self._settings.strategy_enabled and not self._risk_halted
        alerts = []
        if self._risk_halted:
            reason = self._risk_halt_reason or "UNKNOWN"
            alerts.append(f"Paper risk limit reached ({reason}); new entries are halted.")
        elif not armed:
            alerts.append("REST Momentum tournament is paused.")
        elif any(
            strategy.status.startswith("WARMING_UP") for strategy in self._strategies.values()
        ):
            alerts.append(
                "REST Momentum is monitoring all screened pairs and warming quote windows."
            )
        elif any(
            strategy.status == "WAITING_CONFIRMATION" for strategy in self._strategies.values()
        ):
            alerts.append("REST Momentum is waiting for persistent trend and regime confirmation.")
        elif not positions:
            alerts.append(
                "All screened pairs are monitored; one highest-confidence eligible pair may trade."
            )
        return PaperSnapshot(
            portfolio=self._portfolio(
                equity,
                fees,
                trades,
                len(positions),
            ),
            strategy={
                "name": "REST Momentum Tournament",
                "symbol": active_symbol,
                "status": active_strategy.status if armed or positions else "PAUSED",
                "armed": armed,
                "confidence": round(active_strategy.confidence, 4),
                "expected_gross_bps": round(active_strategy.expected_gross_bps, 3),
                "expected_cost_bps": round(active_strategy.expected_cost_bps, 3),
                "expected_net_bps": round(active_strategy.expected_net_bps, 3),
                "last_signal": active_strategy.last_signal,
                "monitored_symbols": len(self._strategies),
                "execution_slots": 1,
                "candidates": candidates,
            },
            orders=self._engine.cache.orders_total_count(),
            positions=len(positions),
            risk_halted=self._risk_halted,
            alerts=alerts,
            open_trade=self._open_trade(positions[0] if positions else None),
            trade_history=self._recent_trade_history(),
            diagnostics=self._diagnostics(equity, trades, fees),
        )

    def close(self) -> None:
        if self._engine is not None:
            self._engine.end()
            self._engine.dispose()
            self._engine = None
            self._engine_verified = False

    def _initialize(self, markets: list[dict[str, object]]) -> None:
        self._settings.log_directory.mkdir(parents=True, exist_ok=True)
        logging_config = LoggingConfig(
            log_level=self._settings.log_console_level,
            log_level_file=self._settings.log_file_level,
            log_directory=str(self._settings.log_directory),
            log_file_name="paper-engine",
            log_file_format="json",
            log_file_max_size=self._settings.log_file_max_size,
            log_file_max_backup_count=self._settings.log_file_max_backup_count,
        )
        engine = BacktestEngine(
            BacktestEngineConfig(
                trader_id=TraderId(self._settings.trader_id),
                logging=logging_config,
                risk_engine=RiskEngineConfig(bypass=False),
                run_analysis=False,
            )
        )
        venue = Venue(self._settings.venue)
        usdt = Currency.from_str("USDT")
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=usdt,
            starting_balances=[Money(self._initial_balance, usdt)],
            default_leverage=self._settings.leverage,
        )
        initialized: list[str] = []
        for market in markets:
            raw_symbol = str(market["symbol"])
            if raw_symbol in self._strategies:
                continue
            try:
                price_increment = _state_decimal(
                    market.get("price_increment"), f"{raw_symbol}.price_increment"
                )
                precision = _decimal_places(price_increment)
                if price_increment <= 0 or precision > 16:
                    raise QuoteValidationError(f"invalid Gate price increment for {raw_symbol}")
                market_ask = _state_decimal(market.get("ask"), f"{raw_symbol}.ask")
                trade_size = (self._settings.strategy_notional_usdt / market_ask).quantize(
                    Decimal("0.000001"), rounding=ROUND_DOWN
                )
                if trade_size <= 0:
                    raise QuoteValidationError(
                        f"paper trade quantity rounded to zero for {raw_symbol}"
                    )
            except (InvalidOperation, ValueError, QuoteValidationError) as exc:
                self._symbol_errors[raw_symbol] = f"PRECISION_REJECTED: {exc}"[:160]
                self._logger.warning("Paper symbol rejected %s: %s", raw_symbol, exc)
                continue
            base = Currency.from_str(raw_symbol.removesuffix("_USDT"))
            timestamp = time_ns()
            instrument_id = InstrumentId(Symbol(f"{raw_symbol}-PERP"), venue)
            instrument = CryptoPerpetual(
                instrument_id=instrument_id,
                raw_symbol=Symbol(raw_symbol),
                base_currency=base,
                quote_currency=usdt,
                settlement_currency=usdt,
                is_inverse=False,
                price_precision=precision,
                size_precision=6,
                price_increment=Price.from_str(format(price_increment, "f")),
                size_increment=Quantity.from_str("0.000001"),
                ts_event=timestamp,
                ts_init=timestamp,
                margin_init=Decimal("1"),
                margin_maint=Decimal("1"),
                maker_fee=Decimal("0.0002"),
                taker_fee=TAKER_FEE,
            )
            strategy = RestMomentumStrategy(
                MomentumConfig(
                    instrument_id=instrument_id,
                    trade_size=trade_size,
                    window=self._settings.strategy_window,
                    entry_threshold_bps=float(self._settings.strategy_entry_threshold_bps),
                    minimum_net_edge_bps=float(self._settings.strategy_minimum_net_edge_bps),
                    minimum_confidence=float(self._settings.strategy_minimum_confidence),
                    stop_loss_bps=float(self._settings.strategy_stop_loss_bps),
                    take_profit_bps=float(self._settings.strategy_take_profit_bps),
                    persistence_ticks=self._settings.strategy_persistence_ticks,
                    regime_window=self._settings.strategy_regime_window,
                    max_hold_ns=self._settings.strategy_max_hold_seconds * 1_000_000_000,
                    cooldown_ns=self._settings.strategy_cooldown_seconds * 1_000_000_000,
                )
            )
            engine.add_instrument(instrument)
            engine.add_strategy(strategy)
            self._strategies[raw_symbol] = strategy
            self._instruments[raw_symbol] = instrument
            initialized.append(raw_symbol)
        if not initialized:
            raise RuntimeError("paper universe has no valid instruments")
        self._engine = engine
        self._logger.info(
            "Paper tournament initialized symbols=%s execution_slots=1 notional=%s leverage=%s",
            ",".join(initialized),
            self._settings.strategy_notional_usdt,
            self._settings.leverage,
        )

    def _verify_engine_accounting(self) -> None:
        if self._engine is None:
            raise RuntimeError("paper engine initialization failed")
        account = self._engine.cache.account_for_venue(Venue(self._settings.venue))
        balance_money = account.balance_total(Currency.from_str("USDT")) if account else None
        if balance_money is None:
            raise RuntimeError("initialized paper account has no USDT balance")
        engine_balance = balance_money.as_decimal()
        if abs(engine_balance - self._initial_balance) > ACCOUNTING_TOLERANCE:
            reason = "checkpoint/Nautilus opening balance mismatch"
            self._emit_reconciliation_failure(reason, engine_balance, self._initial_balance)
            self._set_state_invalid(reason)
            raise RuntimeError(reason)

    def _emit_reconciliation_failure(
        self,
        reason: str,
        engine_equity: Decimal,
        ledger_equity: Decimal,
    ) -> None:
        if self._ledger is None:
            self._logger.error("Paper reconciliation failed: %s", reason)
            return
        self._pending_event_specs.append(
            (
                "state_reconciliation_failed",
                {
                    "reason": reason,
                    "checkpoint_equity": str(self._initial_balance),
                    "engine_equity": str(engine_equity),
                    "ledger_equity": str(ledger_equity),
                    "difference": str(engine_equity - ledger_equity),
                },
            )
        )

    def _quote(self, symbol: str, market: dict[str, object]) -> QuoteTick:
        instrument = self._instruments[symbol]
        raw_bid = _state_decimal(market.get("bid"), f"{symbol}.bid")
        raw_ask = _state_decimal(market.get("ask"), f"{symbol}.ask")
        increment = instrument.price_increment.as_decimal()
        bid, ask = construct_pessimistic_prices(
            raw_bid,
            raw_ask,
            increment,
            self._settings.strategy_slippage_bps,
        )
        precision = instrument.price_precision
        bid_price = Price.from_str(f"{bid:.{precision}f}")
        ask_price = Price.from_str(f"{ask:.{precision}f}")
        timestamp = max(time_ns(), self._last_ts + 1)
        self._last_ts = timestamp
        mark = ((bid + ask) / 2 / increment).to_integral_value(rounding=ROUND_FLOOR) * increment
        self._last_marks[symbol] = Price.from_str(f"{mark:.{precision}f}")
        return QuoteTick(
            instrument_id=instrument.id,
            bid_price=bid_price,
            ask_price=ask_price,
            bid_size=Quantity.from_str("100.000000"),
            ask_size=Quantity.from_str("100.000000"),
            ts_event=timestamp,
            ts_init=timestamp,
        )

    def _candidate_snapshots(self) -> list[dict[str, object]]:
        return [
            {
                "symbol": symbol,
                "status": strategy.status,
                "selected": bool(self._last_markets.get(symbol, {}).get("selected")),
                "eligible": strategy.entry_candidate,
                "confidence": round(strategy.confidence, 4),
                "expected_gross_bps": round(strategy.expected_gross_bps, 3),
                "expected_cost_bps": round(strategy.expected_cost_bps, 3),
                "expected_net_bps": round(strategy.expected_net_bps, 3),
                "last_signal": strategy.last_signal,
            }
            for symbol, strategy in sorted(
                self._strategies.items(),
                key=lambda item: (
                    item[1].confidence,
                    item[1].expected_net_bps,
                    item[0],
                ),
                reverse=True,
            )
        ]

    def _symbol_for_position(self, position: object) -> str:
        instrument_id = position.instrument_id  # type: ignore[attr-defined]
        for symbol, instrument in self._instruments.items():
            if instrument.id == instrument_id:
                return symbol
        raise RuntimeError(f"paper position instrument is not monitored: {instrument_id}")

    def _apply_risk_limits(self) -> None:
        _, equity, trades, _, _ = self._accounting_totals()
        self._roll_risk_day(equity, trades)
        pnl = equity - self._day_start_equity
        drawdown = (
            (self._peak_equity - equity) / self._peak_equity * 100
            if self._peak_equity
            else Decimal(0)
        )
        if pnl <= -self._settings.strategy_daily_loss_usdt:
            self._risk_halted = True
            self._risk_halt_reason = self._risk_halt_reason or "DAILY_LOSS"
        if drawdown >= self._settings.strategy_max_drawdown_pct:
            self._risk_halted = True
            self._risk_halt_reason = self._risk_halt_reason or "MAX_DRAWDOWN"
        if self._risk_halted:
            self._entry_enabled = False
            for strategy in self._strategies.values():
                strategy.entry_enabled = False
                if strategy.has_position:
                    strategy.request_flatten("RISK_FLATTEN")

    def _roll_risk_day(self, equity: Decimal, trades: int) -> None:
        today = datetime.now(UTC).date().isoformat()
        if today == self._risk_day_utc:
            self._peak_equity = max(self._peak_equity, equity)
            return
        self._persist_state(rollover=False)
        prior_daily_halt = self._risk_halted and self._risk_halt_reason == "DAILY_LOSS"
        self._risk_day_utc = today
        self._day_start_equity = equity
        self._trades_at_day_start = trades
        self._peak_equity = max(self._peak_equity, equity)
        if prior_daily_halt:
            self._risk_halted = True
            self._risk_halt_reason = "DAILY_LOSS_REVIEW"
            self._rollover_review_required = True
        self._persist_state(rollover=False)

    def _inactive_snapshot(self, *, status: str, positions: int, alert: str) -> PaperSnapshot:
        alerts = [alert]
        if self._risk_halted:
            reason = self._risk_halt_reason or "UNKNOWN"
            alerts.insert(0, f"Paper risk limit reached ({reason}); new entries are halted.")
        return PaperSnapshot(
            portfolio=self._portfolio(
                self._initial_balance,
                self._restored_fees,
                self._restored_trades,
                positions,
            ),
            strategy={
                "name": "REST Momentum Tournament",
                "symbol": str(
                    (self._recovery_position or {}).get("symbol", self._settings.strategy_symbol)
                ),
                "status": status,
                "armed": False,
                "confidence": 0.0,
                "expected_gross_bps": 0.0,
                "expected_cost_bps": 0.0,
                "expected_net_bps": 0.0,
                "last_signal": None,
                "monitored_symbols": len(self._strategies),
                "execution_slots": 1,
                "candidates": self._candidate_snapshots(),
            },
            orders=0,
            positions=positions,
            risk_halted=self._risk_halted or self._state_error is not None,
            alerts=alerts,
            open_trade=self._open_trade(None),
            trade_history=self._recent_trade_history(),
            diagnostics=self._diagnostics(
                self._initial_balance,
                self._restored_trades,
                self._restored_fees,
            ),
        )

    def _diagnostics(
        self,
        equity: Decimal,
        trades: int,
        fees: Decimal,
    ) -> dict[str, object]:
        usage = shutil.disk_usage(self._settings.log_directory.parent)
        free_percent = usage.free / usage.total * 100 if usage.total else 0.0
        last_event_sequence = self._ledger.last_sequence if self._ledger is not None else None
        return {
            "accounting": {
                "state": self._accounting_state,
                "reason": self._accounting_error,
                "equity_usdt": str(equity),
                "starting_equity_usdt": str(self._starting_equity),
                "cumulative_realized_pnl_usdt": str(self._cumulative_realized_pnl),
                "fees_usdt": str(fees),
                "trade_count": trades,
                "checkpoint_sequence": self._checkpoint_sequence,
                "last_event_sequence": last_event_sequence,
                "tolerance_usdt": str(ACCOUNTING_TOLERANCE),
            },
            "run": {
                **self._metadata,
                "run_id": self._run_id,
                "session_id": self._session_id,
                "event_schema_version": EVENT_SCHEMA_VERSION,
            },
            "risk": {
                "state": self._risk_halt_reason or "OK",
                "risk_day_utc": self._risk_day_utc,
                "day_start_equity_usdt": str(self._day_start_equity),
                "rollover_review_required": self._rollover_review_required,
            },
            "execution_model": {
                "state": "PAPER_SIM",
                "version": EXECUTION_MODEL_VERSION,
                "market_data_mode": MARKET_DATA_MODE,
                "spread": "OBSERVED",
                "fees": "MODELED",
                "slippage": "MODELED",
                "decision_latency": "NOT_MODELED",
                "submit_latency": "NOT_MODELED",
                "fill_latency": "NOT_MODELED",
                "partial_fills": "OBSERVED",
                "fill_ratio": "NOT_MODELED",
                "adverse_selection": "NOT_MODELED",
                "funding": "NOT_MODELED",
                "queue_model": "NOT_MODELED",
            },
            "symbols": {
                "quarantined": dict(sorted(self._symbol_errors.items())),
            },
            "storage": {
                "free_disk_bytes": usage.free,
                "free_disk_percent": round(free_percent, 2),
                "safe": usage.free >= MIN_FREE_DISK_BYTES,
            },
        }

    def _load_state(self) -> None:
        if not self._state_path.exists():
            has_legacy = (
                self._legacy_events_path.is_file() and self._legacy_events_path.stat().st_size > 0
            )
            has_prior_run = any((self._project_root / "data" / "runs").glob("*/metadata.json"))
            if has_legacy or has_prior_run:
                self._set_state_invalid(
                    "checkpoint is missing while prior paper-run evidence exists"
                )
                return
            try:
                create_new_run(
                    project_root=self._project_root,
                    config_path=Path(__file__).resolve().parents[1] / "config" / "paper.toml",
                    log_directory=self._settings.log_directory,
                    starting_equity=self._settings.starting_balance_usdt,
                )
            except (OSError, ValueError) as exc:
                self._set_state_invalid(f"fresh paper run creation failed: {exc}")
                return
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("state must be an object")
            schema_version = int(state.get("schema_version", 1))
            if schema_version not in {1, 2, STATE_SCHEMA_VERSION}:
                raise ValueError("unsupported state schema")
            if state.get("mode") != "PAPER":
                raise ValueError("mode mismatch")
            if schema_version == STATE_SCHEMA_VERSION:
                run_id = str(state.get("run_id", ""))
                ledger_path = (
                    self._project_root / Path(str(state.get("event_ledger_path", "")))
                ).resolve()
                if not ledger_path.is_relative_to(self._project_root.resolve()):
                    raise IntegrityError("event ledger path escapes the project root")
                recovered = recover_pending_transition(self._state_path, ledger_path, run_id)
                if recovered is not None:
                    state = recovered
                    self._logger.warning(
                        "Recovered committed paper transition %s",
                        recovered.get("last_transition_id"),
                    )
            self._initial_balance = _state_decimal(state["balance_usdt"], "balance_usdt")
            if self._initial_balance < 0:
                raise ValueError("balance_usdt must be non-negative")
            self._restored_trades = int(state.get("trades", 0))
            if self._restored_trades < 0:
                raise ValueError("trades must be non-negative")
            self._restored_fees = _state_decimal(state.get("fees_usdt", 0), "fees_usdt")
            if self._restored_fees < 0:
                raise ValueError("fees_usdt must be non-negative")
            self._starting_equity = _state_decimal(
                state.get("starting_equity_usdt", self._settings.starting_balance_usdt),
                "starting_equity_usdt",
            )
            self._cumulative_realized_pnl = _state_decimal(
                state.get(
                    "cumulative_realized_pnl_usdt",
                    self._initial_balance - self._starting_equity,
                ),
                "cumulative_realized_pnl_usdt",
            )
            self._load_risk_state(state)
            position = state.get("position")
            if position is not None and not isinstance(position, dict):
                raise ValueError("position must be an object")
            if position is not None:
                position.setdefault("symbol", state.get("symbol"))
                symbol = str(position.get("symbol", ""))
                if not symbol.endswith("_USDT"):
                    raise ValueError("position symbol is invalid")
                if str(position.get("side")) not in {"LONG", "SHORT"}:
                    raise ValueError("position side is invalid")
                if _state_decimal(position.get("quantity"), "position.quantity") <= 0:
                    raise ValueError("position quantity must be positive")
                if _state_decimal(position.get("entry_price"), "position.entry_price") <= 0:
                    raise ValueError("position entry_price must be positive")
                if _state_decimal(position.get("entry_fee_usdt", 0), "position.entry_fee_usdt") < 0:
                    raise ValueError("position entry_fee_usdt must be non-negative")
            self._recovery_position = position
            if position is not None:
                self._risk_halted = True
                self._risk_halt_reason = self._risk_halt_reason or "RECOVERY_REQUIRED"
            self._restore_risk_halt_for_current_window()
            if schema_version != STATE_SCHEMA_VERSION:
                raise IntegrityError(
                    f"checkpoint schema v{schema_version} is legacy/unattributed; "
                    "use new-paper-run after archiving"
                )
            self._load_v3_integrity(state)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            InvalidOperation,
            OverflowError,
            json.JSONDecodeError,
        ) as exc:
            self._set_state_invalid(str(exc))

    def _load_v3_integrity(self, state: dict[str, object]) -> None:
        run_id = str(state.get("run_id", ""))
        ledger_relative = Path(str(state.get("event_ledger_path", "")))
        ledger_path = (self._project_root / ledger_relative).resolve()
        project_root = self._project_root.resolve()
        if not ledger_path.is_relative_to(project_root):
            raise IntegrityError("event ledger path escapes the project root")
        metadata_path = project_root / "data" / "runs" / run_id / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"run metadata is unavailable: {exc}") from exc
        if not isinstance(metadata, dict) or metadata.get("run_id") != run_id:
            raise IntegrityError("run metadata identity mismatch")
        if metadata.get("event_schema_version") != EVENT_SCHEMA_VERSION:
            raise IntegrityError("run metadata event schema mismatch")
        self._run_id = run_id
        self._events_path = ledger_path
        self._metadata = metadata
        self._checkpoint_sequence = int(str(state.get("checkpoint_sequence", -1)))
        if self._checkpoint_sequence < 0:
            raise IntegrityError("checkpoint_sequence must be non-negative")
        summary = scan_ledger(ledger_path, run_id)
        if summary.legacy_count:
            raise IntegrityError("current run ledger contains legacy/unattributed events")
        reconcile_checkpoint(state, summary)
        self._ledger = EventLedger(ledger_path, run_id, self._session_id)
        self._accounting_state = "VALID"
        self._accounting_error = None
        self._state_error = None
        self._rollover_review_required = bool(state.get("rollover_review_required", False))
        if self._recovery_position is not None:
            position_id = self._recovery_position.get("position_id")
            if position_id is not None:
                self._lifecycle[str(self._recovery_position["symbol"])] = {
                    "signal_id": str(self._recovery_position.get("signal_id") or "") or None,
                    "position_id": str(position_id),
                    "entry_order_id": None,
                    "exit_order_id": None,
                    "entry_client_order_id": None,
                    "exit_client_order_id": None,
                    "closing": None,
                    "position_open_timestamp": str(
                        self._recovery_position.get("position_open_timestamp") or ""
                    )
                    or None,
                }

    def _set_state_invalid(self, reason: str) -> None:
        self._state_error = reason[:200]
        self._accounting_state = "INVALID"
        self._accounting_error = self._state_error
        self._entry_enabled = False
        self._risk_halted = True
        self._risk_halt_reason = "STATE_INVALID"
        self.set_entry_enabled(False)

    def _load_risk_state(self, state: dict[str, object]) -> None:
        today = datetime.now(UTC).date().isoformat()
        risk_day = state.get("risk_day_utc")
        if isinstance(risk_day, str):
            datetime.strptime(risk_day, "%Y-%m-%d")
            self._risk_day_utc = risk_day
            self._day_start_equity = _state_decimal(
                state.get("day_start_equity_usdt"), "day_start_equity_usdt"
            )
            self._trades_at_day_start = int(str(state.get("trades_at_day_start", 0)))
        else:
            today_trades = [
                trade
                for trade in self._trade_history
                if str(trade.get("closed_at", "")).startswith(today)
            ]
            today_pnl = sum(
                (Decimal(str(trade["realized_pnl_usdt"])) for trade in today_trades),
                Decimal(0),
            )
            self._risk_day_utc = today
            self._day_start_equity = self._initial_balance - today_pnl
            self._trades_at_day_start = max(0, self._restored_trades - len(today_trades))
        if self._day_start_equity < 0 or self._trades_at_day_start < 0:
            raise ValueError("daily risk window is invalid")
        self._peak_equity = max(
            self._settings.starting_balance_usdt,
            self._initial_balance,
            _state_decimal(
                state.get("peak_equity_usdt", self._initial_balance),
                "peak_equity_usdt",
            ),
        )
        risk_halted = state.get("risk_halted", False)
        if not isinstance(risk_halted, bool):
            raise ValueError("risk_halted must be boolean")
        reason = state.get("risk_halt_reason")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 64):
            raise ValueError("risk_halt_reason is invalid")
        self._risk_halted = risk_halted
        self._risk_halt_reason = reason
        if (
            self._risk_halted
            and self._risk_halt_reason == "DAILY_LOSS"
            and self._risk_day_utc < today
        ):
            self._rollover_review_required = True

    def _restore_risk_halt_for_current_window(self) -> None:
        daily_pnl = self._initial_balance - self._day_start_equity
        drawdown = (
            (self._peak_equity - self._initial_balance) / self._peak_equity * 100
            if self._peak_equity
            else Decimal(0)
        )
        if daily_pnl <= -self._settings.strategy_daily_loss_usdt:
            self._risk_halted = True
            self._risk_halt_reason = self._risk_halt_reason or "DAILY_LOSS"
        if drawdown >= self._settings.strategy_max_drawdown_pct:
            self._risk_halted = True
            self._risk_halt_reason = self._risk_halt_reason or "MAX_DRAWDOWN"

    def _persist_state(self, *, rollover: bool = True) -> None:
        if self._engine is None or not self._strategies:
            return
        balance, equity, trades, fees, positions = self._accounting_totals()
        if rollover and datetime.now(UTC).date().isoformat() != self._risk_day_utc:
            self._roll_risk_day(equity, trades)
            return
        if len(positions) > 1:
            raise RuntimeError("cannot persist more than one paper position")
        position = None
        if positions:
            current = positions[0]
            symbol = self._symbol_for_position(current)
            strategy = self._strategies[symbol]
            position = {
                "symbol": symbol,
                "side": current.side.name,
                "quantity": str(current.quantity.as_decimal()),
                "entry_price": str(current.avg_px_open),
                "entry_fee_usdt": str(strategy.entry_fee_usdt),
                "position_id": self._lifecycle.get(symbol, {}).get("position_id"),
                "signal_id": self._lifecycle.get(symbol, {}).get("signal_id"),
                "position_open_timestamp": self._lifecycle.get(symbol, {}).get(
                    "position_open_timestamp"
                ),
            }
        if self._ledger is None or self._run_id is None:
            self._set_state_invalid("current paper run has no v3 event ledger")
            raise RuntimeError(self._state_error)
        open_entry_fee = (
            _state_decimal(position["entry_fee_usdt"], "position.entry_fee_usdt")
            if position is not None
            else Decimal(0)
        )
        self._cumulative_realized_pnl = balance - self._starting_equity + open_entry_fee
        self._checkpoint_sequence += 1
        state = {
            "schema_version": STATE_SCHEMA_VERSION,
            "checkpoint_sequence": self._checkpoint_sequence,
            "last_event_sequence": self._ledger.last_sequence,
            "last_committed_event_sequence": self._ledger.last_sequence,
            "transition_protocol_version": TRANSITION_PROTOCOL_VERSION,
            "run_id": self._run_id,
            "session_id": self._session_id,
            "event_ledger_path": str(self._events_path.relative_to(self._project_root)),
            "mode": "PAPER",
            "symbol": "MULTI",
            "starting_equity_usdt": str(self._starting_equity),
            "balance_usdt": str(balance),
            "cumulative_realized_pnl_usdt": str(self._cumulative_realized_pnl),
            "trades": trades,
            "fees_usdt": str(fees),
            "position": position,
            "risk_day_utc": self._risk_day_utc,
            "day_start_equity_usdt": str(self._day_start_equity),
            "trades_at_day_start": self._trades_at_day_start,
            "peak_equity_usdt": str(self._peak_equity),
            "risk_halted": self._risk_halted,
            "risk_halt_reason": self._risk_halt_reason,
            "rollover_review_required": self._rollover_review_required,
            "updated_at_utc": utc_now(),
            "updated_ns": time_ns(),
        }
        self._commit_checkpoint(state)

    def _accounting_totals(
        self,
    ) -> tuple[Decimal, Decimal, int, Decimal, list[Any]]:
        if self._engine is None:
            raise RuntimeError("paper engine is unavailable")
        account = self._engine.cache.account_for_venue(Venue(self._settings.venue))
        balance_money = account.balance_total(Currency.from_str("USDT")) if account else None
        if balance_money is None:
            raise RuntimeError("paper account balance is unavailable")
        balance = balance_money.as_decimal()
        positions = list(self._engine.cache.positions_open())
        unrealized = Decimal(0)
        for current in positions:
            symbol = self._symbol_for_position(current)
            mark = self._last_marks.get(symbol)
            if mark is not None:
                unrealized += current.unrealized_pnl(mark).as_decimal()
        trades = self._restored_trades + sum(
            strategy.trades for strategy in self._strategies.values()
        )
        fees = self._restored_fees + sum(
            (strategy.fees_usdt for strategy in self._strategies.values()), Decimal(0)
        )
        return balance, balance + unrealized, trades, fees, positions

    def _flatten_recovery(self) -> None:
        if self._recovery_position is None:
            raise RuntimeError("no recovery position exists")
        symbol = str(self._recovery_position.get("symbol", ""))
        market = self._last_markets.get(symbol)
        received = self._last_market_monotonic.get(symbol)
        if (
            market is None
            or received is None
            or monotonic() - received > self._settings.market_stale_after_seconds
        ):
            raise RuntimeError("recovery position has no fresh market")
        side = str(self._recovery_position.get("side"))
        quantity = Decimal(str(self._recovery_position["quantity"]))
        entry = Decimal(str(self._recovery_position["entry_price"]))
        entry_fee = Decimal(str(self._recovery_position.get("entry_fee_usdt", 0)))
        slippage = self._settings.strategy_slippage_bps / 10_000
        if side == "LONG":
            exit_price = Decimal(str(market["bid"])) * (1 - slippage)
            gross = (exit_price - entry) * quantity
        elif side == "SHORT":
            exit_price = Decimal(str(market["ask"])) * (1 + slippage)
            gross = (entry - exit_price) * quantity
        else:
            raise RuntimeError("recovery position side is invalid")
        exit_fee = exit_price * quantity * TAKER_FEE
        self._initial_balance += gross - exit_fee
        self._restored_fees += exit_fee
        self._restored_trades += 1
        trade_pnl = gross - entry_fee - exit_fee
        entry_notional = entry * quantity
        self._write_events(
            [
                {
                    "event": "recovery_flatten",
                    "side": side,
                    "quantity": str(quantity),
                    "price": str(exit_price),
                    "open_price": str(entry),
                    "close_price": str(exit_price),
                    "realized_pnl_usdt": str(trade_pnl),
                    "pnl_pct": str(trade_pnl / entry_notional * 100),
                    "fee_usdt": str(entry_fee + exit_fee),
                    "ledger_fee_usdt": str(exit_fee),
                    "total_fee_usdt": str(entry_fee + exit_fee),
                    "reason": "RECOVERY_FLATTEN",
                    "position_id": self._recovery_position.get("position_id"),
                    "signal_id": self._recovery_position.get("signal_id"),
                    "recovery_identity_status": (
                        "KNOWN"
                        if self._recovery_position.get("position_id") is not None
                        else "UNKNOWN"
                    ),
                }
            ],
            symbol,
        )
        self._recovery_position = None
        if self._risk_halt_reason == "RECOVERY_REQUIRED":
            self._risk_halted = False
            self._risk_halt_reason = None
        self._peak_equity = max(self._peak_equity, self._initial_balance)
        self._restore_risk_halt_for_current_window()
        if self._ledger is None or self._run_id is None:
            self._set_state_invalid("recovery has no durable v3 ledger")
            raise RuntimeError(self._state_error)
        self._cumulative_realized_pnl = self._initial_balance - self._starting_equity
        self._checkpoint_sequence += 1
        self._commit_checkpoint(
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "checkpoint_sequence": self._checkpoint_sequence,
                "last_event_sequence": self._ledger.last_sequence,
                "last_committed_event_sequence": self._ledger.last_sequence,
                "transition_protocol_version": TRANSITION_PROTOCOL_VERSION,
                "run_id": self._run_id,
                "session_id": self._session_id,
                "event_ledger_path": str(self._events_path.relative_to(self._project_root)),
                "mode": "PAPER",
                "symbol": "MULTI",
                "starting_equity_usdt": str(self._starting_equity),
                "balance_usdt": str(self._initial_balance),
                "cumulative_realized_pnl_usdt": str(self._cumulative_realized_pnl),
                "trades": self._restored_trades,
                "fees_usdt": str(self._restored_fees),
                "position": None,
                "risk_day_utc": self._risk_day_utc,
                "day_start_equity_usdt": str(self._day_start_equity),
                "trades_at_day_start": self._trades_at_day_start,
                "peak_equity_usdt": str(self._peak_equity),
                "risk_halted": self._risk_halted,
                "risk_halt_reason": self._risk_halt_reason,
                "rollover_review_required": self._rollover_review_required,
                "updated_at_utc": utc_now(),
                "updated_ns": time_ns(),
            }
        )

    def _commit_checkpoint(self, state: dict[str, object]) -> None:
        if self._ledger is None:
            raise RuntimeError("paper event ledger is unavailable")
        try:
            if self._pending_event_specs:
                committed = commit_transition(
                    self._ledger,
                    self._state_path,
                    state,
                    self._pending_event_specs,
                )
            else:
                reconcile_checkpoint(state, self._ledger.summary)
                self._write_state(state)
                committed = []
        except (IntegrityError, OSError, ValueError) as exc:
            self._set_state_invalid(str(exc))
            raise
        self._pending_event_specs.clear()
        self._cumulative_realized_pnl = self._ledger.summary.realized_pnl
        for event in committed:
            record = self._trade_record(event)
            if record is not None:
                self._trade_history.append(record)
        self._trade_history = list(reversed(self._recent_trade_history()))

    def _write_state(self, state: dict[str, object]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(state, file, allow_nan=False, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(self._state_path)

    def _portfolio(
        self,
        equity: Decimal,
        fees: Decimal,
        trades: int,
        positions: int,
    ) -> dict[str, float | int]:
        drawdown = (
            (self._peak_equity - equity) / self._peak_equity * 100
            if self._peak_equity
            else Decimal(0)
        )
        return {
            "equity_usdt": float(equity),
            "daily_pnl_usdt": float(equity - self._day_start_equity),
            "drawdown_pct": float(drawdown),
            "trades_today": max(0, trades - self._trades_at_day_start),
            "open_positions": positions,
            "fees_usdt": float(fees),
            "slippage_usdt": float(fees / TAKER_FEE * self._settings.strategy_slippage_bps / 10_000)
            if fees
            else 0.0,
        }

    def _write_events(self, events: list[dict[str, object]], symbol: str) -> None:
        if not events:
            return
        if self._ledger is None:
            raise RuntimeError("refusing to write unversioned paper events")
        for raw_event in events:
            self._pending_event_specs.append(self._prepare_strategy_event(raw_event, symbol))

    def _prepare_strategy_event(
        self,
        raw_event: dict[str, object],
        symbol: str,
    ) -> EventSpec:
        if self._ledger is None:
            raise RuntimeError("paper event ledger is unavailable")
        event_type = str(raw_event.get("event", ""))
        observed = str(raw_event.get("timestamp_utc") or utc_now())
        context = self._lifecycle.setdefault(
            symbol,
            {
                "signal_id": None,
                "position_id": None,
                "entry_order_id": None,
                "exit_order_id": None,
                "entry_client_order_id": None,
                "exit_client_order_id": None,
                "closing": None,
                "position_open_timestamp": None,
            },
        )
        if event_type == "signal":
            context.update(
                {
                    "signal_id": new_identity(),
                    "position_id": new_identity(),
                    "entry_order_id": None,
                    "exit_order_id": None,
                    "entry_client_order_id": None,
                    "exit_client_order_id": None,
                    "closing": None,
                    "position_open_timestamp": None,
                }
            )
        elif event_type == "order_submitted":
            context["entry_order_id"] = context["entry_order_id"] or new_identity()
            context["entry_client_order_id"] = str(raw_event.get("client_order_id"))
        elif event_type == "exit_signal":
            context["closing"] = "true"
            context["exit_order_id"] = new_identity()
        elif event_type == "position_opened":
            context["position_open_timestamp"] = str(
                raw_event.get("exchange_timestamp") or observed
            )

        closing = context["closing"] == "true"
        order_id = context["exit_order_id"] if closing else context["entry_order_id"]
        client_order_id = (
            context["exit_client_order_id"] if closing else context["entry_client_order_id"]
        )
        if event_type == "fill":
            raw_client_order_id = str(raw_event.get("client_order_id") or "")
            if closing:
                context["exit_client_order_id"] = raw_client_order_id
                client_order_id = raw_client_order_id
            else:
                context["entry_client_order_id"] = raw_client_order_id
                client_order_id = raw_client_order_id

        values: dict[str, object] = {
            "timestamp_utc": observed,
            "timestamp_exchange": raw_event.get("exchange_timestamp"),
            "timestamp_local_or_receive": observed,
            "symbol": symbol,
            "strategy_id": STRATEGY_VERSION,
            "signal_id": context["signal_id"],
            "position_id": context["position_id"],
            "order_id": order_id,
            "client_order_id": client_order_id,
            "fill_id": new_identity() if event_type == "fill" else None,
            "side": raw_event.get("side") or raw_event.get("direction"),
            "order_type": raw_event.get("order_type"),
            "quantity": raw_event.get("quantity"),
            "price": raw_event.get("price"),
            "fee_amount": raw_event.get("ledger_fee_usdt", raw_event.get("fee_usdt")),
            "fee_currency": "USDT" if raw_event.get("fee_usdt") is not None else None,
            "reason": raw_event.get("reason"),
            "exchange_timestamp": raw_event.get("exchange_timestamp"),
            "local_receive_timestamp": observed,
            "decision_timestamp": observed if event_type in {"signal", "exit_signal"} else None,
            "order_submit_timestamp": observed if event_type == "order_submitted" else None,
            "fill_timestamp": observed if event_type == "fill" else None,
            "latency_ms": None,
        }
        if event_type == "order_rejected":
            values["order_id"] = order_id or new_identity()
        elif event_type == "recovery_flatten":
            values["signal_id"] = raw_event.get("signal_id")
            values["position_id"] = raw_event.get("position_id")
            values["order_id"] = new_identity()
            values["client_order_id"] = "RECOVERY_FLATTEN"
            values["fill_id"] = new_identity()
            values["fill_timestamp"] = observed
        for key, value in raw_event.items():
            if key not in {"event", "timestamp_utc", "fee_usdt", "exchange_timestamp"}:
                values.setdefault(key, value)
        if event_type == "position_closed":
            opened_at = context.get("position_open_timestamp")
            closed_at = str(raw_event.get("exchange_timestamp") or observed)
            values["position_open_timestamp"] = opened_at
            values["position_close_timestamp"] = closed_at
            if opened_at is not None:
                opened = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                closed = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
                values["holding_time_ms"] = max(0, round((closed - opened).total_seconds() * 1000))
        if event_type == "position_closed":
            self._lifecycle.pop(symbol, None)
        return event_type, values

    def _open_trade(self, position: object | None) -> dict[str, object] | None:
        if position is not None:
            symbol = self._symbol_for_position(position)
            market = self._last_markets.get(symbol)
            return {
                "symbol": symbol,
                "side": position.side.name,  # type: ignore[attr-defined]
                "quantity": float(position.quantity.as_decimal()),  # type: ignore[attr-defined]
                "entry_price": position.avg_px_open,  # type: ignore[attr-defined]
                "current_price": market.get("last") if market is not None else None,
            }
        if self._recovery_position is not None:
            symbol = str(self._recovery_position.get("symbol", ""))
            market = self._last_markets.get(symbol)
            return {
                "symbol": symbol,
                "side": self._recovery_position.get("side"),
                "quantity": self._recovery_position.get("quantity"),
                "entry_price": self._recovery_position.get("entry_price"),
                "current_price": market.get("last") if market is not None else None,
            }
        return None

    def _load_trade_history(self) -> list[dict[str, object]]:
        if not self._events_path.is_file():
            return []
        records = []
        entry_fills: dict[str, list[dict[str, object]]] = {}
        exit_fills: dict[str, list[dict[str, object]]] = {}
        closing: set[str] = set()
        try:
            with self._events_path.open(encoding="utf-8") as file:
                for line in file:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("schema_version") == EVENT_SCHEMA_VERSION:
                        record = self._trade_record(event)
                        if record is not None:
                            records.append(record)
                        continue
                    symbol = event.get("symbol")
                    event_type = event.get("event")
                    if isinstance(symbol, str):
                        if event_type == "signal":
                            entry_fills[symbol] = []
                            exit_fills[symbol] = []
                            closing.discard(symbol)
                        elif event_type == "fill" and symbol in entry_fills:
                            target = exit_fills if symbol in closing else entry_fills
                            target[symbol].append(event)
                        elif event_type == "exit_signal" and symbol in entry_fills:
                            closing.add(symbol)
                        elif event_type == "position_closed" and symbol in entry_fills:
                            event = self._aggregate_trade_fills(
                                event,
                                entry_fills.pop(symbol),
                                exit_fills.pop(symbol),
                            )
                            closing.discard(symbol)
                    record = self._trade_record(event)
                    if record is not None:
                        records.append(record)
        except OSError:
            return []
        return records

    @staticmethod
    def _aggregate_trade_fills(
        event: dict[str, object],
        entry_fills: list[dict[str, object]],
        exit_fills: list[dict[str, object]],
    ) -> dict[str, object]:
        try:
            entry_quantity, entry_notional, entry_fee = _fill_totals(entry_fills)
            exit_quantity, exit_notional, exit_fee = _fill_totals(exit_fills)
            realized = Decimal(str(event["realized_pnl_usdt"]))
        except (KeyError, ValueError, TypeError, InvalidOperation):
            return event
        if not all(
            value.is_finite()
            for value in (
                entry_quantity,
                entry_notional,
                entry_fee,
                exit_quantity,
                exit_notional,
                exit_fee,
                realized,
            )
        ):
            return event
        tolerance = max(Decimal("0.000001"), entry_quantity * Decimal("0.000001"))
        if (
            entry_quantity <= 0
            or exit_quantity <= 0
            or abs(entry_quantity - exit_quantity) > tolerance
        ):
            return event
        return {
            **event,
            "quantity": float(entry_quantity),
            "open_price": float(entry_notional / entry_quantity),
            "close_price": float(exit_notional / exit_quantity),
            "fee_usdt": float(entry_fee + exit_fee),
            "pnl_pct": float(realized / entry_notional * 100),
        }

    def _recent_trade_history(self) -> list[dict[str, object]]:
        cutoff = datetime.now(UTC) - timedelta(hours=TRADE_HISTORY_HOURS)
        recent = [
            record
            for record in self._trade_history
            if datetime.fromisoformat(str(record["closed_at"]).replace("Z", "+00:00")) >= cutoff
        ]
        return list(reversed(recent))

    @staticmethod
    def _trade_record(event: object) -> dict[str, object] | None:
        if not isinstance(event, dict) or event.get("event_type", event.get("event")) not in {
            "position_closed",
            "recovery_flatten",
        }:
            return None
        try:
            closed_at = str(event["timestamp_utc"])
            parsed_time = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            values = {
                key: float(event[key])
                for key in (
                    "quantity",
                    "open_price",
                    "close_price",
                    "realized_pnl_usdt",
                    "pnl_pct",
                )
            }
            fee_value = (
                event["total_fee_usdt"]
                if "total_fee_usdt" in event
                else event["fee_amount"]
                if "fee_amount" in event
                else event["fee_usdt"]
            )
            values["fee_usdt"] = float(fee_value)
            symbol = str(event["symbol"])
            side = str(event["side"])
            reason = str(event["reason"])
        except (KeyError, TypeError, ValueError):
            return None
        if (
            parsed_time.tzinfo is None
            or not all(math.isfinite(value) for value in values.values())
            or values["quantity"] <= 0
            or values["open_price"] <= 0
            or values["close_price"] <= 0
            or values["fee_usdt"] < 0
            or side not in {"LONG", "SHORT"}
            or not symbol
            or len(symbol) > 32
            or not reason
            or len(reason) > 64
        ):
            return None
        return {
            "closed_at": parsed_time.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "symbol": symbol,
            "side": side,
            **values,
            "reason": reason,
            "position_id": event.get("position_id"),
            "holding_time_ms": event.get("holding_time_ms"),
        }


def _decimal_places(value: object) -> int:
    exponent = Decimal(str(value)).normalize().as_tuple().exponent
    if not isinstance(exponent, int):
        return 8
    return max(0, -exponent)


def construct_pessimistic_prices(
    raw_bid: Decimal,
    raw_ask: Decimal,
    price_increment: Decimal,
    slippage_bps: Decimal,
) -> tuple[Decimal, Decimal]:
    values = (raw_bid, raw_ask, price_increment, slippage_bps)
    if not all(value.is_finite() for value in values):
        raise QuoteValidationError("quote inputs must be finite")
    if raw_bid <= 0 or raw_ask <= 0 or raw_bid >= raw_ask:
        raise QuoteValidationError("raw quote must satisfy 0 < bid < ask")
    if price_increment <= 0 or slippage_bps < 0:
        raise QuoteValidationError("tick size and slippage are invalid")
    slippage = slippage_bps / Decimal(10_000)
    modeled_bid = raw_bid * (Decimal(1) - slippage)
    modeled_ask = raw_ask * (Decimal(1) + slippage)
    bid = (modeled_bid / price_increment).to_integral_value(rounding=ROUND_FLOOR) * price_increment
    ask = (modeled_ask / price_increment).to_integral_value(
        rounding=ROUND_CEILING
    ) * price_increment
    if bid <= 0:
        raise QuoteValidationError("modeled bid rounded to zero")
    if ask <= bid:
        ask = bid + price_increment
    if ask <= bid:
        raise QuoteValidationError("modeled quote remains crossed after tick repair")
    return bid, ask


def _state_decimal(value: object, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return parsed


def _ns_to_utc(timestamp_ns: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ns / 1_000_000_000, UTC).isoformat().replace("+00:00", "Z")
    )


def _fill_totals(fills: list[dict[str, object]]) -> tuple[Decimal, Decimal, Decimal]:
    quantity = Decimal(0)
    notional = Decimal(0)
    fees = Decimal(0)
    for fill in fills:
        fill_quantity = Decimal(str(fill["quantity"]))
        fill_price = Decimal(str(fill["price"]))
        fill_fee = Decimal(str(fill["fee_usdt"]))
        if fill_quantity <= 0 or fill_price <= 0 or fill_fee < 0:
            raise ValueError("invalid fill values")
        quantity += fill_quantity
        notional += fill_quantity * fill_price
        fees += fill_fee
    return quantity, notional, fees
