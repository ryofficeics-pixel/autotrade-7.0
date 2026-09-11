from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autotrade.config import load_settings
from autotrade.entry_v3 import LiveEntryV3Shadow


class EntryV3ShadowTests(unittest.TestCase):
    def test_shadow_status_cannot_enable_orders(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TRADING_MODE": "PAPER",
                "LIVE_TRADING_ENABLED": "false",
                "LIVE_CONFIRMATION": "",
            },
            clear=True,
        ):
            settings = load_settings()
        with tempfile.TemporaryDirectory() as directory:
            shadow = LiveEntryV3Shadow(settings, ("ETH_USDT",), Path(directory))
            status = shadow.status()
            shadow.recorder.close(
                complete=False,
                connections=0,
                gaps=0,
                duplicates=0,
                resnapshots=0,
                error="test closed before capture",
            )
        self.assertEqual(status["strategy_status"], "SHADOW")
        self.assertFalse(status["execution_enabled"])
        self.assertEqual(status["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
