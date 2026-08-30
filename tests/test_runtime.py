from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from autotrade.config import load_settings
from autotrade.runtime import run_paper_smoke

SAFE_ENV = {
    "TRADING_MODE": "PAPER",
    "LIVE_TRADING_ENABLED": "false",
    "LIVE_CONFIRMATION": "",
}


class PaperRuntimeTests(unittest.TestCase):
    def test_nautilus_paper_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            settings = replace(load_settings(), log_directory=Path(directory))
            report = run_paper_smoke(settings)
            logs = list(Path(directory).glob("engine*.jsonl"))

        self.assertEqual(report.mode, "PAPER")
        self.assertEqual(report.balance_usdt, "300")
        self.assertEqual(report.leverage, "1")
        self.assertTrue(report.risk_engine_enabled)
        self.assertTrue(report.market_event_processed)
        self.assertTrue(logs)

    def test_runtime_has_no_live_execution_symbols(self) -> None:
        package = Path(__file__).resolve().parents[1] / "autotrade"
        source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
        for forbidden in ("TradingNode", "LiveExec", "LiveExecution", "adapters.gateio"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
