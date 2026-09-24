from __future__ import annotations

import hashlib
import json
import os
import time
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

GATE_API = "https://api.gateio.ws/api/v4/futures/usdt"
STABLE_BASES = {
    "BUSD",
    "DAI",
    "FDUSD",
    "PYUSD",
    "TUSD",
    "USDC",
    "USDE",
    "USD1",
    "USDP",
    "USDS",
    "USTC",
}
NON_CRYPTO_BASES = {"PAXG", "XAG", "XAU", "XAUT"}


@dataclass(frozen=True)
class Candle:
    timestamp: int
    symbol: str
    interval: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_contracts: Decimal
    quote_volume_usdt: Decimal


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str
    ).encode()


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if not number.is_finite():
        raise ValueError(f"invalid {field}")
    return number


def _request_json(path: str, query: Mapping[str, object] | None = None) -> object:
    url = f"{GATE_API}/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"User-Agent": "AUTOTRADE-7-research/1"})
    error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                if response.status != 200:
                    raise RuntimeError(f"Gate returned HTTP {response.status}")
                payload = response.read(20_000_001)
                if len(payload) > 20_000_000:
                    raise RuntimeError("Gate response exceeded safety limit")
                return json.loads(payload)
        except (OSError, ValueError, RuntimeError) as exc:
            error = exc
            if attempt < 3:
                time.sleep(0.25 * (2**attempt))
    raise RuntimeError(f"Gate public data request failed: {error}")


def _git_commit(project_root: Path) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def load_wide_config(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        return cast(dict[str, object], tomllib.load(stream))


def select_snapshot_universe(
    tickers: Sequence[Mapping[str, object]],
    contracts: Sequence[Mapping[str, object]],
    *,
    now_timestamp: int,
    universe_size: int,
    minimum_listing_days: int,
    minimum_quote_volume: Decimal,
    maximum_spread_bps: Decimal,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    contract_by_name = {str(row.get("name")): row for row in contracts}
    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    for ticker in tickers:
        symbol = str(ticker.get("contract", ""))
        if not symbol.endswith("_USDT"):
            continue
        contract = contract_by_name.get(symbol, {})
        base = symbol.removesuffix("_USDT")
        reasons: list[str] = []
        bid = _decimal(ticker.get("highest_bid", 0), "highest_bid")
        ask = _decimal(ticker.get("lowest_ask", 0), "lowest_ask")
        volume = _decimal(ticker.get("volume_24h_quote", 0), "volume_24h_quote")
        created = int(str(contract.get("create_time", 0) or 0))
        age_days = (now_timestamp - created) / 86400 if created else 0
        spread = (
            (ask - bid) / ((ask + bid) / Decimal(2)) * Decimal(10_000)
            if bid > 0 and ask > bid
            else Decimal("Infinity")
        )
        if base in STABLE_BASES or base in NON_CRYPTO_BASES:
            reasons.append("UNSUPPORTED_MARKET_CLASS")
        if contract.get("in_delisting") is True:
            reasons.append("INSTRUMENT_UNAVAILABLE")
        if age_days < minimum_listing_days:
            reasons.append("NEW_LISTING")
        if volume < minimum_quote_volume:
            reasons.append("LOW_LIQUIDITY")
        if not spread.is_finite() or spread > maximum_spread_bps:
            reasons.append("SPREAD_TOO_WIDE")
        row = {
            "symbol": symbol,
            "snapshot_quote_volume_usdt": float(volume),
            "snapshot_spread_bps": float(spread) if spread.is_finite() else None,
            "funding_rate": ticker.get("funding_rate"),
            "mark_price": ticker.get("mark_price"),
            "index_price": ticker.get("index_price"),
            "bid": ticker.get("highest_bid"),
            "ask": ticker.get("lowest_ask"),
            "listing_age_days": age_days,
            "tick_size": contract.get("order_price_round"),
            "lot_size": contract.get("quanto_multiplier"),
            "minimum_order_size": contract.get("order_size_min"),
            "tradability": "AVAILABLE" if not reasons else "REJECTED",
            "rejection_reasons": reasons,
            "historical_bbo": "UNAVAILABLE",
            "exchange_event_timestamp": "AVAILABLE_FOR_CANDLES_ONLY",
        }
        (rejected if reasons else accepted).append(row)
    accepted.sort(key=lambda row: float(str(row["snapshot_quote_volume_usdt"])), reverse=True)
    selected = accepted[:universe_size]
    for row in accepted[universe_size:]:
        row["tradability"] = "REJECTED"
        cast(list[str], row["rejection_reasons"]).append("OUTSIDE_UNIVERSE_CAP")
        rejected.append(row)
    return selected, sorted(rejected, key=lambda row: str(row["symbol"]))


def fetch_candles(symbol: str, interval: str, start: int, end: int) -> list[Candle]:
    seconds = {"15m": 900, "1h": 3600}[interval]
    chunk_seconds = seconds * 1900
    by_time: dict[int, Candle] = {}
    cursor = start - (start % seconds)
    while cursor <= end:
        stop = min(end, cursor + chunk_seconds)
        raw = _request_json(
            "candlesticks",
            {"contract": symbol, "interval": interval, "from": cursor, "to": stop},
        )
        if not isinstance(raw, list):
            raise ValueError(f"invalid candle response for {symbol} {interval}")
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError(f"invalid candle row for {symbol} {interval}")
            timestamp = int(str(item["t"]))
            candle = Candle(
                timestamp=timestamp,
                symbol=symbol,
                interval=interval,
                open=_decimal(item["o"], "open"),
                high=_decimal(item["h"], "high"),
                low=_decimal(item["l"], "low"),
                close=_decimal(item["c"], "close"),
                volume_contracts=_decimal(item["v"], "volume"),
                quote_volume_usdt=_decimal(item.get("sum", 0), "quote volume"),
            )
            if not (
                candle.low > 0
                and candle.low <= candle.open <= candle.high
                and candle.low <= candle.close <= candle.high
                and candle.volume_contracts >= 0
                and candle.quote_volume_usdt >= 0
            ):
                raise ValueError(f"invalid OHLCV for {symbol} at {timestamp}")
            by_time[timestamp] = candle
        cursor = stop + seconds
        time.sleep(0.03)
    return [by_time[key] for key in sorted(by_time) if start <= key <= end]


def aggregate_four_hour(candles: Sequence[Candle]) -> list[Candle]:
    groups: dict[int, list[Candle]] = {}
    for candle in candles:
        bucket = candle.timestamp - candle.timestamp % 14_400
        groups.setdefault(bucket, []).append(candle)
    result: list[Candle] = []
    for timestamp, rows in sorted(groups.items()):
        rows.sort(key=lambda row: row.timestamp)
        if len(rows) != 4 or any(
            rows[index].timestamp != timestamp + index * 3600 for index in range(4)
        ):
            continue
        result.append(
            Candle(
                timestamp=timestamp,
                symbol=rows[0].symbol,
                interval="4h",
                open=rows[0].open,
                high=max(row.high for row in rows),
                low=min(row.low for row in rows),
                close=rows[-1].close,
                volume_contracts=sum((row.volume_contracts for row in rows), Decimal(0)),
                quote_volume_usdt=sum((row.quote_volume_usdt for row in rows), Decimal(0)),
            )
        )
    return result


def _candle_record(candle: Candle) -> dict[str, object]:
    row = asdict(candle)
    return {key: str(value) if isinstance(value, Decimal) else value for key, value in row.items()}


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    with path.open("xb") as stream:
        for row in rows:
            stream.write(_canonical(row) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def freeze_wide_crypto_dataset(
    project_root: Path,
    *,
    config_path: Path,
    end_timestamp: int | None = None,
) -> dict[str, object]:
    config_bytes = config_path.read_bytes()
    config = load_wide_config(config_path)
    dataset_config = cast(dict[str, object], config["dataset"])
    now = end_timestamp or int(datetime.now(UTC).timestamp())
    end = now - now % 3600 - 3600
    start = end - int(str(dataset_config["lookback_days"])) * 86400
    tickers_raw = _request_json("tickers")
    contracts_raw = _request_json("contracts")
    if not isinstance(tickers_raw, list) or not isinstance(contracts_raw, list):
        raise ValueError("Gate universe responses are invalid")
    tickers = [cast(dict[str, object], row) for row in tickers_raw if isinstance(row, dict)]
    contracts = [cast(dict[str, object], row) for row in contracts_raw if isinstance(row, dict)]
    selected, rejected = select_snapshot_universe(
        tickers,
        contracts,
        now_timestamp=end,
        universe_size=int(str(dataset_config["universe_size"])),
        minimum_listing_days=int(str(dataset_config["minimum_listing_days"])),
        minimum_quote_volume=_decimal(
            dataset_config["minimum_snapshot_quote_volume_usdt"], "minimum quote volume"
        ),
        maximum_spread_bps=_decimal(
            dataset_config["maximum_snapshot_spread_bps"], "maximum spread"
        ),
    )
    if len(selected) < 15 or not any(row["symbol"] == "BTC_USDT" for row in selected):
        raise ValueError("eligible Gate universe is too small or lacks BTC_USDT")
    code_commit = _git_commit(project_root)
    acquisition_identity = {
        "family": dataset_config["family"],
        "start": start,
        "end": end,
        "symbols": [row["symbol"] for row in selected],
        "config_hash": _hash_bytes(config_bytes),
        "code_commit": code_commit,
        "source": "GATE_PUBLIC_API_V4",
    }
    short_identity = _hash_bytes(_canonical(acquisition_identity))[:24]
    dataset_id = f"wide-crypto-v1-{short_identity}"
    dataset_root = project_root / "research" / "datasets" / dataset_id
    if dataset_root.exists():
        return verify_wide_crypto_dataset(dataset_root)
    dataset_root.mkdir(parents=True, exist_ok=False)
    try:
        interval_rows: dict[str, list[Candle]] = {"15m": [], "1h": [], "4h": []}
        coverage: list[dict[str, object]] = []
        for market in selected:
            symbol = str(market["symbol"])
            candles_15m = fetch_candles(symbol, "15m", start, end)
            candles_1h = fetch_candles(symbol, "1h", start, end)
            candles_4h = aggregate_four_hour(candles_1h)
            interval_rows["15m"].extend(candles_15m)
            interval_rows["1h"].extend(candles_1h)
            interval_rows["4h"].extend(candles_4h)
            coverage.append(
                {
                    "symbol": symbol,
                    "rows_15m": len(candles_15m),
                    "rows_1h": len(candles_1h),
                    "rows_4h": len(candles_4h),
                    "first_1h": candles_1h[0].timestamp if candles_1h else None,
                    "last_1h": candles_1h[-1].timestamp if candles_1h else None,
                }
            )
        for interval, rows in interval_rows.items():
            rows.sort(key=lambda row: (row.timestamp, row.symbol))
            _write_jsonl(
                dataset_root / f"candles_{interval}.jsonl",
                (_candle_record(row) for row in rows),
            )
        universe = {
            "selected": selected,
            "rejected": rejected,
            "selection_timestamp": end,
            "selection_bias": "CURRENT_SNAPSHOT_SURVIVORSHIP_BIAS_PRESENT",
            "historical_spread_semantics": "UNAVAILABLE; SNAPSHOT_ONLY",
            "historical_funding_semantics": "UNAVAILABLE; SNAPSHOT_ONLY",
        }
        (dataset_root / "universe.json").write_bytes(_canonical(universe) + b"\n")
        files: dict[str, dict[str, object]] = {}
        for path in sorted(dataset_root.iterdir()):
            if path.is_file():
                files[path.name] = {"sha256": _file_hash(path), "bytes": path.stat().st_size}
        manifest_without_hash: dict[str, object] = {
            "schema_version": int(str(dataset_config["schema_version"])),
            "dataset_id": dataset_id,
            "family": dataset_config["family"],
            "status": "FROZEN",
            "immutable": True,
            "source": "GATE_PUBLIC_API_V4",
            "capture_start": datetime.fromtimestamp(start, UTC).isoformat().replace("+00:00", "Z"),
            "capture_end": datetime.fromtimestamp(end, UTC).isoformat().replace("+00:00", "Z"),
            "received_at": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "code_commit": code_commit,
            "config_hash": _hash_bytes(config_bytes),
            "source_identity_hash": _hash_bytes(_canonical(acquisition_identity)),
            "symbols": [row["symbol"] for row in selected],
            "coverage": coverage,
            "row_counts": {key: len(value) for key, value in interval_rows.items()},
            "files": files,
            "limitations": [
                "CURRENT_SNAPSHOT_SURVIVORSHIP_BIAS_PRESENT",
                "HISTORICAL_EXECUTABLE_BBO_UNAVAILABLE",
                "HISTORICAL_FUNDING_UNAVAILABLE",
                "CANDLE_DATA_CANNOT_PROVE_FILL_QUALITY",
            ],
        }
        manifest_without_hash["identity_hash"] = _hash_bytes(_canonical(manifest_without_hash))
        manifest_bytes = _canonical(manifest_without_hash) + b"\n"
        (dataset_root / "manifest.json").write_bytes(manifest_bytes)
        (dataset_root / "manifest.sha256").write_text(
            _hash_bytes(manifest_bytes), encoding="ascii", newline="\n"
        )
        for path in dataset_root.iterdir():
            path.chmod(0o444)
        dataset_root.chmod(0o555)
        return verify_wide_crypto_dataset(dataset_root)
    except Exception:
        for path in dataset_root.glob("*"):
            path.chmod(0o666)
            path.unlink()
        dataset_root.rmdir()
        raise


def verify_wide_crypto_dataset(dataset_root: Path) -> dict[str, object]:
    manifest_path = dataset_root / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    if (dataset_root / "manifest.sha256").read_text(encoding="ascii").strip() != _hash_bytes(
        manifest_bytes
    ):
        raise ValueError("wide-crypto manifest hash mismatch")
    raw = json.loads(manifest_bytes)
    if not isinstance(raw, dict):
        raise ValueError("wide-crypto manifest is invalid")
    manifest = cast(dict[str, object], raw)
    if manifest.get("status") != "FROZEN" or manifest.get("immutable") is not True:
        raise ValueError("wide-crypto dataset is not frozen")
    if manifest.get("dataset_id") != dataset_root.name:
        raise ValueError("wide-crypto dataset identity mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("wide-crypto file identities are missing")
    for name, identity in files.items():
        if not isinstance(identity, dict):
            raise ValueError(f"invalid file identity: {name}")
        path = dataset_root / str(name)
        if not path.is_file() or _file_hash(path) != identity.get("sha256"):
            raise ValueError(f"wide-crypto file hash mismatch: {name}")
        if path.stat().st_size != int(str(identity.get("bytes", -1))):
            raise ValueError(f"wide-crypto file size mismatch: {name}")
    return manifest


def load_candles(dataset_root: Path, interval: str = "1h") -> dict[str, list[Candle]]:
    verify_wide_crypto_dataset(dataset_root)
    result: dict[str, list[Candle]] = {}
    path = dataset_root / f"candles_{interval}.jsonl"
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"invalid candle at line {line_number}")
            candle = Candle(
                timestamp=int(row["timestamp"]),
                symbol=str(row["symbol"]),
                interval=str(row["interval"]),
                open=_decimal(row["open"], "open"),
                high=_decimal(row["high"], "high"),
                low=_decimal(row["low"], "low"),
                close=_decimal(row["close"], "close"),
                volume_contracts=_decimal(row["volume_contracts"], "volume"),
                quote_volume_usdt=_decimal(row["quote_volume_usdt"], "quote volume"),
            )
            result.setdefault(candle.symbol, []).append(candle)
    for symbol, rows in result.items():
        if len({row.timestamp for row in rows}) != len(rows):
            raise ValueError(f"duplicate candle timestamp for {symbol}")
        rows.sort(key=lambda row: row.timestamp)
    return result
