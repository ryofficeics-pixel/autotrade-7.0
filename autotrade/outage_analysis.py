from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from autotrade.time_utils import canonical_utc, parse_timestamp, utc_to_wib

GATE_FUTURES_CANDLES = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"


def fetch_public_candles(
    contract: str,
    start_utc: str,
    end_utc: str,
    *,
    interval: str = "10s",
) -> list[dict[str, object]]:
    query = urllib.parse.urlencode(
        {
            "contract": contract,
            "from": int(parse_timestamp(start_utc).timestamp()),
            "to": int(parse_timestamp(end_utc).timestamp()),
            "interval": interval,
        }
    )
    request = urllib.request.Request(
        f"{GATE_FUTURES_CANDLES}?{query}",
        headers={"User-Agent": "autotrade-paper-audit/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise ValueError("Gate candle response is not a list")
    return [item for item in payload if isinstance(item, dict)]


def analyze_long_outage(
    candles: Iterable[Mapping[str, object]],
    *,
    entry_utc: str,
    entry_price: Decimal,
    quantity: Decimal,
    entry_fee_usdt: Decimal,
    stop_loss_bps: Decimal,
    take_profit_bps: Decimal,
    slippage_bps: Decimal,
    price_increment: Decimal,
    recovery_pnl_usdt: Decimal,
) -> dict[str, object]:
    entry_time = parse_timestamp(entry_utc)
    rows = sorted(candles, key=lambda item: int(str(item["t"])))
    if not rows:
        raise ValueError("no candles supplied")
    stop_price = entry_price * (Decimal(1) - stop_loss_bps / Decimal(10_000))
    take_profit_price = entry_price * (Decimal(1) + take_profit_bps / Decimal(10_000))
    first_stop: Mapping[str, object] | None = None
    first_take_profit: Mapping[str, object] | None = None
    maximum = max(rows, key=lambda item: Decimal(str(item["h"])))
    minimum = min(rows, key=lambda item: Decimal(str(item["l"])))
    for row in rows:
        if first_stop is None and Decimal(str(row["l"])) <= stop_price:
            first_stop = row
        if first_take_profit is None and Decimal(str(row["h"])) >= take_profit_price:
            first_take_profit = row
    first_stop_time = (
        datetime.fromtimestamp(int(str(first_stop["t"])), tz=entry_time.tzinfo)
        if first_stop is not None
        else None
    )
    first_take_profit_time = (
        datetime.fromtimestamp(int(str(first_take_profit["t"])), tz=entry_time.tzinfo)
        if first_take_profit is not None
        else None
    )
    ordering = (
        "TAKE_PROFIT_FIRST"
        if first_take_profit_time is not None
        and (first_stop_time is None or first_take_profit_time < first_stop_time)
        else "STOP_FIRST"
        if first_stop_time is not None
        and (first_take_profit_time is None or first_stop_time < first_take_profit_time)
        else "SAME_CANDLE_AMBIGUOUS"
        if first_stop_time is not None and first_take_profit_time is not None
        else "NO_TRIGGER"
    )
    trigger = take_profit_price if ordering == "TAKE_PROFIT_FIRST" else stop_price
    modeled_exit = (
        trigger * (Decimal(1) - slippage_bps / Decimal(10_000)) / price_increment
    ).to_integral_value(rounding=ROUND_DOWN) * price_increment
    exit_fee = modeled_exit * quantity * Decimal("0.0005")
    expected_pnl = (modeled_exit - entry_price) * quantity - entry_fee_usdt - exit_fee
    maximum_price = Decimal(str(maximum["h"]))
    minimum_price = Decimal(str(minimum["l"]))
    mfe_time = datetime.fromtimestamp(int(str(maximum["t"])), tz=entry_time.tzinfo)
    mae_time = datetime.fromtimestamp(int(str(minimum["t"])), tz=entry_time.tzinfo)
    return {
        "entry_utc": canonical_utc(entry_time),
        "entry_wib": utc_to_wib(entry_time),
        "candle_count": len(rows),
        "interval": "10s",
        "stop_trigger_price": str(stop_price),
        "take_profit_trigger_price": str(take_profit_price),
        "first_stop_utc": canonical_utc(first_stop_time) if first_stop_time else None,
        "first_take_profit_utc": (
            canonical_utc(first_take_profit_time) if first_take_profit_time else None
        ),
        "trigger_order": ordering,
        "maximum_favorable_excursion_bps": str(
            (maximum_price / entry_price - Decimal(1)) * Decimal(10_000)
        ),
        "maximum_adverse_excursion_bps": str(
            (minimum_price / entry_price - Decimal(1)) * Decimal(10_000)
        ),
        "time_to_mfe_ms": round((mfe_time - entry_time).total_seconds() * 1000),
        "time_to_mae_ms": round((mae_time - entry_time).total_seconds() * 1000),
        "maximum_price": str(maximum_price),
        "minimum_price": str(minimum_price),
        "counterfactual_exit_price": str(modeled_exit),
        "counterfactual_net_pnl_usdt": str(expected_pnl),
        "incremental_outage_pnl_usdt": str(recovery_pnl_usdt - expected_pnl),
        "precision_warning": (
            "Candle highs and lows do not establish intrabar bid/ask order or an executable fill."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze the 15 September LSK PAPER outage")
    parser.add_argument("--start", default="2026-09-15T04:37:52Z")
    parser.add_argument("--end", default="2026-09-15T06:25:16Z")
    args = parser.parse_args()
    candles = fetch_public_candles("LSK_USDT", args.start, args.end)
    result = analyze_long_outage(
        candles,
        entry_utc="2026-09-15T04:37:52.269221Z",
        entry_price=Decimal("0.3705"),
        quantity=Decimal("84.245998"),
        entry_fee_usdt=Decimal("0.01560657"),
        stop_loss_bps=Decimal("35"),
        take_profit_bps=Decimal("55"),
        slippage_bps=Decimal("2"),
        price_increment=Decimal("0.0001"),
        recovery_pnl_usdt=Decimal("5.089537736096313720"),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
