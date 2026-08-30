from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path

from autotrade.capture import capture_dataset, verify_dataset
from autotrade.config import DEFAULT_CONFIG_PATH, ConfigError, Settings, load_settings
from autotrade.dashboard import fetch_gate_tickers, rank_tickers, run_dashboard
from autotrade.runtime import run_paper_smoke


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 1 paper application")
    parser.add_argument(
        "command",
        choices=("smoke", "dashboard", "capture", "replay-verify"),
        nargs="?",
        default="smoke",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--port", type=int)
    parser.add_argument("--symbols", help="Comma-separated Gate USDT perpetual symbols")
    parser.add_argument("--duration", type=int, default=3600)
    parser.add_argument("--output", type=Path, default=Path("data/gate-captures"))
    parser.add_argument("--dataset", type=Path)
    arguments = parser.parse_args(argv)

    try:
        settings = load_settings(arguments.config)
        if arguments.port is not None:
            if not 1024 <= arguments.port <= 65535:
                raise ConfigError("--port must be between 1024 and 65535")
            settings = replace(settings, dashboard_port=arguments.port)
        if arguments.command == "dashboard":
            run_dashboard(settings)
            return 0
        if arguments.command == "capture":
            symbols = _capture_symbols(arguments.symbols, settings)
            manifest_path = capture_dataset(
                symbols=symbols,
                output_root=arguments.output,
                duration_seconds=arguments.duration,
                project_root=Path.cwd(),
                config_path=arguments.config,
            )
            print(manifest_path.read_text(encoding="utf-8"))
            return 0
        if arguments.command == "replay-verify":
            if arguments.dataset is None:
                raise ConfigError("--dataset is required for replay-verify")
            print(json.dumps(verify_dataset(arguments.dataset), sort_keys=True))
            return 0
        report = run_paper_smoke(settings)
    except KeyboardInterrupt:
        return 0
    except (ConfigError, RuntimeError, OSError, ValueError) as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(asdict(report), sort_keys=True))
    return 0


def _capture_symbols(raw: str | None, settings: Settings) -> tuple[str, ...]:
    if raw:
        symbols = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    else:
        payload = fetch_gate_tickers()
        markets = rank_tickers(payload, settings)
        symbols = tuple(str(market["symbol"]) for market in markets if market.get("selected"))
    if not symbols:
        raise ConfigError("capture universe is empty")
    return symbols


if __name__ == "__main__":
    raise SystemExit(main())
