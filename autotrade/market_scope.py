from __future__ import annotations

import json
import os
import threading
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from time import monotonic


class MarketScope(StrEnum):
    WIDE_CRYPTO = "WIDE_CRYPTO"
    XAU_ONLY = "XAU_ONLY"


class SwitchPolicy(StrEnum):
    SWITCH_WHEN_FLAT = "SWITCH_WHEN_FLAT"
    SWITCH_NOW_KEEP_EXISTING = "SWITCH_NOW_KEEP_EXISTING"
    FLATTEN_AND_SWITCH = "FLATTEN_AND_SWITCH"


class SwitchState(StrEnum):
    ACTIVE = "ACTIVE"
    SWITCH_REQUESTED = "SWITCH_REQUESTED"
    DRAINING = "DRAINING"
    FLATTENING = "FLATTENING"
    RECONCILING = "RECONCILING"
    ACTIVATING = "ACTIVATING"
    FAILED = "FAILED"


class XauDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"


class FeedHealth(StrEnum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class XauRegime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    ABNORMAL = "ABNORMAL"
    UNTRADABLE = "UNTRADABLE"


@dataclass(frozen=True)
class XauSettings:
    execution_symbol: str = "XAU_USDT"
    confirmation_symbols: tuple[str, ...] = ("XAUT_USDT", "PAXG_USDT")
    confirmations_enabled: bool = True
    confirmations_required: bool = False
    stale_after_seconds: int = 15
    short_window: int = 3
    trend_window: int = 12
    entry_threshold_bps: Decimal = Decimal("12")
    abnormal_divergence_bps: Decimal = Decimal("18")
    high_volatility_bps: Decimal = Decimal("16")
    low_volatility_bps: Decimal = Decimal("3")
    minimum_confidence: Decimal = Decimal("0.65")
    maximum_spread_bps: Decimal = Decimal("8")
    slippage_bps: Decimal = Decimal("2")
    risk_per_trade_usdt: Decimal = Decimal("0.15")
    maximum_position_notional_usdt: Decimal = Decimal("15")
    maximum_leverage: Decimal = Decimal("1")
    maximum_daily_loss_usdt: Decimal = Decimal("3")
    maximum_consecutive_losses: int = 3
    stop_loss_method: str = "FIXED_BPS"
    stop_loss_bps: Decimal = Decimal("35")
    take_profit_method: str = "FIXED_BPS"
    take_profit_bps: Decimal = Decimal("55")
    trailing_enabled: bool = False
    trailing_bps: Decimal = Decimal("30")
    cooldown_seconds: int = 60
    volatility_scaling: bool = True
    high_volatility_size_factor: Decimal = Decimal("0.50")
    degraded_confirmation_size_factor: Decimal = Decimal("0.75")
    maximum_holding_seconds: int = 300


@dataclass(frozen=True)
class XauDecision:
    direction: XauDirection
    confidence: Decimal
    regime: XauRegime
    volatility: str
    confirmation: str
    reasons: tuple[str, ...]
    size_factor: Decimal
    feed_health: dict[str, str]
    gross_edge_bps: Decimal = Decimal(0)
    cost_bps: Decimal = Decimal(0)

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        result.update(
            direction=self.direction.value,
            confidence=float(self.confidence),
            regime=self.regime.value,
            size_factor=float(self.size_factor),
            gross_edge_bps=float(self.gross_edge_bps),
            cost_bps=float(self.cost_bps),
        )
        return result


@dataclass
class ScopeRecord:
    active_scope: MarketScope
    requested_scope: MarketScope | None = None
    switch_status: SwitchState = SwitchState.ACTIVE
    switch_policy: SwitchPolicy | None = None
    blocking_reason: str | None = None
    updated_at_utc: str = ""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class MarketScopeController:
    """Durable, fail-closed authority for entry routing and scope transitions."""

    def __init__(
        self,
        state_path: Path,
        event_path: Path,
        default_scope: MarketScope = MarketScope.WIDE_CRYPTO,
    ) -> None:
        self._state_path = state_path
        self._event_path = event_path
        self._lock = threading.RLock()
        self._record = ScopeRecord(default_scope, updated_at_utc=_utc_now())
        self._load()

    @property
    def active_scope(self) -> MarketScope:
        with self._lock:
            return self._record.active_scope

    @property
    def entry_scope(self) -> MarketScope | None:
        with self._lock:
            return (
                self._record.active_scope
                if self._record.switch_status == SwitchState.ACTIVE
                else None
            )

    @property
    def required_symbols(self) -> tuple[str, ...]:
        return ("XAU_USDT", "XAUT_USDT", "PAXG_USDT")

    def snapshot(self, open_positions: int = 0) -> dict[str, object]:
        with self._lock:
            record = self._record
            return {
                "active_scope": record.active_scope.value,
                "requested_scope": (
                    record.requested_scope.value if record.requested_scope is not None else None
                ),
                "switch_status": record.switch_status.value,
                "switch_policy": (
                    record.switch_policy.value if record.switch_policy is not None else None
                ),
                "open_positions": open_positions,
                "blocking_reason": record.blocking_reason,
                "timestamp": record.updated_at_utc,
                "execution_symbol": (
                    "XAU_USDT" if record.active_scope == MarketScope.XAU_ONLY else "DYNAMIC"
                ),
            }

    def request(
        self,
        target: MarketScope,
        policy: SwitchPolicy,
        *,
        open_positions: int,
        confirm_flatten: bool = False,
    ) -> ScopeRecord:
        with self._lock:
            current = self._record
            previous = self._clone(current)
            if current.switch_status == SwitchState.FAILED:
                raise RuntimeError("market-scope state is FAILED; repair the persisted state first")
            if current.requested_scope == target and current.switch_policy == policy:
                return current
            if current.switch_status != SwitchState.ACTIVE:
                raise RuntimeError("another market-scope transition is already active")
            if target == current.active_scope:
                return current
            if policy == SwitchPolicy.FLATTEN_AND_SWITCH and not confirm_flatten:
                raise ValueError("FLATTEN_AND_SWITCH requires explicit confirmation")

            status = SwitchState.ACTIVE
            requested: MarketScope | None = None
            blocking: str | None = None
            active = current.active_scope
            if policy == SwitchPolicy.SWITCH_NOW_KEEP_EXISTING or open_positions == 0:
                active = target
            else:
                requested = target
                status = (
                    SwitchState.FLATTENING
                    if policy == SwitchPolicy.FLATTEN_AND_SWITCH
                    else SwitchState.DRAINING
                )
                blocking = "OPEN_POSITION"
            self._record = ScopeRecord(
                active_scope=active,
                requested_scope=requested,
                switch_status=status,
                switch_policy=policy,
                blocking_reason=blocking,
                updated_at_utc=_utc_now(),
            )
            self._save_or_fail("SCOPE_SWITCH_REQUESTED", previous)
            return self._record

    def reconcile(self, *, open_positions: int, accounting_valid: bool) -> ScopeRecord:
        with self._lock:
            current = self._record
            previous = self._clone(current)
            if current.switch_status not in {
                SwitchState.DRAINING,
                SwitchState.FLATTENING,
                SwitchState.RECONCILING,
                SwitchState.ACTIVATING,
            }:
                return current
            if open_positions:
                return current
            if not accounting_valid:
                if current.switch_status != SwitchState.RECONCILING:
                    current.switch_status = SwitchState.RECONCILING
                    current.blocking_reason = "ACCOUNTING_RECONCILIATION"
                    current.updated_at_utc = _utc_now()
                    self._save_or_fail("SCOPE_SWITCH_RECONCILING", previous)
                return current
            if current.requested_scope is None:
                self._fail("transition has no requested scope")
                return self._record
            current.active_scope = current.requested_scope
            current.requested_scope = None
            current.switch_status = SwitchState.ACTIVE
            current.blocking_reason = None
            current.updated_at_utc = _utc_now()
            self._save_or_fail("SCOPE_SWITCH_ACTIVATED", previous)
            return current

    def fail(self, reason: str) -> None:
        with self._lock:
            self._fail(reason)

    def _fail(self, reason: str) -> None:
        self._record.switch_status = SwitchState.FAILED
        self._record.blocking_reason = reason[:200]
        self._record.updated_at_utc = _utc_now()
        self._persist("SCOPE_SWITCH_FAILED")

    @staticmethod
    def _clone(record: ScopeRecord) -> ScopeRecord:
        return ScopeRecord(
            active_scope=record.active_scope,
            requested_scope=record.requested_scope,
            switch_status=record.switch_status,
            switch_policy=record.switch_policy,
            blocking_reason=record.blocking_reason,
            updated_at_utc=record.updated_at_utc,
        )

    def _save_or_fail(self, event: str, previous: ScopeRecord) -> None:
        try:
            self._persist(event)
        except OSError as exc:
            self._record = ScopeRecord(
                active_scope=previous.active_scope,
                switch_status=SwitchState.FAILED,
                blocking_reason=f"market-scope persistence failed: {exc}"[:200],
                updated_at_utc=_utc_now(),
            )
            raise RuntimeError(self._record.blocking_reason) from exc

    def _load(self) -> None:
        if not self._state_path.exists():
            self._persist("SCOPE_INITIALIZED")
            return
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("state is not an object")
            self._record = ScopeRecord(
                active_scope=MarketScope(str(value["active_scope"])),
                requested_scope=(
                    MarketScope(str(value["requested_scope"]))
                    if value.get("requested_scope") is not None
                    else None
                ),
                switch_status=SwitchState(str(value["switch_status"])),
                switch_policy=(
                    SwitchPolicy(str(value["switch_policy"]))
                    if value.get("switch_policy") is not None
                    else None
                ),
                blocking_reason=(
                    str(value["blocking_reason"])
                    if value.get("blocking_reason") is not None
                    else None
                ),
                updated_at_utc=str(value["updated_at_utc"]),
            )
            event = self._last_event()
            if event is not None and any(
                event.get(field) != value.get(field)
                for field in (
                    "active_scope",
                    "requested_scope",
                    "switch_status",
                    "switch_policy",
                    "blocking_reason",
                    "updated_at_utc",
                )
            ):
                raise ValueError("state does not match the latest durable scope event")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._record.switch_status = SwitchState.FAILED
            self._record.blocking_reason = f"invalid persisted market-scope state: {exc}"[:200]
            self._record.updated_at_utc = _utc_now()

    def _last_event(self) -> dict[str, object] | None:
        if not self._event_path.exists():
            return None
        lines = self._event_path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return None
        value = json.loads(lines[-1])
        if not isinstance(value, dict):
            raise ValueError("latest scope event is not an object")
        return value

    def _persist(self, event: str) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "active_scope": self._record.active_scope.value,
            "requested_scope": (
                self._record.requested_scope.value
                if self._record.requested_scope is not None
                else None
            ),
            "switch_status": self._record.switch_status.value,
            "switch_policy": (
                self._record.switch_policy.value if self._record.switch_policy is not None else None
            ),
            "blocking_reason": self._record.blocking_reason,
            "updated_at_utc": self._record.updated_at_utc,
        }
        event_record = {"event": event, **payload}
        with self._event_path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(event_record, allow_nan=False, separators=(",", ":")) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, allow_nan=False, separators=(",", ":"))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self._state_path)


class XauSignalEngine:
    """Return-normalized XAU decision path. It never submits orders."""

    def __init__(self, settings: XauSettings) -> None:
        self.settings = settings
        history_size = max(settings.trend_window + 1, settings.short_window + 1)
        self._history: dict[str, deque[Decimal]] = {
            symbol: deque(maxlen=history_size)
            for symbol in (settings.execution_symbol, *settings.confirmation_symbols)
        }
        self._updated: dict[str, float] = {}
        self._last = self._wait("WARMING_UP", XauRegime.UNTRADABLE)

    @property
    def last_decision(self) -> XauDecision:
        return self._last

    def update(
        self,
        markets: Mapping[str, Mapping[str, object]],
        *,
        now: float | None = None,
    ) -> XauDecision:
        observed = monotonic() if now is None else now
        for symbol, history in self._history.items():
            market = markets.get(symbol)
            if market is None:
                continue
            try:
                price = Decimal(str(market["last"]))
            except (KeyError, InvalidOperation, ValueError):
                continue
            if price.is_finite() and price > 0:
                history.append(price)
                self._updated[symbol] = observed

        health = {
            symbol: self._health(symbol, observed).value
            for symbol in self._history
        }
        primary = self.settings.execution_symbol
        if health[primary] != FeedHealth.HEALTHY.value:
            self._last = self._wait("PRIMARY_FEED_STALE", XauRegime.UNTRADABLE, health)
            return self._last
        history = self._history[primary]
        if len(history) < self.settings.trend_window + 1:
            self._last = self._wait("WARMING_UP", XauRegime.UNTRADABLE, health)
            return self._last

        short_return = self._return_bps(history, self.settings.short_window)
        trend_return = self._return_bps(history, self.settings.trend_window)
        step_returns = [
            (current / previous - 1) * Decimal(10_000)
            for previous, current in zip(history, tuple(history)[1:], strict=False)
            if previous > 0
        ]
        mean = sum(step_returns, Decimal(0)) / Decimal(len(step_returns))
        variance = sum(((value - mean) ** 2 for value in step_returns), Decimal(0)) / Decimal(
            len(step_returns)
        )
        volatility = variance.sqrt()
        if volatility >= self.settings.high_volatility_bps:
            regime = XauRegime.HIGH_VOLATILITY
            volatility_state = "HIGH"
        elif volatility <= self.settings.low_volatility_bps:
            regime = XauRegime.LOW_VOLATILITY
            volatility_state = "LOW"
        elif trend_return >= self.settings.entry_threshold_bps:
            regime = XauRegime.TRENDING_UP
            volatility_state = "NORMAL"
        elif trend_return <= -self.settings.entry_threshold_bps:
            regime = XauRegime.TRENDING_DOWN
            volatility_state = "NORMAL"
        else:
            regime = XauRegime.RANGING
            volatility_state = "NORMAL"

        market = markets[primary]
        spread = Decimal(str(market.get("spread_bps", "1000000")))
        cost = Decimal(10) + spread + self.settings.slippage_bps * 2 + Decimal(3)
        gross = abs(short_return)
        base_direction = (
            XauDirection.LONG
            if short_return > 0 and trend_return > 0
            else XauDirection.SHORT
            if short_return < 0 and trend_return < 0
            else XauDirection.WAIT
        )
        reasons: list[str] = []
        if spread > self.settings.maximum_spread_bps:
            reasons.append("SPREAD_LIMIT")
        if gross < max(self.settings.entry_threshold_bps, cost):
            reasons.append("INSUFFICIENT_NET_EDGE")
        if base_direction == XauDirection.WAIT:
            reasons.append("DIRECTION_NOT_CONFIRMED")

        reference_returns: list[Decimal] = []
        agreements = 0
        disagreements = 0
        healthy_refs = 0
        if self.settings.confirmations_enabled:
            for symbol in self.settings.confirmation_symbols:
                ref_history = self._history[symbol]
                if (
                    health[symbol] != FeedHealth.HEALTHY.value
                    or len(ref_history) <= self.settings.short_window
                ):
                    continue
                healthy_refs += 1
                ref_return = self._return_bps(ref_history, self.settings.short_window)
                reference_returns.append(ref_return)
                agrees = (
                    base_direction == XauDirection.LONG and ref_return > 0
                ) or (base_direction == XauDirection.SHORT and ref_return < 0)
                if agrees:
                    agreements += 1
                elif base_direction != XauDirection.WAIT:
                    disagreements += 1
            if self.settings.confirmations_required and healthy_refs < len(
                self.settings.confirmation_symbols
            ):
                reasons.append("CONFIRMATION_REQUIRED")
            if reference_returns and max(
                abs(short_return - value) for value in reference_returns
            ) > self.settings.abnormal_divergence_bps:
                reasons.append("ABNORMAL_DIVERGENCE")
                regime = XauRegime.ABNORMAL

        confirmation = (
            "DISABLED"
            if not self.settings.confirmations_enabled
            else "UNAVAILABLE"
            if healthy_refs == 0
            else "AGREE"
            if agreements == healthy_refs
            else "DISAGREE"
            if disagreements
            else "PARTIAL"
        )
        net = max(Decimal(0), gross - cost)
        confidence = min(Decimal(1), net / max(self.settings.entry_threshold_bps, Decimal(1)))
        confidence += Decimal("0.05") * agreements
        confidence -= Decimal("0.08") * disagreements
        confidence = min(Decimal(1), max(Decimal(0), confidence))
        if confidence < self.settings.minimum_confidence:
            reasons.append("LOW_CONFIDENCE")
        direction = base_direction if not reasons else XauDirection.WAIT
        size_factor = Decimal(1)
        if regime == XauRegime.HIGH_VOLATILITY and self.settings.volatility_scaling:
            size_factor *= self.settings.high_volatility_size_factor
        if healthy_refs < len(self.settings.confirmation_symbols):
            size_factor *= self.settings.degraded_confirmation_size_factor
        self._last = XauDecision(
            direction=direction,
            confidence=confidence,
            regime=regime,
            volatility=volatility_state,
            confirmation=confirmation,
            reasons=tuple(reasons) if reasons else ("QUALIFIED",),
            size_factor=size_factor,
            feed_health=health,
            gross_edge_bps=gross,
            cost_bps=cost,
        )
        return self._last

    def _health(self, symbol: str, now: float) -> FeedHealth:
        updated = self._updated.get(symbol)
        if updated is None:
            return FeedHealth.UNAVAILABLE
        return (
            FeedHealth.HEALTHY
            if now - updated <= self.settings.stale_after_seconds
            else FeedHealth.STALE
        )

    @staticmethod
    def _return_bps(history: Iterable[Decimal], window: int) -> Decimal:
        values = tuple(history)
        return (values[-1] / values[-window - 1] - 1) * Decimal(10_000)

    @staticmethod
    def _wait(
        reason: str,
        regime: XauRegime,
        health: dict[str, str] | None = None,
    ) -> XauDecision:
        return XauDecision(
            direction=XauDirection.WAIT,
            confidence=Decimal(0),
            regime=regime,
            volatility="UNKNOWN",
            confirmation="UNAVAILABLE",
            reasons=(reason,),
            size_factor=Decimal(0),
            feed_health=health or {},
        )
