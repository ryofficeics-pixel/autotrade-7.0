from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from autotrade.ama_control import AmaControlSettings
from autotrade.market_scope import MarketScope, SwitchPolicy, XauSettings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "paper.toml"


class ConfigError(ValueError):
    """Raised when configuration is missing or violates Phase 1 safety."""


@dataclass(frozen=True)
class EntryV3Settings:
    enabled: bool
    shadow_enabled: bool
    execution_enabled: bool
    book_depth: int
    feature_window_events: int
    feature_window_ms: int
    impulse_window_ms: int
    impulse_min_bps: Decimal
    pullback_min_ratio: Decimal
    pullback_max_ratio: Decimal
    reacceleration_bps: Decimal
    flow_confirmation: Decimal
    breakout_flow: Decimal
    book_confirmation: Decimal
    vwap_extension_bps: Decimal
    maximum_spread_bps: Decimal
    minimum_net_edge_bps: Decimal
    continuation_fraction: Decimal
    observed_latency_cap_ms: int
    whipsaw_block_ms: int
    maximum_reversals: int
    minimum_events: int


@dataclass(frozen=True)
class ExperimentSettings:
    risk_normalized_sizing: bool
    risk_budget_usdt: Decimal
    maximum_position_notional_usdt: Decimal
    maximum_symbol_exposure_usdt: Decimal
    liquidity_notional_cap_usdt: Decimal
    signal_reset_reentry: bool
    maximum_symbol_attempts: int
    attempt_window_seconds: int
    maximum_consecutive_symbol_losses: int
    maximum_symbol_loss_utc_day_usdt: Decimal
    maximum_symbol_loss_wib_day_usdt: Decimal
    candidate_ranking: bool
    candidate_top_n: int
    cost_to_edge_gate: bool
    cost_multiplier: Decimal
    market_quality_filters: bool
    maximum_entry_spread_bps: Decimal
    minimum_entry_quote_volume: Decimal
    minimum_depth_usdt: Decimal
    maximum_one_bar_volatility_bps: Decimal
    maximum_price_gap_bps: Decimal
    require_order_book: bool
    exit_variant: str
    runner_trail_bps: Decimal
    runner_max_hold_seconds: int
    edge_decay_time_exit: bool
    protective_orders: bool


@dataclass(frozen=True)
class Settings:
    mode: str
    live_trading_enabled: bool
    live_confirmation: str
    venue: str
    trader_id: str
    starting_balance_usdt: Decimal
    leverage: Decimal
    log_console_level: str
    log_file_level: str
    log_directory: Path
    log_file_name: str
    log_file_max_size: int
    log_file_max_backup_count: int
    market_poll_seconds: int
    market_stale_after_seconds: int
    active_symbols: int
    minimum_quote_volume: Decimal
    maximum_spread_bps: Decimal
    strategy_enabled: bool
    strategy_symbol: str
    strategy_notional_usdt: Decimal
    strategy_window: int
    strategy_persistence_ticks: int
    strategy_regime_window: int
    strategy_entry_threshold_bps: Decimal
    strategy_minimum_net_edge_bps: Decimal
    strategy_minimum_confidence: Decimal
    strategy_stop_loss_bps: Decimal
    strategy_take_profit_bps: Decimal
    strategy_max_hold_seconds: int
    strategy_cooldown_seconds: int
    strategy_slippage_bps: Decimal
    strategy_daily_loss_usdt: Decimal
    strategy_max_drawdown_pct: Decimal
    market_scope: MarketScope
    mode_switch_policy: SwitchPolicy
    xau: XauSettings
    ama_control: AmaControlSettings
    entry_v3: EntryV3Settings
    experiments: ExperimentSettings
    dashboard_host: str
    dashboard_port: int
    tradingview_enabled: bool
    tradingview_confirmation_mode: str
    tradingview_weight: Decimal
    tradingview_timeout_ms: int
    tradingview_cache_enabled: bool
    tradingview_cache_ttl_seconds: int
    tradingview_poll_seconds: int
    tradingview_stale_after_seconds: int
    tradingview_expected_timeframe: str
    tradingview_pine_enabled: bool
    tradingview_screenshot_enabled: bool


def _table(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"missing [{name}] configuration")
    return value


def _optional_table(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] configuration must be a table")
    return value


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    raise ConfigError(f"{name} must be true or false")


def _decimal(value: object, name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(f"{name} must be numeric") from exc


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ConfigError(f"{name} must be an integer")
    try:
        result = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if str(result) != str(value):
        raise ConfigError(f"{name} must be an integer")
    return result


def load_settings(path: Path | str = DEFAULT_CONFIG_PATH) -> Settings:
    config_path = Path(path)
    try:
        with config_path.open("rb") as file:
            document = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot load configuration {config_path}: {exc}") from exc

    trading = _table(document, "trading")
    logging = _table(document, "logging")
    market_data = _table(document, "market_data")
    strategy = _table(document, "paper_strategy")
    entry_v3 = _optional_table(strategy, "entry_v3")
    impulse = _optional_table(entry_v3, "impulse")
    pullback = _optional_table(entry_v3, "pullback")
    flow = _optional_table(entry_v3, "flow")
    book = _optional_table(entry_v3, "book")
    exhaustion = _optional_table(entry_v3, "exhaustion")
    edge = _optional_table(entry_v3, "edge")
    whipsaw = _optional_table(entry_v3, "whipsaw")
    experiments = _optional_table(strategy, "experiments")
    dashboard = _table(document, "dashboard")
    tradingview = _optional_table(document, "tradingview")
    market = _optional_table(document, "market")
    mode_switch = _optional_table(document, "mode_switch")
    xau = _optional_table(document, "xau")
    confirmations = _optional_table(xau, "confirmations")
    xau_risk = _optional_table(xau, "risk")
    ama_control = _optional_table(xau, "ama_control")

    mode = os.getenv("TRADING_MODE", str(trading.get("mode", ""))).strip().upper()
    live_enabled = _boolean(
        os.getenv("LIVE_TRADING_ENABLED", trading.get("live_trading_enabled")),
        "LIVE_TRADING_ENABLED",
    )
    live_confirmation = os.getenv(
        "LIVE_CONFIRMATION", str(trading.get("live_confirmation", ""))
    ).strip()
    venue = str(trading.get("venue", "")).strip().upper()
    trader_id = str(trading.get("trader_id", "")).strip()
    starting_balance = _decimal(trading.get("starting_balance_usdt"), "starting_balance_usdt")
    leverage = _decimal(trading.get("leverage"), "leverage")
    log_console_level = str(logging.get("console_level", "WARNING")).strip().upper()
    log_file_level = str(logging.get("file_level", "INFO")).strip().upper()
    log_directory = PROJECT_ROOT / str(logging.get("directory", "logs"))
    log_file_name = str(logging.get("file_name", "engine")).strip()
    log_file_max_size = _integer(
        logging.get("max_file_size_bytes", 10_000_000), "max_file_size_bytes"
    )
    log_file_max_backup_count = _integer(logging.get("max_backup_count", 5), "max_backup_count")
    market_poll_seconds = _integer(market_data.get("poll_seconds", 5), "poll_seconds")
    market_stale_after_seconds = _integer(
        market_data.get("stale_after_seconds", 15), "stale_after_seconds"
    )
    active_symbols = _integer(market_data.get("active_symbols", 8), "active_symbols")
    minimum_quote_volume = _decimal(
        market_data.get("minimum_quote_volume", "5000000"), "minimum_quote_volume"
    )
    maximum_spread_bps = _decimal(market_data.get("maximum_spread_bps", "12"), "maximum_spread_bps")
    strategy_enabled = _boolean(strategy.get("enabled", False), "paper_strategy.enabled")
    strategy_symbol = str(strategy.get("symbol", "")).strip().upper()
    strategy_notional_usdt = _decimal(
        strategy.get("notional_usdt", "0"), "paper_strategy.notional_usdt"
    )
    strategy_window = _integer(strategy.get("window", 12), "paper_strategy.window")
    strategy_persistence_ticks = _integer(
        strategy.get("persistence_ticks", 3), "paper_strategy.persistence_ticks"
    )
    strategy_regime_window = _integer(
        strategy.get("regime_window", strategy_window * 3),
        "paper_strategy.regime_window",
    )
    strategy_entry_threshold_bps = _decimal(
        strategy.get("entry_threshold_bps", "20"), "paper_strategy.entry_threshold_bps"
    )
    strategy_minimum_net_edge_bps = _decimal(
        strategy.get("minimum_net_edge_bps", "12"),
        "paper_strategy.minimum_net_edge_bps",
    )
    strategy_minimum_confidence = _decimal(
        strategy.get("minimum_confidence", "0.60"),
        "paper_strategy.minimum_confidence",
    )
    strategy_stop_loss_bps = _decimal(
        strategy.get("stop_loss_bps", "35"), "paper_strategy.stop_loss_bps"
    )
    strategy_take_profit_bps = _decimal(
        strategy.get("take_profit_bps", "55"), "paper_strategy.take_profit_bps"
    )
    strategy_max_hold_seconds = _integer(
        strategy.get("max_hold_seconds", 300), "paper_strategy.max_hold_seconds"
    )
    strategy_cooldown_seconds = _integer(
        strategy.get("cooldown_seconds", 60), "paper_strategy.cooldown_seconds"
    )
    strategy_slippage_bps = _decimal(
        strategy.get("slippage_bps", "2"), "paper_strategy.slippage_bps"
    )
    strategy_daily_loss_usdt = _decimal(
        strategy.get("daily_loss_usdt", "6"), "paper_strategy.daily_loss_usdt"
    )
    strategy_max_drawdown_pct = _decimal(
        strategy.get("max_drawdown_pct", "3"), "paper_strategy.max_drawdown_pct"
    )
    try:
        market_scope = MarketScope(str(market.get("scope", "WIDE_CRYPTO")).strip().upper())
        mode_switch_policy = SwitchPolicy(
            str(mode_switch.get("policy", "SWITCH_WHEN_FLAT")).strip().upper()
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    raw_confirmation_symbols = confirmations.get("symbols", ["XAUT_USDT", "PAXG_USDT"])
    if not isinstance(raw_confirmation_symbols, list) or not all(
        isinstance(symbol, str) for symbol in raw_confirmation_symbols
    ):
        raise ConfigError("xau.confirmations.symbols must be a string array")
    xau_settings = XauSettings(
        execution_symbol=str(xau.get("execution_symbol", "XAU_USDT")).strip().upper(),
        confirmation_symbols=tuple(symbol.strip().upper() for symbol in raw_confirmation_symbols),
        confirmations_enabled=_boolean(
            confirmations.get("enabled", True), "xau.confirmations.enabled"
        ),
        confirmations_required=_boolean(
            confirmations.get("required", False), "xau.confirmations.required"
        ),
        stale_after_seconds=_integer(
            xau.get("stale_after_seconds", market_stale_after_seconds),
            "xau.stale_after_seconds",
        ),
        short_window=_integer(xau.get("short_window", 3), "xau.short_window"),
        trend_window=_integer(xau.get("trend_window", 12), "xau.trend_window"),
        entry_threshold_bps=_decimal(
            xau.get("entry_threshold_bps", "12"), "xau.entry_threshold_bps"
        ),
        abnormal_divergence_bps=_decimal(
            confirmations.get("abnormal_divergence_bps", "18"),
            "xau.confirmations.abnormal_divergence_bps",
        ),
        high_volatility_bps=_decimal(
            xau.get("high_volatility_bps", "16"), "xau.high_volatility_bps"
        ),
        low_volatility_bps=_decimal(
            xau.get("low_volatility_bps", "3"), "xau.low_volatility_bps"
        ),
        minimum_confidence=_decimal(
            xau_risk.get("minimum_confidence", "0.65"), "xau.risk.minimum_confidence"
        ),
        maximum_spread_bps=_decimal(
            xau_risk.get("maximum_spread_bps", "8"), "xau.risk.maximum_spread_bps"
        ),
        slippage_bps=_decimal(xau_risk.get("slippage_bps", "2"), "xau.risk.slippage_bps"),
        risk_per_trade_usdt=_decimal(
            xau_risk.get("risk_per_trade_usdt", "0.15"), "xau.risk.risk_per_trade_usdt"
        ),
        maximum_position_notional_usdt=_decimal(
            xau_risk.get("maximum_position_notional_usdt", "15"),
            "xau.risk.maximum_position_notional_usdt",
        ),
        maximum_leverage=_decimal(
            xau_risk.get("maximum_leverage", "1"), "xau.risk.maximum_leverage"
        ),
        maximum_daily_loss_usdt=_decimal(
            xau_risk.get("maximum_daily_loss_usdt", "3"),
            "xau.risk.maximum_daily_loss_usdt",
        ),
        maximum_consecutive_losses=_integer(
            xau_risk.get("maximum_consecutive_losses", 3),
            "xau.risk.maximum_consecutive_losses",
        ),
        stop_loss_method=str(xau_risk.get("stop_loss_method", "FIXED_BPS")).strip().upper(),
        stop_loss_bps=_decimal(
            xau_risk.get("stop_loss_bps", "35"), "xau.risk.stop_loss_bps"
        ),
        take_profit_method=str(
            xau_risk.get("take_profit_method", "FIXED_BPS")
        ).strip().upper(),
        take_profit_bps=_decimal(
            xau_risk.get("take_profit_bps", "55"), "xau.risk.take_profit_bps"
        ),
        trailing_enabled=_boolean(
            xau_risk.get("trailing_enabled", False), "xau.risk.trailing_enabled"
        ),
        trailing_bps=_decimal(
            xau_risk.get("trailing_bps", "30"), "xau.risk.trailing_bps"
        ),
        cooldown_seconds=_integer(
            xau_risk.get("cooldown_seconds", 60), "xau.risk.cooldown_seconds"
        ),
        volatility_scaling=_boolean(
            xau_risk.get("volatility_scaling", True), "xau.risk.volatility_scaling"
        ),
        high_volatility_size_factor=_decimal(
            xau_risk.get("high_volatility_size_factor", "0.50"),
            "xau.risk.high_volatility_size_factor",
        ),
        degraded_confirmation_size_factor=_decimal(
            xau_risk.get("degraded_confirmation_size_factor", "0.75"),
            "xau.risk.degraded_confirmation_size_factor",
        ),
        maximum_holding_seconds=_integer(
            xau_risk.get("maximum_holding_seconds", 300),
            "xau.risk.maximum_holding_seconds",
        ),
    )
    ama_control_settings = AmaControlSettings(
        enabled=_boolean(ama_control.get("enabled", True), "xau.ama_control.enabled"),
        execution_enabled=_boolean(
            ama_control.get("execution_enabled", False),
            "xau.ama_control.execution_enabled",
        ),
        fast_period=_integer(ama_control.get("fast_period", 10), "xau.ama_control.fast_period"),
        control_period=_integer(
            ama_control.get("control_period", 20), "xau.ama_control.control_period"
        ),
        slow_period=_integer(ama_control.get("slow_period", 50), "xau.ama_control.slow_period"),
        kama_fast=_integer(ama_control.get("kama_fast", 2), "xau.ama_control.kama_fast"),
        kama_slow=_integer(ama_control.get("kama_slow", 30), "xau.ama_control.kama_slow"),
        atr_proxy_window=_integer(
            ama_control.get("atr_proxy_window", 20), "xau.ama_control.atr_proxy_window"
        ),
        atr_multiplier=_decimal(
            ama_control.get("atr_multiplier", "1.50"), "xau.ama_control.atr_multiplier"
        ),
        minimum_hysteresis_bps=_decimal(
            ama_control.get("minimum_hysteresis_bps", "3"),
            "xau.ama_control.minimum_hysteresis_bps",
        ),
        minimum_slope_bps=_decimal(
            ama_control.get("minimum_slope_bps", "0.10"),
            "xau.ama_control.minimum_slope_bps",
        ),
        maximum_distance_bps=_decimal(
            ama_control.get("maximum_distance_bps", "50"),
            "xau.ama_control.maximum_distance_bps",
        ),
        minimum_net_edge_bps=_decimal(
            ama_control.get("minimum_net_edge_bps", "3"),
            "xau.ama_control.minimum_net_edge_bps",
        ),
        minimum_confidence=_decimal(
            ama_control.get("minimum_confidence", "0.55"),
            "xau.ama_control.minimum_confidence",
        ),
        minimum_regime_observations=_integer(
            ama_control.get("minimum_regime_observations", 3),
            "xau.ama_control.minimum_regime_observations",
        ),
        maximum_spread_bps=_decimal(
            ama_control.get("maximum_spread_bps", str(xau_settings.maximum_spread_bps)),
            "xau.ama_control.maximum_spread_bps",
        ),
        minimum_quote_volume_usdt=_decimal(
            ama_control.get("minimum_quote_volume_usdt", "1000000"),
            "xau.ama_control.minimum_quote_volume_usdt",
        ),
        maximum_reference_dispersion_bps=_decimal(
            ama_control.get("maximum_reference_dispersion_bps", "25"),
            "xau.ama_control.maximum_reference_dispersion_bps",
        ),
        maximum_reference_dislocation_bps=_decimal(
            ama_control.get("maximum_reference_dislocation_bps", "35"),
            "xau.ama_control.maximum_reference_dislocation_bps",
        ),
        reference_required=_boolean(
            ama_control.get("reference_required", False),
            "xau.ama_control.reference_required",
        ),
        notional_usdt=_decimal(
            ama_control.get("notional_usdt", str(xau_settings.maximum_position_notional_usdt)),
            "xau.ama_control.notional_usdt",
        ),
        maximum_holding_seconds=_integer(
            ama_control.get("maximum_holding_seconds", 900),
            "xau.ama_control.maximum_holding_seconds",
        ),
        round_trip_fee_bps=_decimal(
            ama_control.get("round_trip_fee_bps", "10"),
            "xau.ama_control.round_trip_fee_bps",
        ),
        slippage_bps_per_side=_decimal(
            ama_control.get("slippage_bps_per_side", str(xau_settings.slippage_bps)),
            "xau.ama_control.slippage_bps_per_side",
        ),
        funding_buffer_bps=_decimal(
            ama_control.get("funding_buffer_bps", "3"),
            "xau.ama_control.funding_buffer_bps",
        ),
        stressed_cost_multiplier=_decimal(
            ama_control.get("stressed_cost_multiplier", "1.50"),
            "xau.ama_control.stressed_cost_multiplier",
        ),
        minimum_comparison_trades=_integer(
            ama_control.get("minimum_comparison_trades", 30),
            "xau.ama_control.minimum_comparison_trades",
        ),
        quarantine_minimum_trades=_integer(
            ama_control.get("quarantine_minimum_trades", 30),
            "xau.ama_control.quarantine_minimum_trades",
        ),
        quarantine_profit_factor=_decimal(
            ama_control.get("quarantine_profit_factor", "0.80"),
            "xau.ama_control.quarantine_profit_factor",
        ),
        decision_interval_seconds=_integer(
            ama_control.get("decision_interval_seconds", 30),
            "xau.ama_control.decision_interval_seconds",
        ),
        report_interval_seconds=_integer(
            ama_control.get("report_interval_seconds", 60),
            "xau.ama_control.report_interval_seconds",
        ),
    )
    entry_v3_settings = EntryV3Settings(
        enabled=_boolean(entry_v3.get("enabled", True), "paper_strategy.entry_v3.enabled"),
        shadow_enabled=_boolean(
            entry_v3.get("shadow_enabled", entry_v3.get("enabled", True)),
            "paper_strategy.entry_v3.shadow_enabled",
        ),
        execution_enabled=_boolean(
            entry_v3.get("execution_enabled", False),
            "paper_strategy.entry_v3.execution_enabled",
        ),
        book_depth=_integer(book.get("depth", 5), "entry_v3.book.depth"),
        feature_window_events=_integer(
            entry_v3.get("feature_window_events", 256),
            "entry_v3.feature_window_events",
        ),
        feature_window_ms=_integer(
            entry_v3.get("feature_window_ms", 5000), "entry_v3.feature_window_ms"
        ),
        impulse_window_ms=_integer(impulse.get("window_ms", 1500), "entry_v3.impulse.window_ms"),
        impulse_min_bps=_decimal(impulse.get("minimum_bps", "8"), "entry_v3.impulse.minimum_bps"),
        pullback_min_ratio=_decimal(
            pullback.get("minimum_ratio", "0.20"), "entry_v3.pullback.minimum_ratio"
        ),
        pullback_max_ratio=_decimal(
            pullback.get("maximum_ratio", "0.60"), "entry_v3.pullback.maximum_ratio"
        ),
        reacceleration_bps=_decimal(
            pullback.get("reacceleration_bps", "2"),
            "entry_v3.pullback.reacceleration_bps",
        ),
        flow_confirmation=_decimal(flow.get("confirmation", "0.15"), "entry_v3.flow.confirmation"),
        breakout_flow=_decimal(
            flow.get("breakout_confirmation", "0.35"),
            "entry_v3.flow.breakout_confirmation",
        ),
        book_confirmation=_decimal(book.get("confirmation", "0.05"), "entry_v3.book.confirmation"),
        vwap_extension_bps=_decimal(
            exhaustion.get("vwap_extension_bps", "15"),
            "entry_v3.exhaustion.vwap_extension_bps",
        ),
        maximum_spread_bps=_decimal(
            book.get("maximum_spread_bps", "8"), "entry_v3.book.maximum_spread_bps"
        ),
        minimum_net_edge_bps=_decimal(
            edge.get("minimum_net_edge_bps", "5"),
            "entry_v3.edge.minimum_net_edge_bps",
        ),
        continuation_fraction=_decimal(
            edge.get("continuation_fraction", "0.35"),
            "entry_v3.edge.continuation_fraction",
        ),
        observed_latency_cap_ms=_integer(
            edge.get("observed_latency_cap_ms", 1000),
            "entry_v3.edge.observed_latency_cap_ms",
        ),
        whipsaw_block_ms=_integer(whipsaw.get("block_ms", 30000), "entry_v3.whipsaw.block_ms"),
        maximum_reversals=_integer(
            whipsaw.get("maximum_reversals", 4), "entry_v3.whipsaw.maximum_reversals"
        ),
        minimum_events=_integer(entry_v3.get("minimum_events", 12), "entry_v3.minimum_events"),
    )
    experiment_settings = ExperimentSettings(
        risk_normalized_sizing=_boolean(
            experiments.get("risk_normalized_sizing", False),
            "paper_strategy.experiments.risk_normalized_sizing",
        ),
        risk_budget_usdt=_decimal(
            experiments.get("risk_budget_usdt", "0.20"),
            "paper_strategy.experiments.risk_budget_usdt",
        ),
        maximum_position_notional_usdt=_decimal(
            experiments.get("maximum_position_notional_usdt", strategy_notional_usdt),
            "paper_strategy.experiments.maximum_position_notional_usdt",
        ),
        maximum_symbol_exposure_usdt=_decimal(
            experiments.get("maximum_symbol_exposure_usdt", strategy_notional_usdt),
            "paper_strategy.experiments.maximum_symbol_exposure_usdt",
        ),
        liquidity_notional_cap_usdt=_decimal(
            experiments.get("liquidity_notional_cap_usdt", strategy_notional_usdt),
            "paper_strategy.experiments.liquidity_notional_cap_usdt",
        ),
        signal_reset_reentry=_boolean(
            experiments.get("signal_reset_reentry", False),
            "paper_strategy.experiments.signal_reset_reentry",
        ),
        maximum_symbol_attempts=_integer(
            experiments.get("maximum_symbol_attempts", 0),
            "paper_strategy.experiments.maximum_symbol_attempts",
        ),
        attempt_window_seconds=_integer(
            experiments.get("attempt_window_seconds", 3600),
            "paper_strategy.experiments.attempt_window_seconds",
        ),
        maximum_consecutive_symbol_losses=_integer(
            experiments.get("maximum_consecutive_symbol_losses", 0),
            "paper_strategy.experiments.maximum_consecutive_symbol_losses",
        ),
        maximum_symbol_loss_utc_day_usdt=_decimal(
            experiments.get("maximum_symbol_loss_utc_day_usdt", "0"),
            "paper_strategy.experiments.maximum_symbol_loss_utc_day_usdt",
        ),
        maximum_symbol_loss_wib_day_usdt=_decimal(
            experiments.get("maximum_symbol_loss_wib_day_usdt", "0"),
            "paper_strategy.experiments.maximum_symbol_loss_wib_day_usdt",
        ),
        candidate_ranking=_boolean(
            experiments.get("candidate_ranking", False),
            "paper_strategy.experiments.candidate_ranking",
        ),
        candidate_top_n=_integer(
            experiments.get("candidate_top_n", 1),
            "paper_strategy.experiments.candidate_top_n",
        ),
        cost_to_edge_gate=_boolean(
            experiments.get("cost_to_edge_gate", False),
            "paper_strategy.experiments.cost_to_edge_gate",
        ),
        cost_multiplier=_decimal(
            experiments.get("cost_multiplier", "1.50"),
            "paper_strategy.experiments.cost_multiplier",
        ),
        market_quality_filters=_boolean(
            experiments.get("market_quality_filters", False),
            "paper_strategy.experiments.market_quality_filters",
        ),
        maximum_entry_spread_bps=_decimal(
            experiments.get("maximum_entry_spread_bps", maximum_spread_bps),
            "paper_strategy.experiments.maximum_entry_spread_bps",
        ),
        minimum_entry_quote_volume=_decimal(
            experiments.get("minimum_entry_quote_volume", minimum_quote_volume),
            "paper_strategy.experiments.minimum_entry_quote_volume",
        ),
        minimum_depth_usdt=_decimal(
            experiments.get("minimum_depth_usdt", "0"),
            "paper_strategy.experiments.minimum_depth_usdt",
        ),
        maximum_one_bar_volatility_bps=_decimal(
            experiments.get("maximum_one_bar_volatility_bps", "0"),
            "paper_strategy.experiments.maximum_one_bar_volatility_bps",
        ),
        maximum_price_gap_bps=_decimal(
            experiments.get("maximum_price_gap_bps", "0"),
            "paper_strategy.experiments.maximum_price_gap_bps",
        ),
        require_order_book=_boolean(
            experiments.get("require_order_book", False),
            "paper_strategy.experiments.require_order_book",
        ),
        exit_variant=str(experiments.get("exit_variant", "BASELINE_FULL_TP")).strip().upper(),
        runner_trail_bps=_decimal(
            experiments.get("runner_trail_bps", strategy_stop_loss_bps),
            "paper_strategy.experiments.runner_trail_bps",
        ),
        runner_max_hold_seconds=_integer(
            experiments.get("runner_max_hold_seconds", strategy_max_hold_seconds),
            "paper_strategy.experiments.runner_max_hold_seconds",
        ),
        edge_decay_time_exit=_boolean(
            experiments.get("edge_decay_time_exit", False),
            "paper_strategy.experiments.edge_decay_time_exit",
        ),
        protective_orders=_boolean(
            experiments.get("protective_orders", False),
            "paper_strategy.experiments.protective_orders",
        ),
    )
    dashboard_host = str(dashboard.get("host", "127.0.0.1")).strip()
    dashboard_port = _integer(dashboard.get("port", 8765), "dashboard.port")
    tradingview_enabled = _boolean(
        os.getenv("TRADINGVIEW_ENABLED", tradingview.get("enabled", False)),
        "TRADINGVIEW_ENABLED",
    )
    tradingview_confirmation_mode = (
        os.getenv(
            "TRADINGVIEW_CONFIRMATION_MODE",
            str(tradingview.get("confirmation_mode", "borderline")),
        )
        .strip()
        .lower()
    )
    tradingview_weight = _decimal(
        os.getenv("TRADINGVIEW_WEIGHT", tradingview.get("weight", "0.20")),
        "TRADINGVIEW_WEIGHT",
    )
    tradingview_timeout_ms = _integer(
        os.getenv("TRADINGVIEW_TIMEOUT_MS", tradingview.get("timeout_ms", 2500)),
        "TRADINGVIEW_TIMEOUT_MS",
    )
    tradingview_cache_enabled = _boolean(
        os.getenv(
            "TRADINGVIEW_CACHE_ENABLED",
            tradingview.get("cache_enabled", True),
        ),
        "TRADINGVIEW_CACHE_ENABLED",
    )
    tradingview_cache_ttl_seconds = _integer(
        tradingview.get("cache_ttl_seconds", 15), "tradingview.cache_ttl_seconds"
    )
    tradingview_poll_seconds = _integer(
        tradingview.get("poll_seconds", 15), "tradingview.poll_seconds"
    )
    tradingview_stale_after_seconds = _integer(
        tradingview.get("stale_after_seconds", 45), "tradingview.stale_after_seconds"
    )
    tradingview_expected_timeframe = str(tradingview.get("expected_timeframe", "5")).strip().upper()
    tradingview_pine_enabled = _boolean(
        os.getenv("TRADINGVIEW_PINE_ENABLED", tradingview.get("pine_enabled", True)),
        "TRADINGVIEW_PINE_ENABLED",
    )
    tradingview_screenshot_enabled = _boolean(
        os.getenv(
            "TRADINGVIEW_SCREENSHOT_ENABLED",
            tradingview.get("screenshot_enabled", False),
        ),
        "TRADINGVIEW_SCREENSHOT_ENABLED",
    )

    if mode != "PAPER":
        raise ConfigError("Phase 1 requires TRADING_MODE=PAPER")
    if live_enabled:
        raise ConfigError("Phase 1 requires LIVE_TRADING_ENABLED=false")
    if live_confirmation:
        raise ConfigError("LIVE_CONFIRMATION must be empty in Phase 1")
    if venue != "GATE":
        raise ConfigError("Phase 1 venue must be GATE")
    if not trader_id:
        raise ConfigError("trader_id must not be empty")
    if starting_balance <= 0:
        raise ConfigError("starting_balance_usdt must be positive")
    if leverage <= 0 or leverage > 1:
        raise ConfigError("Phase 1 leverage must be greater than 0 and no more than 1x")
    valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR"}
    if log_console_level not in valid_log_levels or log_file_level not in valid_log_levels:
        raise ConfigError("logging levels must be DEBUG, INFO, WARNING, or ERROR")
    if not log_file_name or Path(log_file_name).name != log_file_name:
        raise ConfigError("logging.file_name must be a plain file name")
    if log_file_max_size <= 0:
        raise ConfigError("max_file_size_bytes must be positive")
    if log_file_max_backup_count < 1:
        raise ConfigError("max_backup_count must be at least 1")
    if market_poll_seconds < 2:
        raise ConfigError("poll_seconds must be at least 2")
    if market_stale_after_seconds <= market_poll_seconds:
        raise ConfigError("stale_after_seconds must be greater than poll_seconds")
    if not 5 <= active_symbols <= 10:
        raise ConfigError("active_symbols must be between 5 and 10")
    if minimum_quote_volume < 0:
        raise ConfigError("minimum_quote_volume must not be negative")
    if maximum_spread_bps <= 0:
        raise ConfigError("maximum_spread_bps must be positive")
    base_symbol = strategy_symbol.removesuffix("_USDT")
    if not base_symbol or not base_symbol.isascii() or not base_symbol.isalnum():
        raise ConfigError("paper_strategy.symbol must be an ASCII BASE_USDT contract")
    if strategy_notional_usdt <= 0 or strategy_notional_usdt > starting_balance / 4:
        raise ConfigError(
            "paper_strategy.notional_usdt must be positive and at most 25% of capital"
        )
    if not 3 <= strategy_window <= 120:
        raise ConfigError("paper_strategy.window must be between 3 and 120")
    if not 2 <= strategy_persistence_ticks <= 12:
        raise ConfigError("paper_strategy.persistence_ticks must be between 2 and 12")
    if strategy_persistence_ticks > strategy_window:
        raise ConfigError("paper_strategy.persistence_ticks must not exceed window")
    if strategy_regime_window <= strategy_window:
        raise ConfigError("paper_strategy.regime_window must exceed window")
    if strategy_regime_window > 360:
        raise ConfigError("paper_strategy.regime_window must not exceed 360")
    if strategy_slippage_bps < 0 or strategy_slippage_bps > 10:
        raise ConfigError("paper_strategy.slippage_bps must be between 0 and 10")
    minimum_cost_hurdle = Decimal("10") + strategy_slippage_bps * 2
    if strategy_entry_threshold_bps < minimum_cost_hurdle:
        raise ConfigError("paper_strategy.entry_threshold_bps must cover fees and slippage")
    if not Decimal("5") <= strategy_minimum_net_edge_bps <= strategy_take_profit_bps:
        raise ConfigError(
            "paper_strategy.minimum_net_edge_bps must be between 5 and take_profit_bps"
        )
    if not Decimal("0.50") <= strategy_minimum_confidence <= 1:
        raise ConfigError("paper_strategy.minimum_confidence must be between 0.50 and 1.00")
    if strategy_stop_loss_bps <= 0 or strategy_take_profit_bps <= 0:
        raise ConfigError("paper strategy stop and take-profit must be positive")
    if strategy_max_hold_seconds <= market_poll_seconds:
        raise ConfigError("paper_strategy.max_hold_seconds must exceed the poll interval")
    if strategy_cooldown_seconds < market_poll_seconds:
        raise ConfigError("paper_strategy.cooldown_seconds must cover at least one poll interval")
    if strategy_daily_loss_usdt <= 0 or strategy_daily_loss_usdt > starting_balance / 20:
        raise ConfigError(
            "paper_strategy.daily_loss_usdt must be positive and at most 5% of capital"
        )
    if strategy_max_drawdown_pct <= 0 or strategy_max_drawdown_pct > 5:
        raise ConfigError("paper_strategy.max_drawdown_pct must be positive and at most 5%")
    if xau_settings.execution_symbol != "XAU_USDT":
        raise ConfigError("xau.execution_symbol must be XAU_USDT in Phase 1")
    if set(xau_settings.confirmation_symbols) - {"XAUT_USDT", "PAXG_USDT"}:
        raise ConfigError("XAU confirmation symbols may only be XAUT_USDT and PAXG_USDT")
    if not 2 <= xau_settings.short_window < xau_settings.trend_window <= 120:
        raise ConfigError("XAU windows must satisfy 2 <= short_window < trend_window <= 120")
    if xau_settings.stale_after_seconds <= market_poll_seconds:
        raise ConfigError("xau.stale_after_seconds must exceed the market poll interval")
    if xau_settings.entry_threshold_bps <= 0 or xau_settings.abnormal_divergence_bps <= 0:
        raise ConfigError("XAU entry and divergence thresholds must be positive")
    if not Decimal("0.50") <= xau_settings.minimum_confidence <= 1:
        raise ConfigError("xau.risk.minimum_confidence must be between 0.50 and 1.00")
    if xau_settings.maximum_spread_bps <= 0:
        raise ConfigError("xau.risk.maximum_spread_bps must be positive")
    if xau_settings.slippage_bps < 0 or xau_settings.slippage_bps > 10:
        raise ConfigError("xau.risk.slippage_bps must be between 0 and 10")
    if xau_settings.maximum_leverage <= 0 or xau_settings.maximum_leverage > 1:
        raise ConfigError("Phase 1 XAU leverage must be greater than 0 and no more than 1x")
    if (
        xau_settings.maximum_position_notional_usdt <= 0
        or xau_settings.maximum_position_notional_usdt > starting_balance / 4
    ):
        raise ConfigError("XAU maximum position must be positive and at most 25% of capital")
    if (
        xau_settings.risk_per_trade_usdt <= 0
        or xau_settings.maximum_daily_loss_usdt <= 0
        or xau_settings.maximum_daily_loss_usdt > strategy_daily_loss_usdt
    ):
        raise ConfigError("XAU risk budgets must be positive and not exceed the global daily limit")
    if xau_settings.maximum_consecutive_losses < 1:
        raise ConfigError("xau.risk.maximum_consecutive_losses must be positive")
    if (
        xau_settings.stop_loss_method != "FIXED_BPS"
        or xau_settings.take_profit_method != "FIXED_BPS"
    ):
        raise ConfigError("Phase 1 XAU stop and take-profit methods must be FIXED_BPS")
    if min(xau_settings.stop_loss_bps, xau_settings.take_profit_bps) <= 0:
        raise ConfigError("XAU stop and take-profit distances must be positive")
    if xau_settings.cooldown_seconds < market_poll_seconds:
        raise ConfigError("xau.risk.cooldown_seconds must cover one poll interval")
    if xau_settings.maximum_holding_seconds <= market_poll_seconds:
        raise ConfigError("xau.risk.maximum_holding_seconds must exceed one poll interval")
    if not all(
        Decimal(0) < value <= Decimal(1)
        for value in (
            xau_settings.high_volatility_size_factor,
            xau_settings.degraded_confirmation_size_factor,
        )
    ):
        raise ConfigError("XAU size factors must be greater than 0 and no more than 1")
    if ama_control_settings.execution_enabled:
        raise ConfigError("xau.ama_control.execution_enabled must remain false")
    if not (
        2
        <= ama_control_settings.fast_period
        < ama_control_settings.control_period
        < ama_control_settings.slow_period
        <= 240
    ):
        raise ConfigError("AMA periods must satisfy 2 <= fast < control < slow <= 240")
    if not 1 <= ama_control_settings.kama_fast < ama_control_settings.kama_slow <= 240:
        raise ConfigError("AMA KAMA smoothing periods are invalid")
    if not 2 <= ama_control_settings.atr_proxy_window <= 240:
        raise ConfigError("AMA atr_proxy_window must be between 2 and 240")
    if (
        min(
            ama_control_settings.atr_multiplier,
            ama_control_settings.minimum_hysteresis_bps,
            ama_control_settings.minimum_slope_bps,
            ama_control_settings.maximum_distance_bps,
            ama_control_settings.minimum_net_edge_bps,
            ama_control_settings.minimum_confidence,
            ama_control_settings.maximum_spread_bps,
            ama_control_settings.maximum_reference_dispersion_bps,
            ama_control_settings.maximum_reference_dislocation_bps,
            ama_control_settings.round_trip_fee_bps,
            ama_control_settings.stressed_cost_multiplier,
        )
        <= 0
    ):
        raise ConfigError("AMA thresholds and cost assumptions must be positive")
    if ama_control_settings.maximum_distance_bps <= ama_control_settings.minimum_hysteresis_bps:
        raise ConfigError("AMA maximum_distance_bps must exceed minimum_hysteresis_bps")
    if ama_control_settings.minimum_confidence > 1:
        raise ConfigError("AMA minimum_confidence must be no more than 1")
    if not 1 <= ama_control_settings.minimum_regime_observations <= 20:
        raise ConfigError("AMA minimum_regime_observations must be between 1 and 20")
    if ama_control_settings.minimum_quote_volume_usdt < 0:
        raise ConfigError("AMA minimum_quote_volume_usdt must not be negative")
    if ama_control_settings.notional_usdt <= 0 or (
        ama_control_settings.notional_usdt > xau_settings.maximum_position_notional_usdt
    ):
        raise ConfigError("AMA shadow notional must be positive and no larger than XAU PAPER cap")
    if ama_control_settings.maximum_holding_seconds <= market_poll_seconds:
        raise ConfigError("AMA maximum_holding_seconds must exceed the poll interval")
    if (
        ama_control_settings.slippage_bps_per_side < 0
        or ama_control_settings.funding_buffer_bps < 0
    ):
        raise ConfigError("AMA slippage and funding buffers must not be negative")
    if (
        min(
            ama_control_settings.minimum_comparison_trades,
            ama_control_settings.quarantine_minimum_trades,
            ama_control_settings.decision_interval_seconds,
            ama_control_settings.report_interval_seconds,
        )
        < 1
    ):
        raise ConfigError("AMA sample, decision, and report intervals must be positive")
    if ama_control_settings.quarantine_profit_factor <= 0:
        raise ConfigError("AMA quarantine profit factor must be positive")
    if not 1 <= entry_v3_settings.book_depth <= 20:
        raise ConfigError("entry_v3.book.depth must be between 1 and 20")
    if entry_v3_settings.execution_enabled:
        raise ConfigError("entry_v3.execution_enabled must remain false in PAPER shadow mode")
    if not 32 <= entry_v3_settings.feature_window_events <= 4096:
        raise ConfigError("entry_v3.feature_window_events must be between 32 and 4096")
    if not 1000 <= entry_v3_settings.feature_window_ms <= 60000:
        raise ConfigError("entry_v3.feature_window_ms must be between 1000 and 60000")
    if not 100 <= entry_v3_settings.impulse_window_ms < entry_v3_settings.feature_window_ms:
        raise ConfigError("entry_v3.impulse.window_ms must be shorter than the feature window")
    if entry_v3_settings.impulse_min_bps <= 0:
        raise ConfigError("entry_v3.impulse.minimum_bps must be positive")
    if (
        not Decimal("0")
        < entry_v3_settings.pullback_min_ratio
        < (entry_v3_settings.pullback_max_ratio)
        < Decimal("1")
    ):
        raise ConfigError("entry_v3 pullback ratios must satisfy 0 < minimum < maximum < 1")
    if entry_v3_settings.reacceleration_bps <= 0:
        raise ConfigError("entry_v3.pullback.reacceleration_bps must be positive")
    if not all(
        Decimal("0") <= value <= Decimal("1")
        for value in (
            entry_v3_settings.flow_confirmation,
            entry_v3_settings.breakout_flow,
            entry_v3_settings.book_confirmation,
            entry_v3_settings.continuation_fraction,
        )
    ):
        raise ConfigError("entry_v3 normalized thresholds must be between 0 and 1")
    if entry_v3_settings.breakout_flow < entry_v3_settings.flow_confirmation:
        raise ConfigError("entry_v3 breakout flow must be at least the normal confirmation")
    if entry_v3_settings.maximum_spread_bps <= 0:
        raise ConfigError("entry_v3.book.maximum_spread_bps must be positive")
    if entry_v3_settings.minimum_net_edge_bps < 5:
        raise ConfigError("entry_v3.edge.minimum_net_edge_bps must be at least 5")
    if not 1 <= entry_v3_settings.observed_latency_cap_ms <= 5000:
        raise ConfigError("entry_v3.edge.observed_latency_cap_ms must be between 1 and 5000")
    if not 1000 <= entry_v3_settings.whipsaw_block_ms <= 300000:
        raise ConfigError("entry_v3.whipsaw.block_ms must be between 1000 and 300000")
    if not 1 <= entry_v3_settings.maximum_reversals <= 20:
        raise ConfigError("entry_v3.whipsaw.maximum_reversals must be between 1 and 20")
    if not 4 <= entry_v3_settings.minimum_events <= entry_v3_settings.feature_window_events:
        raise ConfigError("entry_v3.minimum_events is outside the feature window")
    if experiment_settings.risk_budget_usdt <= 0:
        raise ConfigError("experiments.risk_budget_usdt must be positive")
    if not all(
        Decimal(0) < value <= strategy_notional_usdt
        for value in (
            experiment_settings.maximum_position_notional_usdt,
            experiment_settings.maximum_symbol_exposure_usdt,
            experiment_settings.liquidity_notional_cap_usdt,
        )
    ):
        raise ConfigError("experiment notional caps must be positive and no larger than baseline")
    if experiment_settings.maximum_symbol_attempts < 0:
        raise ConfigError("experiments.maximum_symbol_attempts must not be negative")
    if experiment_settings.attempt_window_seconds < market_poll_seconds:
        raise ConfigError("experiments.attempt_window_seconds must cover one poll interval")
    if experiment_settings.maximum_consecutive_symbol_losses < 0:
        raise ConfigError("experiments.maximum_consecutive_symbol_losses must not be negative")
    if (
        experiment_settings.maximum_symbol_loss_utc_day_usdt < 0
        or experiment_settings.maximum_symbol_loss_wib_day_usdt < 0
    ):
        raise ConfigError("experiment symbol-loss limits must not be negative")
    if not 1 <= experiment_settings.candidate_top_n <= active_symbols:
        raise ConfigError("experiments.candidate_top_n must be between 1 and active_symbols")
    if experiment_settings.cost_multiplier < 1:
        raise ConfigError("experiments.cost_multiplier must be at least 1")
    if experiment_settings.maximum_entry_spread_bps <= 0:
        raise ConfigError("experiments.maximum_entry_spread_bps must be positive")
    if (
        experiment_settings.minimum_entry_quote_volume < 0
        or experiment_settings.minimum_depth_usdt < 0
    ):
        raise ConfigError("experiment liquidity limits must not be negative")
    if (
        experiment_settings.maximum_one_bar_volatility_bps < 0
        or experiment_settings.maximum_price_gap_bps < 0
    ):
        raise ConfigError("experiment volatility and gap limits must not be negative")
    if experiment_settings.exit_variant not in {
        "BASELINE_FULL_TP",
        "TP75_RUNNER25",
        "TP50_RUNNER50",
    }:
        raise ConfigError("experiments.exit_variant is unsupported")
    if experiment_settings.runner_trail_bps <= 0:
        raise ConfigError("experiments.runner_trail_bps must be positive")
    if experiment_settings.runner_max_hold_seconds < strategy_max_hold_seconds:
        raise ConfigError("experiments.runner_max_hold_seconds must not shorten baseline hold time")
    if dashboard_host != "127.0.0.1":
        raise ConfigError("Phase 1 dashboard must bind to 127.0.0.1")
    if not 1024 <= dashboard_port <= 65535:
        raise ConfigError("dashboard.port must be between 1024 and 65535")
    if tradingview_confirmation_mode not in {"off", "async", "borderline"}:
        raise ConfigError("TRADINGVIEW_CONFIRMATION_MODE must be off, async, or borderline")
    if tradingview_weight < 0 or tradingview_weight > Decimal("0.20"):
        raise ConfigError("TRADINGVIEW_WEIGHT must be between 0 and 0.20")
    if not 250 <= tradingview_timeout_ms <= 5000:
        raise ConfigError("TRADINGVIEW_TIMEOUT_MS must be between 250 and 5000")
    if not 1 <= tradingview_cache_ttl_seconds <= 300:
        raise ConfigError("tradingview.cache_ttl_seconds must be between 1 and 300")
    if not 5 <= tradingview_poll_seconds <= 300:
        raise ConfigError("tradingview.poll_seconds must be between 5 and 300")
    if tradingview_stale_after_seconds <= tradingview_poll_seconds:
        raise ConfigError("tradingview.stale_after_seconds must exceed poll_seconds")
    if not re.fullmatch(r"(?:[1-9][0-9]{0,3}|[DWM])", tradingview_expected_timeframe):
        raise ConfigError("tradingview.expected_timeframe is invalid")
    if tradingview_screenshot_enabled:
        raise ConfigError("TradingView screenshots are disabled in Phase 1")

    return Settings(
        mode=mode,
        live_trading_enabled=live_enabled,
        live_confirmation=live_confirmation,
        venue=venue,
        trader_id=trader_id,
        starting_balance_usdt=starting_balance,
        leverage=leverage,
        log_console_level=log_console_level,
        log_file_level=log_file_level,
        log_directory=log_directory.resolve(),
        log_file_name=log_file_name,
        log_file_max_size=log_file_max_size,
        log_file_max_backup_count=log_file_max_backup_count,
        market_poll_seconds=market_poll_seconds,
        market_stale_after_seconds=market_stale_after_seconds,
        active_symbols=active_symbols,
        minimum_quote_volume=minimum_quote_volume,
        maximum_spread_bps=maximum_spread_bps,
        strategy_enabled=strategy_enabled,
        strategy_symbol=strategy_symbol,
        strategy_notional_usdt=strategy_notional_usdt,
        strategy_window=strategy_window,
        strategy_persistence_ticks=strategy_persistence_ticks,
        strategy_regime_window=strategy_regime_window,
        strategy_entry_threshold_bps=strategy_entry_threshold_bps,
        strategy_minimum_net_edge_bps=strategy_minimum_net_edge_bps,
        strategy_minimum_confidence=strategy_minimum_confidence,
        strategy_stop_loss_bps=strategy_stop_loss_bps,
        strategy_take_profit_bps=strategy_take_profit_bps,
        strategy_max_hold_seconds=strategy_max_hold_seconds,
        strategy_cooldown_seconds=strategy_cooldown_seconds,
        strategy_slippage_bps=strategy_slippage_bps,
        strategy_daily_loss_usdt=strategy_daily_loss_usdt,
        strategy_max_drawdown_pct=strategy_max_drawdown_pct,
        market_scope=market_scope,
        mode_switch_policy=mode_switch_policy,
        xau=xau_settings,
        ama_control=ama_control_settings,
        entry_v3=entry_v3_settings,
        experiments=experiment_settings,
        dashboard_host=dashboard_host,
        dashboard_port=dashboard_port,
        tradingview_enabled=tradingview_enabled,
        tradingview_confirmation_mode=tradingview_confirmation_mode,
        tradingview_weight=tradingview_weight,
        tradingview_timeout_ms=tradingview_timeout_ms,
        tradingview_cache_enabled=tradingview_cache_enabled,
        tradingview_cache_ttl_seconds=tradingview_cache_ttl_seconds,
        tradingview_poll_seconds=tradingview_poll_seconds,
        tradingview_stale_after_seconds=tradingview_stale_after_seconds,
        tradingview_expected_timeframe=tradingview_expected_timeframe,
        tradingview_pine_enabled=tradingview_pine_enabled,
        tradingview_screenshot_enabled=tradingview_screenshot_enabled,
    )
