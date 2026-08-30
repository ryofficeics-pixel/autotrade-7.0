from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from time import time_ns

import nautilus_trader
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, RiskEngineConfig
from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money, Price, Quantity

from autotrade.config import Settings


@dataclass(frozen=True)
class RuntimeReport:
    mode: str
    nautilus_version: str
    trader_id: str
    venue: str
    balance_usdt: str
    leverage: str
    risk_engine_enabled: bool
    market_event_processed: bool


def _synthetic_market(venue: Venue) -> tuple[CryptoPerpetual, QuoteTick]:
    instrument_id = InstrumentId(Symbol("BTC_USDT-PERP"), venue)
    timestamp_ns = time_ns()
    instrument = CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol("BTC_USDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=timestamp_ns,
        ts_init=timestamp_ns,
        margin_init=Decimal("1"),
        margin_maint=Decimal("1"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
    )
    quote = QuoteTick(
        instrument_id=instrument_id,
        bid_price=Price.from_str("50000.0"),
        ask_price=Price.from_str("50000.1"),
        bid_size=Quantity.from_str("1.000"),
        ask_size=Quantity.from_str("1.000"),
        ts_event=timestamp_ns + 1,
        ts_init=timestamp_ns + 1,
    )
    return instrument, quote


def run_paper_smoke(settings: Settings) -> RuntimeReport:
    """Start Nautilus in simulation, process one quote, and shut down cleanly."""
    settings.log_directory.mkdir(parents=True, exist_ok=True)
    logging = LoggingConfig(
        log_level=settings.log_console_level,
        log_level_file=settings.log_file_level,
        log_directory=str(settings.log_directory),
        log_file_name=settings.log_file_name,
        log_file_format="json",
        log_file_max_size=settings.log_file_max_size,
        log_file_max_backup_count=settings.log_file_max_backup_count,
    )
    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId(settings.trader_id),
            logging=logging,
            risk_engine=RiskEngineConfig(bypass=False),
            run_analysis=False,
        )
    )
    venue = Venue(settings.venue)

    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USDT,
            starting_balances=[Money(settings.starting_balance_usdt, USDT)],
            default_leverage=settings.leverage,
        )
        instrument, quote = _synthetic_market(venue)
        engine.add_instrument(instrument)
        engine.add_data([quote])
        engine.run()

        account = engine.cache.account_for_venue(venue)
        if account is None:
            raise RuntimeError("Nautilus did not initialize the simulated Gate account")
        balance = account.balance_total(USDT)
        if balance is None or balance.as_decimal() != settings.starting_balance_usdt:
            raise RuntimeError("simulated account balance does not match configuration")
        processed_quote = engine.cache.quote_tick(instrument.id)
        if processed_quote is None:
            raise RuntimeError("Nautilus did not process the synthetic market event")
        if engine.kernel.risk_engine.is_bypassed:
            raise RuntimeError("Nautilus RiskEngine is unexpectedly bypassed")

        lifecycle_log = settings.log_directory / f"{settings.log_file_name}-lifecycle.jsonl"
        with lifecycle_log.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(
                    {
                        "event": "paper_smoke_complete",
                        "mode": settings.mode,
                        "risk_engine_enabled": True,
                        "market_event_processed": True,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )

        return RuntimeReport(
            mode=settings.mode,
            nautilus_version=nautilus_trader.__version__,
            trader_id=settings.trader_id,
            venue=settings.venue,
            balance_usdt=format(balance.as_decimal(), "f"),
            leverage=format(settings.leverage, "f"),
            risk_engine_enabled=True,
            market_event_processed=True,
        )
    finally:
        engine.dispose()
