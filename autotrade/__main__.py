from __future__ import annotations

import argparse
import json
import socket
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path

from autotrade.capture import capture_dataset, verify_dataset
from autotrade.config import DEFAULT_CONFIG_PATH, ConfigError, Settings, load_settings
from autotrade.dashboard import fetch_gate_tickers, rank_tickers, run_dashboard
from autotrade.integrity import create_new_run
from autotrade.runtime import run_paper_smoke


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 1 paper application")
    parser.add_argument(
        "command",
        choices=("smoke", "dashboard", "capture", "replay-verify", "new-paper-run"),
        nargs="?",
        default="smoke",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--port", type=int)
    parser.add_argument("--symbols", help="Comma-separated Gate USDT perpetual symbols")
    parser.add_argument("--duration", type=int, default=3600)
    parser.add_argument("--output", type=Path, default=Path("data/gate-captures"))
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--confirm-new-run", action="store_true")
    arguments = parser.parse_args(argv)

    try:
        settings = load_settings(arguments.config)
        if arguments.port is not None:
            if not 1024 <= arguments.port <= 65535:
                raise ConfigError("--port must be between 1024 and 65535")
            settings = replace(settings, dashboard_port=arguments.port)
        if arguments.command == "dashboard":
            run_dashboard(settings, arguments.config)
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
        if arguments.command == "new-paper-run":
            if not arguments.confirm_new_run:
                raise ConfigError("new-paper-run requires --confirm-new-run")
            if _port_is_open(settings.dashboard_host, settings.dashboard_port):
                raise ConfigError("stop the dashboard before creating a new paper run")
            config_path = arguments.config.resolve()
            metadata = create_new_run(
                project_root=Path(__file__).resolve().parents[1],
                config_path=config_path,
                log_directory=settings.log_directory,
                starting_equity=settings.starting_balance_usdt,
            )
            print(json.dumps(metadata, sort_keys=True))
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


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
