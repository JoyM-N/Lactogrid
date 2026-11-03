"""Stateful controller.

Safety is applied before a manual override, and a manual override is applied
before the strategy. The strategy never sees a sample whose tank or store
sensor has already been rejected.

The thermostat, the store-charge band, and the backup rule are updated on every
sample. ``controller.interval_minutes`` is the longest gap the gateway may leave
between calls. The gateway calls ``step`` on every sample, which is shorter
than that gap, so a hot tank is not left for fifteen minutes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from shared.config import RootConfig
from shared.enums import CompressorTarget, ControlActionKind, ControlMode, EventType, Severity
from shared.schemas import ActuatorCommand, ControlAction, Event, ManualOverride, Reading, Site
from edge.rule import RuleStrategy
from edge.safety import (
    SafetyAssessment,
    TempSample,
    assess_sensors,
    battery_kwh_of,
    store_kwh_of,
    tank_sensor_is_stuck,
)
from edge.strategy import Strategy, StrategyContext, StrategyResult


@dataclass
class ControlDecision:
    site_id: str
    timestamp: datetime
    mode: ControlMode
    action: ControlActionKind
    reason: str
    command: ActuatorCommand
    inputs_snapshot: dict[str, Any]
    events: list[Event] = field(default_factory=list)

    def to_action(self) -> ControlAction:
        return ControlAction(
            site_id=self.site_id,
            timestamp=self.timestamp,
            mode=self.mode,
            action=self.action,
            reason=self.reason,
            inputs_snapshot=self.inputs_snapshot,
            command=self.command,
        )


class Controller:
    """One site. Hold one instance for the life of the process."""

    def __init__(
        self,
        config: RootConfig,
        site: Site,
        strategy: Strategy | None = None,
        step_hours: float = 1 / 60,
    ) -> None:
        self.config = config
        self.site = site
        self.strategy: Strategy = strategy or RuleStrategy()
        self.step_hours = step_hours
        self.tank_cooling = False
        self.store_charging = False
        self.backup_cooling = False
        self.override: ManualOverride | None = None
        self.history: list[TempSample] = []
        self._was_safe = False
        self._backup_command = False
        self._above_limit = False
        self._store_empty = False
        self._compressor_fault = False
        self._sensor_alarm = False
        self._stuck_latched = False
        self._stuck_value: float | None = None

    def set_override(self, override: ManualOverride) -> Event:
        """Arm a manual command. It is logged and it expires. Safety still wins."""
        if override.site_id != self.site.id:
            raise ValueError("override is for a different site")
        self.override = override
        return Event(
            site_id=self.site.id,
            timestamp=override.started_at,
            event_type=EventType.MANUAL_OVERRIDE,
            severity=Severity.WARNING,
            message=f"Manual override by {override.actor}: {override.reason}",
            details={
                "actor": override.actor,
                "reason": override.reason,
                "expires_at": override.expires_at.isoformat(),
                "command": override.command.model_dump(mode="json"),
            },
        )

    def step(self, reading: Reading) -> ControlDecision:
        """Decide what the actuators should do for the next step."""
        if reading.site_id != self.site.id:
            raise ValueError(f"reading site {reading.site_id} does not match {self.site.id}")
        events: list[Event] = []
        self._expire_override(reading.timestamp, events)
        if reading.tank_temp_c is not None:
            self.history.append(
                TempSample(
                    reading.timestamp,
                    reading.tank_temp_c,
                    0.0,
                    reading.milk_volume_l or 0.0,
                    reading.ambient_c,
                )
            )
            self._trim_history(reading.timestamp)

        assessment = assess_sensors(reading, self.config, self.site)
        if not assessment.active and self._analytical_stuck(reading):
            assessment = SafetyAssessment(
                active=True,
                reasons=["Tank temperature has not moved while the physics says it should"],
            )

        store = store_kwh_of(reading, self.site)
        battery = battery_kwh_of(reading, self.site)
        if assessment.active:
            result = _safe_result(self, reading, assessment, store)
            mode = ControlMode.SAFE
            if self.override is not None:
                result = StrategyResult(
                    action=result.action,
                    command=result.command,
                    reason=(
                        result.reason
                        + f" Manual override by {self.override.actor} is active but safety rules take priority."
                    ),
                )
        elif self.override is not None:
            result = StrategyResult(
                action=ControlActionKind.MANUAL,
                command=self.override.command,
                reason=(
                    f"Manual override by {self.override.actor}: {self.override.reason} "
                    f"Expires at {self.override.expires_at.strftime('%Y-%m-%d %H:%M UTC')}."
                ),
            )
            mode = ControlMode.MANUAL_OVERRIDE
        else:
            assert reading.tank_temp_c is not None
            draft = StrategyContext(
                reading=reading,
                config=self.config,
                site=self.site,
                step_hours=self.step_hours,
                tank_cooling=self.tank_cooling,
                store_charging=self.store_charging,
                backup_cooling=self.backup_cooling,
                store_kwh=store,
                battery_kwh=battery,
            )
            target = self.strategy.store_target_pct(draft)
            self._update_thermostat(reading.tank_temp_c)
            self._update_store(store, target)
            self._update_backup(reading.tank_temp_c, store)
            result = self.strategy.decide(
                StrategyContext(
                    reading=reading,
                    config=self.config,
                    site=self.site,
                    step_hours=self.step_hours,
                    tank_cooling=self.tank_cooling,
                    store_charging=self.store_charging,
                    backup_cooling=self.backup_cooling,
                    store_kwh=store,
                    battery_kwh=battery,
                    store_target_pct=target,
                )
            )
            mode = ControlMode.NORMAL

        self._remember_command(result.command, reading.timestamp)
        events.extend(self._edge_events(reading, result, assessment, store))
        snapshot = {
            "tank_temp_c": reading.tank_temp_c,
            "ambient_c": reading.ambient_c,
            "solar_kw": reading.solar_kw,
            "store_kwh": store,
            "battery_kwh": battery,
            "milk_volume_l": reading.milk_volume_l,
            "grid_available": reading.grid_available,
            "fault_flags": list(reading.fault_flags),
            "tank_cooling": self.tank_cooling,
            "store_charging": self.store_charging,
            "backup_cooling": self.backup_cooling,
            "strategy": self.strategy.name,
        }
        return ControlDecision(
            site_id=self.site.id,
            timestamp=reading.timestamp,
            mode=mode,
            action=result.action,
            reason=result.reason,
            command=result.command,
            inputs_snapshot=snapshot,
            events=events,
        )

    def _expire_override(self, now: datetime, events: list[Event]) -> None:
        if self.override is None or now < self.override.expires_at:
            return
        events.append(
            Event(
                site_id=self.site.id,
                timestamp=now,
                event_type=EventType.MANUAL_OVERRIDE_EXPIRED,
                severity=Severity.INFO,
                message=f"Manual override by {self.override.actor} expired.",
                details={"actor": self.override.actor, "reason": self.override.reason},
            )
        )
        self.override = None

    def _analytical_stuck(self, reading: Reading) -> bool:
        """Latch until the published tank temperature actually moves.

        Entering safe mode commands a lot of cooling. That must not clear the
        fault by itself, or a stuck sensor would drop out of safe mode on the
        next sample.
        """
        if reading.tank_temp_c is None:
            return self._stuck_latched
        if self._stuck_latched and self._stuck_value is not None:
            moved = abs(reading.tank_temp_c - self._stuck_value) > self.config.controller.stuck_resolution_c
            if not moved:
                return True
            self._stuck_latched = False
            self._stuck_value = None
        stuck = tank_sensor_is_stuck(
            self.history,
            self.config,
            reading.ambient_c,
            reading.milk_volume_l,
        )
        if stuck:
            self._stuck_latched = True
            self._stuck_value = reading.tank_temp_c
        return stuck

    def _update_thermostat(self, temp: float) -> None:
        """Cool above setpoint + hysteresis. Stop at or below setpoint - hysteresis."""
        start_at = self.config.tank.setpoint_c + self.config.tank.hysteresis_c
        stop_at = self.config.tank.setpoint_c - self.config.tank.hysteresis_c
        if self.tank_cooling:
            self.tank_cooling = temp > stop_at
        else:
            self.tank_cooling = temp > start_at

    def _update_store(self, store_kwh: float | None, target: float) -> None:
        capacity = self.site.cold_store_kwh_thermal
        if store_kwh is None or capacity <= 0:
            self.store_charging = False
            return
        soc = 100.0 * store_kwh / capacity
        resume_below = target - self.config.controller.store_target_hysteresis_pct
        if self.store_charging:
            self.store_charging = soc < target
        else:
            self.store_charging = soc < resume_below

    def _update_backup(self, temp: float, store_kwh: float | None) -> None:
        """Start only above 6°C with the store below 0.5 kWh. Stop with a margin."""
        if store_kwh is None:
            return
        start_temp = self.config.tank.backup_start_temp_c
        clear_temp = self.config.tank.backup_clear_temp_c
        start_store = self.config.controller.backup_store_max_kwh
        clear_store = self.config.controller.backup_store_clear_kwh
        if not self.backup_cooling:
            self.backup_cooling = temp > start_temp and store_kwh < start_store
        elif temp <= clear_temp or store_kwh >= clear_store:
            self.backup_cooling = False

    def _remember_command(self, command: ActuatorCommand, timestamp: datetime) -> None:
        """Record heat the tank was asked to lose.

        ``tank_cooling_kw_thermal`` is a cap. Store discharge fills that cap.
        Adding the two together makes a healthy hold look like a stuck sensor.
        """
        if not self.history or self.history[-1].timestamp != timestamp:
            return
        if command.compressor_target == CompressorTarget.STORE:
            cooling_kw = 0.0
        elif command.tank_cooling_kw_thermal is not None:
            cooling_kw = command.tank_cooling_kw_thermal
        else:
            cooling_kw = self.config.tank.max_heat_removal_kw_thermal
        self.history[-1].commanded_tank_kwh = max(0.0, cooling_kw) * self.step_hours

    def _trim_history(self, now: datetime) -> None:
        keep = timedelta(minutes=self.config.controller.stuck_window_minutes * 2)
        cutoff = now - keep
        self.history = [sample for sample in self.history if sample.timestamp >= cutoff]

    def _edge_events(
        self,
        reading: Reading,
        result: StrategyResult,
        assessment: SafetyAssessment,
        store: float | None,
    ) -> list[Event]:
        events: list[Event] = []
        if assessment.active and not self._was_safe:
            events.append(
                _event(
                    reading,
                    EventType.SAFE_MODE_ENTERED,
                    Severity.CRITICAL,
                    "Safe mode. " + "; ".join(assessment.reasons) + ".",
                )
            )
            if any("stuck" in reason.lower() or "has not moved" in reason.lower() for reason in assessment.reasons):
                events.append(
                    _event(reading, EventType.SENSOR_STUCK, Severity.CRITICAL, "Tank or store sensor looks stuck.")
                )
            if any("missing" in reason.lower() or "dropped out" in reason.lower() for reason in assessment.reasons):
                events.append(
                    _event(reading, EventType.SENSOR_LOST, Severity.CRITICAL, "A cooling sensor reading was lost.")
                )
        elif not assessment.active and self._was_safe:
            events.append(_event(reading, EventType.SAFE_MODE_CLEARED, Severity.INFO, "Sensors look valid again. Leaving safe mode."))
        self._was_safe = assessment.active

        backup_on = bool(result.command.allow_backup_compressor)
        if backup_on and not self._backup_command:
            events.append(_event(reading, EventType.BACKUP_STARTED, Severity.WARNING, result.reason))
        elif not backup_on and self._backup_command:
            events.append(_event(reading, EventType.BACKUP_STOPPED, Severity.INFO, "Backup cooling stopped."))
        self._backup_command = backup_on

        limit = self.config.sla.milk_safe_temp_c
        milk = reading.milk_volume_l or 0
        hot = reading.tank_temp_c is not None and milk > 0 and reading.tank_temp_c > limit
        if hot and not self._above_limit:
            events.append(
                _event(
                    reading,
                    EventType.TEMP_LIMIT_EXCEEDED,
                    Severity.CRITICAL,
                    f"Tank is {reading.tank_temp_c:.1f}°C with milk in it. The safe limit is {limit:.1f}°C.",
                )
            )
        elif not hot and self._above_limit:
            events.append(_event(reading, EventType.TEMP_LIMIT_CLEARED, Severity.INFO, "Tank is back within the milk-safe limit."))
        self._above_limit = hot

        empty_below = self.config.controller.backup_store_max_kwh
        if store is not None and store < empty_below and not self._store_empty:
            events.append(
                _event(
                    reading,
                    EventType.STORE_EMPTY,
                    Severity.WARNING,
                    f"Cold store is down to {store:.2f} kWh.",
                )
            )
            self._store_empty = True
        elif store is not None and store >= self.config.controller.backup_store_clear_kwh and self._store_empty:
            events.append(_event(reading, EventType.STORE_RECOVERED, Severity.INFO, "Cold store has recovered above the backup line."))
            self._store_empty = False

        fault = "compressor_failure" in reading.fault_flags
        if fault and not self._compressor_fault:
            events.append(
                _event(reading, EventType.COMPRESSOR_FAULT, Severity.CRITICAL, "Compressor reports a failure. Store cooling continues.")
            )
        self._compressor_fault = fault
        return events


def _safe_result(
    controller: Controller,
    reading: Reading,
    assessment: SafetyAssessment,
    store: float | None,
) -> StrategyResult:
    """Full cooling. The plant freeze cut-out is what stops the tank going below 1°C."""
    config = controller.config
    allow_backup = store is None or store < config.controller.backup_store_max_kwh
    fraction = config.controller.safe_mode_compressor_fraction
    command = ActuatorCommand(
        compressor_kw_electric=config.compressor.max_kw_electric * fraction,
        compressor_target=CompressorTarget.TANK,
        tank_cooling_kw_thermal=config.tank.max_heat_removal_kw_thermal,
        store_draw_kw_thermal=config.cold_store.max_draw_kw_thermal,
        aux_kw=config.aux.load_kw,
        allow_backup_compressor=allow_backup,
        allow_battery_aux=True,
        allow_backup_aux=config.aux.backup_allowed,
        charge_battery_from_surplus=False,
    )
    reason = (
        "Safe mode: "
        + "; ".join(assessment.reasons)
        + ". The compressor stays enabled and the cold store can discharge."
    )
    if allow_backup:
        reason += " Backup cooling is allowed because the store is low or unknown."
    else:
        reason += " Backup stays off while the store still has a measured charge."
    return StrategyResult(action=ControlActionKind.SAFE_MODE_COOL, command=command, reason=reason)


def _event(reading: Reading, event_type: EventType, severity: Severity, message: str) -> Event:
    return Event(
        site_id=reading.site_id,
        timestamp=reading.timestamp,
        event_type=event_type,
        severity=severity,
        message=message,
    )
