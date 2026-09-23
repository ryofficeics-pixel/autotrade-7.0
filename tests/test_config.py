from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autotrade.config import ConfigError, Settings, load_settings

BASE_CONFIG = """
[trading]
mode = "PAPER"
live_trading_enabled = false
live_confirmation = ""
venue = "GATE"
trader_id = "TESTER-001"
starting_balance_usdt = "300"
leverage = "1"

[logging]
console_level = "WARNING"
file_level = "INFO"
directory = "logs"
file_name = "engine"
max_file_size_bytes = 10000000
max_backup_count = 5

[market_data]
poll_seconds = 5
stale_after_seconds = 15
active_symbols = 8
minimum_quote_volume = "5000000"
maximum_spread_bps = "12"

[paper_strategy]
enabled = true
symbol = "ETH_USDT"
notional_usdt = "30"
window = 12
persistence_ticks = 5
regime_window = 36
entry_threshold_bps = "20"
minimum_net_edge_bps = "12"
minimum_confidence = "0.60"
stop_loss_bps = "35"
take_profit_bps = "55"
max_hold_seconds = 180
cooldown_seconds = 60
slippage_bps = "2"
daily_loss_usdt = "6"
max_drawdown_pct = "3"

[dashboard]
host = "127.0.0.1"
port = 8765
"""

SAFE_ENV = {
    "TRADING_MODE": "PAPER",
    "LIVE_TRADING_ENABLED": "false",
    "LIVE_CONFIRMATION": "",
}


class SettingsTests(unittest.TestCase):
    def load(self, config: str = BASE_CONFIG, env: dict[str, str] = SAFE_ENV) -> Settings:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.toml"
            path.write_text(config, encoding="utf-8")
            with patch.dict(os.environ, env, clear=True):
                return load_settings(path)

    def test_safe_defaults_load(self) -> None:
        settings = self.load()
        self.assertEqual(settings.mode, "PAPER")
        self.assertFalse(settings.live_trading_enabled)
        self.assertEqual(str(settings.starting_balance_usdt), "300")
        self.assertEqual(str(settings.leverage), "1")
        self.assertTrue(settings.strategy_enabled)
        self.assertEqual(settings.strategy_symbol, "ETH_USDT")
        self.assertEqual(settings.strategy_persistence_ticks, 5)
        self.assertEqual(settings.strategy_regime_window, 36)
        self.assertEqual(str(settings.strategy_minimum_net_edge_bps), "12")
        self.assertEqual(str(settings.strategy_minimum_confidence), "0.60")
        self.assertTrue(settings.strategy_evidence_halt_enabled)
        self.assertEqual(settings.strategy_evidence_minimum_trades, 100)
        self.assertEqual(settings.strategy_evidence_minimum_trading_days, 10)
        self.assertEqual(str(settings.strategy_evidence_profit_factor_floor), "0.80")
        self.assertFalse(settings.tradingview_enabled)
        self.assertEqual(settings.tradingview_confirmation_mode, "borderline")
        self.assertEqual(str(settings.tradingview_weight), "0.20")
        self.assertTrue(settings.entry_v3.enabled)
        self.assertTrue(settings.entry_v3.shadow_enabled)
        self.assertFalse(settings.entry_v3.execution_enabled)
        self.assertTrue(settings.ama_control.enabled)
        self.assertFalse(settings.ama_control.execution_enabled)

    def test_entry_v3_execution_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "execution_enabled must remain false"):
            self.load(BASE_CONFIG + '\n[paper_strategy.entry_v3]\nexecution_enabled = true\n')

    def test_strategy_edge_must_cover_fees_and_slippage(self) -> None:
        with self.assertRaisesRegex(ConfigError, "must cover fees and slippage"):
            self.load(
                BASE_CONFIG.replace(
                    'entry_threshold_bps = "20"', 'entry_threshold_bps = "10"'
                )
            )

    def test_minimum_net_edge_cannot_be_weakened_below_five_bps(self) -> None:
        with self.assertRaisesRegex(ConfigError, "between 5 and take_profit_bps"):
            self.load(
                BASE_CONFIG.replace(
                    'minimum_net_edge_bps = "12"', 'minimum_net_edge_bps = "4.99"'
                )
            )

    def test_minimum_confidence_cannot_be_weakened_below_half(self) -> None:
        with self.assertRaisesRegex(ConfigError, "between 0.50 and 1.00"):
            self.load(
                BASE_CONFIG.replace(
                    'minimum_confidence = "0.60"', 'minimum_confidence = "0.49"'
                )
            )

    def test_evidence_halt_requires_meaningful_sample(self) -> None:
        config = BASE_CONFIG + """

[paper_strategy.evidence_halt]
minimum_trades = 99
"""
        with self.assertRaisesRegex(ConfigError, "minimum_trades must be at least 100"):
            self.load(config)

    def test_evidence_halt_profit_factor_floor_cannot_exceed_one(self) -> None:
        config = BASE_CONFIG + """

[paper_strategy.evidence_halt]
profit_factor_floor = "1.01"
"""
        with self.assertRaisesRegex(ConfigError, "profit_factor_floor"):
            self.load(config)

    def test_non_paper_mode_is_rejected(self) -> None:
        with (
            patch.dict(os.environ, {**SAFE_ENV, "TRADING_MODE": "LIVE"}),
            self.assertRaisesRegex(ConfigError, "TRADING_MODE=PAPER"),
        ):
            load_settings()

    def test_live_flag_is_rejected(self) -> None:
        with (
            patch.dict(os.environ, {**SAFE_ENV, "LIVE_TRADING_ENABLED": "true"}),
            self.assertRaisesRegex(ConfigError, "LIVE_TRADING_ENABLED=false"),
        ):
            load_settings()

    def test_live_confirmation_is_rejected(self) -> None:
        with (
            patch.dict(os.environ, {**SAFE_ENV, "LIVE_CONFIRMATION": "ENABLE"}),
            self.assertRaisesRegex(ConfigError, "LIVE_CONFIRMATION must be empty"),
        ):
            load_settings()

    def test_file_cannot_enable_live(self) -> None:
        unsafe = BASE_CONFIG.replace(
            "live_trading_enabled = false",
            "live_trading_enabled = true",
        )
        with self.assertRaisesRegex(ConfigError, "LIVE_TRADING_ENABLED=false"):
            self.load(unsafe, env={})

    def test_leverage_above_one_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "no more than 1x"):
            self.load(BASE_CONFIG.replace('leverage = "1"', 'leverage = "2"'))

    def test_non_positive_capital_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "must be positive"):
            self.load(
                BASE_CONFIG.replace(
                    'starting_balance_usdt = "300"',
                    'starting_balance_usdt = "0"',
                )
            )

    def test_public_dashboard_bind_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "must bind to 127.0.0.1"):
            self.load(BASE_CONFIG.replace('host = "127.0.0.1"', 'host = "0.0.0.0"'))

    def test_missing_logging_section_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, r"missing \[logging\]"):
            self.load(BASE_CONFIG.split("[logging]")[0])

    def test_tradingview_weight_cannot_exceed_secondary_limit(self) -> None:
        with self.assertRaisesRegex(ConfigError, "between 0 and 0.20"):
            self.load(env={**SAFE_ENV, "TRADINGVIEW_WEIGHT": "0.50"})

    def test_tradingview_screenshots_are_fail_closed(self) -> None:
        with self.assertRaisesRegex(ConfigError, "screenshots are disabled"):
            self.load(env={**SAFE_ENV, "TRADINGVIEW_SCREENSHOT_ENABLED": "true"})


if __name__ == "__main__":
    unittest.main()
