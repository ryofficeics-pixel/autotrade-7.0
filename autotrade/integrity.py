from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4, uuid5

EVENT_SCHEMA_VERSION: Final = 3
CHECKPOINT_SCHEMA_VERSION: Final = 3
ACCOUNTING_TOLERANCE: Final = Decimal("0.00000001")
STRATEGY_VERSION: Final = "REST_MOMENTUM_TOURNAMENT_V2"
EXECUTION_MODEL_VERSION: Final = "NAUTILUS_PAPER_REST_V2"
MARKET_DATA_MODE: Final = "GATE_PUBLIC_REST"
TRANSITION_PROTOCOL_VERSION: Final = 1

EVENT_FIELDS: Final = (
    "schema_version",
    "event_id",
    "sequence",
    "run_id",
    "session_id",
    "timestamp_utc",
    "timestamp_exchange",
    "timestamp_local_or_receive",
    "event_type",
    "symbol",
    "strategy_id",
    "signal_id",
    "position_id",
    "order_id",
    "client_order_id",
    "fill_id",
    "side",
    "order_type",
    "quantity",
    "price",
    "fee_amount",
    "fee_currency",
    "reason",
    "exchange_timestamp",
    "local_receive_timestamp",
    "decision_timestamp",
    "order_submit_timestamp",
    "fill_timestamp",
    "latency_ms",
)

REQUIRED_BY_EVENT: Final = {
    "signal": ("symbol", "strategy_id", "signal_id", "side", "decision_timestamp"),
    "order_submitted": (
        "symbol",
        "strategy_id",
        "signal_id",
        "order_id",
        "client_order_id",
        "side",
        "order_type",
        "quantity",
        "order_submit_timestamp",
    ),
    "fill": (
        "symbol",
        "strategy_id",
        "signal_id",
        "position_id",
        "order_id",
        "client_order_id",
        "fill_id",
        "side",
        "quantity",
        "price",
        "fee_amount",
        "fee_currency",
        "fill_timestamp",
    ),
    "position_opened": (
        "symbol",
        "strategy_id",
        "signal_id",
        "position_id",
        "side",
        "quantity",
        "price",
    ),
    "position_reduced": (
        "symbol",
        "strategy_id",
        "signal_id",
        "position_id",
        "side",
        "quantity",
        "reason",
        "realized_pnl_usdt",
    ),
    "exit_signal": (
        "symbol",
        "strategy_id",
        "signal_id",
        "position_id",
        "order_id",
        "side",
        "reason",
        "decision_timestamp",
    ),
    "position_closed": (
        "symbol",
        "strategy_id",
        "signal_id",
        "position_id",
        "side",
        "quantity",
        "price",
        "fee_amount",
        "fee_currency",
        "reason",
    ),
    "order_rejected": ("symbol", "strategy_id", "order_id", "reason"),
    "recovery_flatten": (
        "symbol",
        "strategy_id",
        "order_id",
        "fill_id",
        "side",
        "quantity",
        "price",
        "fee_amount",
        "fee_currency",
        "reason",
    ),
    "state_reconciliation_failed": ("reason",),
    "transition_intent": ("transition_id", "target_event_sequence"),
    "transition_committed": ("transition_id", "target_event_sequence"),
}


class IntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class LedgerSummary:
    last_sequence: int
    event_count: int
    legacy_count: int
    realized_pnl: Decimal
    fees: Decimal
    trade_count: int
    open_positions: dict[str, dict[str, object]]


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def new_identity() -> str:
    return str(uuid4())


def validate_event(event: dict[str, object]) -> None:
    missing = [field for field in EVENT_FIELDS if field not in event]
    if missing:
        raise IntegrityError(f"event is missing fields: {','.join(missing)}")
    if event["schema_version"] != EVENT_SCHEMA_VERSION:
        raise IntegrityError("unsupported event schema")
    if not isinstance(event["sequence"], int) or int(event["sequence"]) <= 0:
        raise IntegrityError("event sequence must be a positive integer")
    for field in ("event_id", "run_id", "session_id", "timestamp_utc", "event_type"):
        if not isinstance(event[field], str) or not event[field]:
            raise IntegrityError(f"{field} is required")
    for field in ("event_id", "run_id", "session_id"):
        try:
            UUID(str(event[field]))
        except ValueError as exc:
            raise IntegrityError(f"{field} is not a UUID") from exc
    for field in ("signal_id", "position_id", "order_id", "fill_id"):
        value = event.get(field)
        if value is not None:
            try:
                UUID(str(value))
            except ValueError as exc:
                raise IntegrityError(f"{field} is not a UUID") from exc
    if event.get("transition_id") is not None:
        try:
            UUID(str(event["transition_id"]))
        except ValueError as exc:
            raise IntegrityError("transition_id is not a UUID") from exc
    try:
        timestamp = datetime.fromisoformat(str(event["timestamp_utc"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrityError("timestamp_utc is invalid") from exc
    if timestamp.tzinfo is None:
        raise IntegrityError("timestamp_utc must include a timezone")
    event_type = str(event["event_type"])
    if event_type not in REQUIRED_BY_EVENT:
        raise IntegrityError(f"unsupported event type: {event_type}")
    absent = [field for field in REQUIRED_BY_EVENT[event_type] if event.get(field) is None]
    if absent:
        raise IntegrityError(f"{event_type} is missing required values: {','.join(absent)}")
    if event_type in {"transition_intent", "transition_committed"}:
        try:
            target = int(str(event["target_event_sequence"]))
        except (TypeError, ValueError) as exc:
            raise IntegrityError("target_event_sequence must be an integer") from exc
        sequence = int(event["sequence"])
        if (event_type == "transition_intent" and target <= sequence) or (
            event_type == "transition_committed" and target != sequence
        ):
            raise IntegrityError("target_event_sequence is inconsistent with the marker")
    for field in ("quantity", "price", "fee_amount"):
        if event.get(field) is None:
            continue
        value = _event_decimal(event, field)
        if field in {"quantity", "price"} and value <= 0:
            raise IntegrityError(f"{field} must be positive")
        if field == "fee_amount" and value < 0:
            raise IntegrityError("fee_amount must be non-negative")


class EventLedger:
    def __init__(self, path: Path, run_id: str, session_id: str) -> None:
        UUID(run_id)
        UUID(session_id)
        self.path = path
        self.run_id = run_id
        self.session_id = session_id
        self.summary = scan_ledger(path, run_id)
        self.last_sequence = self.summary.last_sequence

    def append(self, event_type: str, **values: object) -> dict[str, object]:
        event = self.prepare(self.last_sequence + 1, event_type, values)
        self.append_records((event,))
        return event

    def prepare(
        self,
        sequence: int,
        event_type: str,
        values: dict[str, object],
        *,
        timestamp_utc: str | None = None,
    ) -> dict[str, object]:
        event: dict[str, object] = dict.fromkeys(EVENT_FIELDS)
        event.update(
            {
                "schema_version": EVENT_SCHEMA_VERSION,
                "event_id": str(uuid5(UUID(self.run_id), f"event:{sequence}:{event_type}")),
                "sequence": sequence,
                "run_id": self.run_id,
                "session_id": self.session_id,
                "timestamp_utc": timestamp_utc or utc_now(),
                "event_type": event_type,
            }
        )
        event.update(values)
        validate_event(event)
        return event

    def append_records(
        self,
        records: Sequence[dict[str, object]],
        *,
        refresh: bool = True,
    ) -> None:
        if not records:
            return
        expected = self.last_sequence + 1
        for record in records:
            validate_event(record)
            if record.get("run_id") != self.run_id or record.get("sequence") != expected:
                raise IntegrityError("prepared event sequence or run identity is invalid")
            expected += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n")
            file.flush()
            os.fsync(file.fileno())
        self.last_sequence = expected - 1
        if refresh:
            self.summary = scan_ledger(self.path, self.run_id)

    def preview(self, records: Sequence[dict[str, object]]) -> LedgerSummary:
        if not records:
            return self.summary
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.path.parent) as directory:
            preview_path = Path(directory) / "events.jsonl"
            with preview_path.open("wb") as preview:
                if self.path.is_file():
                    preview.write(self.path.read_bytes())
                for record in records:
                    preview.write(
                        (json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n").encode()
                    )
            return scan_ledger(preview_path, self.run_id)

    def refresh(self) -> None:
        self.summary = scan_ledger(self.path, self.run_id)
        self.last_sequence = self.summary.last_sequence


FailureInjector = Callable[[str], None]
EventSpec = tuple[str, dict[str, object]]


def commit_transition(
    ledger: EventLedger,
    state_path: Path,
    checkpoint: dict[str, object],
    event_specs: Sequence[EventSpec],
    *,
    failure_injector: FailureInjector | None = None,
) -> list[dict[str, object]]:
    if not event_specs:
        _atomic_json(state_path, checkpoint)
        return []
    pending_path = _transition_path(state_path)
    if pending_path.exists():
        raise IntegrityError("a prior state transition requires recovery")
    transition_id = str(
        uuid5(
            UUID(ledger.run_id),
            f"transition:{ledger.last_sequence + 1}:{checkpoint.get('checkpoint_sequence')}",
        )
    )
    timestamp = utc_now()
    target_sequence = ledger.last_sequence + len(event_specs) + 2
    records = [
        ledger.prepare(
            ledger.last_sequence + 1,
            "transition_intent",
            {
                "transition_id": transition_id,
                "target_event_sequence": target_sequence,
                "reason": "FINANCIAL_STATE_MUTATION",
            },
            timestamp_utc=timestamp,
        )
    ]
    for offset, (event_type, values) in enumerate(event_specs, 2):
        records.append(
            ledger.prepare(
                ledger.last_sequence + offset,
                event_type,
                {**values, "transition_id": transition_id},
                timestamp_utc=str(values.get("timestamp_utc") or timestamp),
            )
        )
    records.append(
        ledger.prepare(
            target_sequence,
            "transition_committed",
            {
                "transition_id": transition_id,
                "target_event_sequence": target_sequence,
                "reason": "FINANCIAL_STATE_MUTATION",
            },
            timestamp_utc=timestamp,
        )
    )
    target_checkpoint = {
        **checkpoint,
        "last_event_sequence": target_sequence,
        "last_committed_event_sequence": target_sequence,
        "last_transition_id": transition_id,
        "transition_protocol_version": TRANSITION_PROTOCOL_VERSION,
    }
    preview = ledger.preview(records)
    reconcile_checkpoint(target_checkpoint, preview)
    plan: dict[str, object] = {
        "protocol_version": TRANSITION_PROTOCOL_VERSION,
        "run_id": ledger.run_id,
        "transition_id": transition_id,
        "records": records,
        "checkpoint": target_checkpoint,
    }
    plan["plan_hash"] = hashlib.sha256(_canonical_json(plan).encode()).hexdigest()
    _atomic_json(pending_path, plan)
    _inject(failure_injector, "before_event_append")
    ledger.append_records(records[:-1], refresh=False)
    _inject(failure_injector, "after_event_append")
    _inject(failure_injector, "before_checkpoint_write")
    _atomic_json_with_hook(state_path, target_checkpoint, failure_injector)
    _inject(failure_injector, "after_atomic_rename")
    _inject(failure_injector, "before_commit_marker")
    ledger.append_records(records[-1:], refresh=False)
    _inject(failure_injector, "after_commit_marker")
    ledger.refresh()
    reconcile_checkpoint(target_checkpoint, ledger.summary)
    pending_path.unlink()
    return records[1:-1]


def recover_pending_transition(
    state_path: Path,
    ledger_path: Path,
    run_id: str,
) -> dict[str, object] | None:
    pending_path = _transition_path(state_path)
    if not pending_path.is_file():
        return None
    try:
        plan = json.loads(pending_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"pending transition is unreadable: {exc}") from exc
    if not isinstance(plan, dict):
        raise IntegrityError("pending transition is not an object")
    plan_hash = plan.pop("plan_hash", None)
    calculated = hashlib.sha256(_canonical_json(plan).encode()).hexdigest()
    if plan_hash != calculated:
        raise IntegrityError("pending transition hash mismatch")
    if plan.get("protocol_version") != TRANSITION_PROTOCOL_VERSION or plan.get("run_id") != run_id:
        raise IntegrityError("pending transition identity mismatch")
    records = plan.get("records")
    checkpoint = plan.get("checkpoint")
    if not isinstance(records, list) or not records or not isinstance(checkpoint, dict):
        raise IntegrityError("pending transition structure is invalid")
    prepared = []
    for record in records:
        if not isinstance(record, dict):
            raise IntegrityError("pending transition contains an invalid event")
        validate_event(record)
        prepared.append(record)
    existing = _read_ledger_records(ledger_path)
    start = int(str(prepared[0]["sequence"])) - 1
    if len(existing) < start:
        raise IntegrityError("ledger is behind the pending transition base")
    suffix = existing[start:]
    if len(suffix) > len(prepared) or suffix != prepared[: len(suffix)]:
        raise IntegrityError("ledger diverges from the pending transition")
    precommit_count = len(prepared) - 1
    if len(suffix) < precommit_count:
        _append_raw_records(ledger_path, prepared[len(suffix) : precommit_count])
        suffix = prepared[:precommit_count]
    _atomic_json(state_path, checkpoint)
    if len(suffix) < len(prepared):
        _append_raw_records(ledger_path, prepared[-1:])
    summary = scan_ledger(ledger_path, run_id)
    reconcile_checkpoint(checkpoint, summary)
    recovered = {
        **checkpoint,
        "transition_recovery": {
            "status": "REPLAYED_COMMIT",
            "transition_id": plan.get("transition_id"),
            "recovered_at_utc": utc_now(),
        },
    }
    _atomic_json(state_path, recovered)
    pending_path.unlink()
    return recovered


def _transition_path(state_path: Path) -> Path:
    return state_path.with_suffix(state_path.suffix + ".transition")


def _inject(injector: FailureInjector | None, boundary: str) -> None:
    if injector is not None:
        injector(boundary)


def _atomic_json_with_hook(
    path: Path,
    value: dict[str, object],
    injector: FailureInjector | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(value, file, allow_nan=False, separators=(",", ":"))
        file.flush()
        os.fsync(file.fileno())
        _inject(injector, "during_temporary_checkpoint_write")
    temporary.replace(path)


def _read_ledger_records(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    records: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise IntegrityError(f"event ledger cannot be read: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IntegrityError(f"event ledger line {line_number} is invalid JSON") from exc
        if not isinstance(record, dict):
            raise IntegrityError(f"event ledger line {line_number} is not an object")
        records.append(record)
    return records


def _append_raw_records(path: Path, records: Sequence[dict[str, object]]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n")
        file.flush()
        os.fsync(file.fileno())


def scan_ledger(path: Path, expected_run_id: str) -> LedgerSummary:
    if not path.is_file():
        return LedgerSummary(0, 0, 0, Decimal(0), Decimal(0), 0, {})
    last_sequence = 0
    event_ids: set[str] = set()
    event_count = 0
    legacy_count = 0
    realized = Decimal(0)
    fees = Decimal(0)
    trades = 0
    positions: dict[str, dict[str, object]] = {}
    signals: set[str] = set()
    orders: dict[str, str | None] = {}
    entry_orders: dict[str, Decimal] = {}
    entry_fills: dict[str, list[dict[str, object]]] = {}
    exit_fills: set[str] = set()
    exit_filled_quantities: dict[str, Decimal] = {}
    reduced_positions: set[str] = set()
    fill_ids: set[str] = set()
    active_transition: tuple[str, int] | None = None
    committed_transitions: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise IntegrityError(f"event ledger cannot be read: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IntegrityError(f"event ledger line {line_number} is invalid JSON") from exc
        if not isinstance(event, dict):
            raise IntegrityError(f"event ledger line {line_number} is not an object")
        if event.get("schema_version") != EVENT_SCHEMA_VERSION:
            legacy_count += 1
            continue
        validate_event(event)
        if event["run_id"] != expected_run_id:
            raise IntegrityError(f"event ledger line {line_number} has a different run_id")
        sequence = int(event["sequence"])
        if sequence != last_sequence + 1:
            raise IntegrityError(f"event sequence regression/gap at line {line_number}")
        event_id = str(event["event_id"])
        if event_id in event_ids:
            raise IntegrityError(f"duplicate event_id at line {line_number}")
        event_ids.add(event_id)
        last_sequence = sequence
        event_count += 1
        event_type = str(event["event_type"])
        transition_id = (
            str(event["transition_id"]) if event.get("transition_id") is not None else None
        )
        if event_type == "transition_intent":
            target = int(str(event["target_event_sequence"]))
            if active_transition is not None or target <= sequence:
                raise IntegrityError(f"invalid transition intent at line {line_number}")
            if transition_id in committed_transitions:
                raise IntegrityError(f"duplicate transition_id at line {line_number}")
            assert transition_id is not None
            active_transition = (transition_id, target)
            continue
        if event_type == "transition_committed":
            target = int(str(event["target_event_sequence"]))
            if active_transition != (transition_id, target) or target != sequence:
                raise IntegrityError(f"invalid transition commit at line {line_number}")
            assert transition_id is not None
            committed_transitions.add(transition_id)
            active_transition = None
            continue
        if transition_id is not None and (
            active_transition is None or active_transition[0] != transition_id
        ):
            raise IntegrityError(f"event has no matching transition at line {line_number}")
        signal_id = str(event["signal_id"]) if event.get("signal_id") is not None else None
        position_id = str(event["position_id"]) if event.get("position_id") is not None else None
        order_id = str(event["order_id"]) if event.get("order_id") is not None else None
        if event_type == "signal":
            assert signal_id is not None
            if signal_id in signals:
                raise IntegrityError(f"duplicate signal_id at line {line_number}")
            signals.add(signal_id)
        elif event_type == "order_submitted":
            if signal_id not in signals or order_id is None:
                raise IntegrityError(f"orphan entry order at line {line_number}")
            orders[order_id] = signal_id
            entry_orders[order_id] = _event_decimal(event, "quantity")
        elif event_type == "fill":
            if order_id not in orders or orders[order_id] != signal_id:
                raise IntegrityError(f"orphan fill at line {line_number}")
            fill_id = str(event["fill_id"])
            if fill_id in fill_ids:
                raise IntegrityError(f"duplicate fill_id at line {line_number}")
            fill_ids.add(fill_id)
            assert position_id is not None
            if order_id in entry_orders:
                entry_orders[order_id] -= _event_decimal(event, "quantity")
                if entry_orders[order_id] < 0:
                    raise IntegrityError(f"entry fills exceed order quantity at line {line_number}")
                entry_fills.setdefault(position_id, []).append(event)
            else:
                exit_fills.add(position_id)
                exit_filled_quantities[position_id] = exit_filled_quantities.get(
                    position_id, Decimal(0)
                ) + _event_decimal(event, "quantity")
            fees += _event_decimal(event, "fee_amount")
        elif event_type == "position_opened":
            if signal_id not in signals or position_id is None or position_id in positions:
                raise IntegrityError(f"invalid position open at line {line_number}")
            quantity, notional, _ = _entry_totals(entry_fills.get(position_id, []))
            if (
                quantity <= 0
                or abs(_event_decimal(event, "quantity") - quantity) > ACCOUNTING_TOLERANCE
                or abs(_event_decimal(event, "price") - notional / quantity) > ACCOUNTING_TOLERANCE
            ):
                raise IntegrityError(
                    f"position open does not match its fills at line {line_number}"
                )
            positions[position_id] = event
        elif event_type == "exit_signal":
            if position_id not in positions or order_id is None:
                raise IntegrityError(f"orphan exit order at line {line_number}")
            orders[order_id] = signal_id
        elif event_type == "position_reduced":
            if position_id not in positions:
                raise IntegrityError(f"orphan position reduction at line {line_number}")
            entry_quantity, _, _ = _entry_totals(entry_fills.get(position_id, []))
            remaining = entry_quantity - exit_filled_quantities.get(position_id, Decimal(0))
            if (
                remaining <= 0
                or abs(_event_decimal(event, "quantity") - remaining) > ACCOUNTING_TOLERANCE
            ):
                raise IntegrityError(f"position reduction quantity mismatch at line {line_number}")
            realized += _event_decimal(event, "realized_pnl_usdt")
            assert position_id is not None
            reduced_positions.add(position_id)
        elif event_type in {"position_closed", "recovery_flatten"}:
            if event_type == "position_closed" and position_id not in positions:
                raise IntegrityError(f"orphan position close at line {line_number}")
            realized += _event_decimal(event, "realized_pnl_usdt")
            trades += 1
            if event_type == "recovery_flatten":
                fees += _event_decimal(event, "fee_amount")
            if position_id is not None:
                positions.pop(position_id, None)
    if active_transition is not None:
        raise IntegrityError("event ledger ends with an incomplete transition")
    for position_id, position in positions.items():
        fills = entry_fills.get(position_id, [])
        if not fills:
            raise IntegrityError("open position has no attributed entry fills")
        expected_side = {"LONG": {"1", "BUY"}, "SHORT": {"2", "SELL"}}.get(
            str(position["side"]), set()
        )
        if any(
            fill["symbol"] != position["symbol"]
            or fill["signal_id"] != position["signal_id"]
            or fill["side"] not in expected_side
            or fill["fee_currency"] != "USDT"
            for fill in fills
        ):
            raise IntegrityError("open position entry-fill identity mismatch")
        quantity, notional, entry_fee = _entry_totals(fills)
        open_quantity = quantity - exit_filled_quantities.get(position_id, Decimal(0))
        if open_quantity < 0:
            raise IntegrityError("exit fills exceed open position quantity")
        remaining_entry_fee = (
            entry_fee * open_quantity / quantity if quantity > 0 else Decimal(0)
        )
        positions[position_id] = {
            **position,
            "exit_pending": position_id in exit_fills and position_id not in reduced_positions,
            "quantity": str(open_quantity),
            "price": str(notional / quantity),
            "entry_fee_usdt": str(remaining_entry_fee),
        }
    return LedgerSummary(
        last_sequence,
        event_count,
        legacy_count,
        realized,
        fees,
        trades,
        positions,
    )


def _entry_totals(fills: list[dict[str, object]]) -> tuple[Decimal, Decimal, Decimal]:
    quantity = sum((_event_decimal(fill, "quantity") for fill in fills), Decimal(0))
    notional = sum(
        (_event_decimal(fill, "quantity") * _event_decimal(fill, "price") for fill in fills),
        Decimal(0),
    )
    fees = sum((_event_decimal(fill, "fee_amount") for fill in fills), Decimal(0))
    return quantity, notional, fees


def reconcile_checkpoint(state: dict[str, object], summary: LedgerSummary) -> None:
    if int(str(state.get("schema_version", 0))) != CHECKPOINT_SCHEMA_VERSION:
        raise IntegrityError("checkpoint is legacy/unattributed; create a new run explicitly")
    if state.get("mode") != "PAPER":
        raise IntegrityError("checkpoint mode is not PAPER")
    if int(str(state.get("last_event_sequence", -1))) != summary.last_sequence:
        raise IntegrityError("checkpoint/event sequence mismatch")
    if int(str(state.get("last_committed_event_sequence", summary.last_sequence))) != (
        summary.last_sequence
    ):
        raise IntegrityError("checkpoint committed-event sequence mismatch")
    starting = _state_decimal(state, "starting_equity_usdt")
    balance = _state_decimal(state, "balance_usdt")
    persisted_fees = _state_decimal(state, "fees_usdt")
    persisted_realized = _state_decimal(state, "cumulative_realized_pnl_usdt")
    if abs(persisted_realized - summary.realized_pnl) > ACCOUNTING_TOLERANCE:
        raise IntegrityError(
            "checkpoint/ledger realized PnL mismatch: "
            f"checkpoint={persisted_realized} ledger={summary.realized_pnl}"
        )
    position = state.get("position")
    open_entry_fee = (
        _state_decimal(position, "entry_fee_usdt") if isinstance(position, dict) else Decimal(0)
    )
    if abs(balance - (starting + summary.realized_pnl - open_entry_fee)) > ACCOUNTING_TOLERANCE:
        raise IntegrityError(
            "checkpoint/ledger equity mismatch: "
            f"balance={balance} expected={starting + summary.realized_pnl - open_entry_fee}"
        )
    if abs(persisted_fees - summary.fees) > ACCOUNTING_TOLERANCE:
        raise IntegrityError("checkpoint/ledger fee mismatch")
    if int(str(state.get("trades", -1))) != summary.trade_count:
        raise IntegrityError("checkpoint/ledger trade-count mismatch")
    if position is None and summary.open_positions:
        raise IntegrityError("checkpoint/ledger open-position mismatch")
    if position is not None:
        if not isinstance(position, dict):
            raise IntegrityError("checkpoint position is not an object")
        position_id = position.get("position_id")
        if position_id is None or set(summary.open_positions) != {str(position_id)}:
            raise IntegrityError("checkpoint/ledger position identity mismatch")
        ledger_position = summary.open_positions[str(position_id)]
        if ledger_position.get("exit_pending"):
            raise IntegrityError("open position has incomplete exit fills; recovery required")
        if position.get("symbol") != ledger_position.get("symbol"):
            raise IntegrityError("checkpoint/ledger position symbol mismatch")
        if position.get("side") != ledger_position.get("side"):
            raise IntegrityError("checkpoint/ledger position side mismatch")
        if position.get("signal_id") != ledger_position.get("signal_id"):
            raise IntegrityError("checkpoint/ledger position signal mismatch")
        if (
            abs(
                _state_decimal(position, "entry_fee_usdt")
                - _state_decimal(ledger_position, "entry_fee_usdt")
            )
            > ACCOUNTING_TOLERANCE
        ):
            raise IntegrityError("checkpoint/ledger position entry fee mismatch")
        if (
            abs(_state_decimal(position, "quantity") - _state_decimal(ledger_position, "quantity"))
            > ACCOUNTING_TOLERANCE
        ):
            raise IntegrityError("checkpoint/ledger position quantity mismatch")
        if (
            abs(_state_decimal(position, "entry_price") - _state_decimal(ledger_position, "price"))
            > ACCOUNTING_TOLERANCE
        ):
            raise IntegrityError("checkpoint/ledger position price mismatch")


def create_new_run(
    *,
    project_root: Path,
    config_path: Path,
    log_directory: Path,
    starting_equity: Decimal,
) -> dict[str, object]:
    run_id = new_identity()
    created_session_id = new_identity()
    created_at = utc_now()
    run_root = project_root / "data" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    _archive_legacy_files(project_root, log_directory, created_at)
    metadata = {
        "run_id": run_id,
        "started_at_utc": created_at,
        "starting_equity_usdt": str(starting_equity),
        "git_commit": _git_commit(project_root),
        "config_hash": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "strategy_version": STRATEGY_VERSION,
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "transition_protocol_version": TRANSITION_PROTOCOL_VERSION,
        "market_data_mode": MARKET_DATA_MODE,
        "execution_model_version": EXECUTION_MODEL_VERSION,
    }
    _atomic_json(run_root / "metadata.json", metadata)
    events_path = run_root / "events.jsonl"
    events_path.touch(exist_ok=False)
    state: dict[str, object] = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_sequence": 0,
        "last_event_sequence": 0,
        "last_committed_event_sequence": 0,
        "last_transition_id": None,
        "transition_protocol_version": TRANSITION_PROTOCOL_VERSION,
        "run_id": run_id,
        "session_id": created_session_id,
        "event_ledger_path": str(events_path.relative_to(project_root)),
        "mode": "PAPER",
        "symbol": "MULTI",
        "starting_equity_usdt": str(starting_equity),
        "balance_usdt": str(starting_equity),
        "cumulative_realized_pnl_usdt": "0",
        "trades": 0,
        "fees_usdt": "0",
        "position": None,
        "risk_day_utc": datetime.now(UTC).date().isoformat(),
        "day_start_equity_usdt": str(starting_equity),
        "trades_at_day_start": 0,
        "peak_equity_usdt": str(starting_equity),
        "drawdown_window": "UTC_DAY",
        "risk_halted": False,
        "risk_halt_reason": None,
        "rollover_review_required": False,
        "updated_at_utc": created_at,
        "updated_ns": 0,
    }
    _atomic_json(log_directory / "paper-state.json", state)
    return metadata


def _archive_legacy_files(project_root: Path, log_directory: Path, timestamp: str) -> None:
    candidates = [log_directory / "paper-state.json", log_directory / "paper-events.jsonl"]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return
    stamp = timestamp.replace(":", "-")
    archive = project_root / "data" / "runs" / f"legacy-{stamp}" / "legacy"
    archive.mkdir(parents=True, exist_ok=False)
    for source in existing:
        shutil.copy2(source, archive / source.name)


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(value, file, allow_nan=False, separators=(",", ":"))
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _git_commit(project_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and len(commit) == 40 else "UNKNOWN"


def _event_decimal(event: dict[str, object], field: str) -> Decimal:
    try:
        value = Decimal(str(event[field]))
    except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
        raise IntegrityError(f"{field} must be numeric") from exc
    if not value.is_finite():
        raise IntegrityError(f"{field} must be finite")
    return value


def _state_decimal(state: dict[str, object], field: str) -> Decimal:
    return _event_decimal(state, field)
