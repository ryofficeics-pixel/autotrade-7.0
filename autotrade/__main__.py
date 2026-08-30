from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path

from autotrade.config import DEFAULT_CONFIG_PATH, ConfigError, load_settings
from autotrade.dashboard import run_dashboard
from autotrade.runtime import run_paper_smoke


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 1 paper application")
    parser.add_argument("command", choices=("smoke", "dashboard"), nargs="?", default="smoke")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--port", type=int)
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
        report = run_paper_smoke(settings)
    except KeyboardInterrupt:
        return 0
    except (ConfigError, RuntimeError, OSError, ValueError) as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(asdict(report), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
