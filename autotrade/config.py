from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "paper.toml"


class ConfigError(ValueError):
    """Raised when configuration is missing or violates Phase 1 safety."""


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
    dashboard = _table(document, "dashboard")
    tradingview = _optional_table(document, "tradingview")

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
    log_file_max_backup_count = _integer(
        logging.get("max_backup_count", 5), "max_backup_count"
    )
    market_poll_seconds = _integer(market_data.get("poll_seconds", 5), "poll_seconds")
    market_stale_after_seconds = _integer(
        market_data.get("stale_after_seconds", 15), "stale_after_seconds"
    )
    active_symbols = _integer(market_data.get("active_symbols", 8), "active_symbols")
    minimum_quote_volume = _decimal(
        market_data.get("minimum_quote_volume", "5000000"), "minimum_quote_volume"
    )
    maximum_spread_bps = _decimal(
        market_data.get("maximum_spread_bps", "12"), "maximum_spread_bps"
    )
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
    dashboard_host = str(dashboard.get("host", "127.0.0.1")).strip()
    dashboard_port = _integer(dashboard.get("port", 8765), "dashboard.port")
    tradingview_enabled = _boolean(
        os.getenv("TRADINGVIEW_ENABLED", tradingview.get("enabled", False)),
        "TRADINGVIEW_ENABLED",
    )
    tradingview_confirmation_mode = os.getenv(
        "TRADINGVIEW_CONFIRMATION_MODE",
        str(tradingview.get("confirmation_mode", "borderline")),
    ).strip().lower()
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
    tradingview_expected_timeframe = str(
        tradingview.get("expected_timeframe", "5")
    ).strip().upper()
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
