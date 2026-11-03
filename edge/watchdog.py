"""If the controller stops beating, actuators fall back to a safe command.

The safe command allows the compressor to run. A dead controller must not
leave milk sitting with the contactor forced open.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from shared.config import RootConfig
from shared.enums import CompressorTarget, ControlActionKind, ControlMode
from shared.schemas import ActuatorCommand


class Watchdog:
    def __init__(self, timeout_seconds: float) -> None:
        if timeout_seconds <= 0:
            raise ValueError("watchdog timeout must be positive")
        self.timeout = timedelta(seconds=timeout_seconds)
        self.last_beat: datetime | None = None

    def beat(self, now: datetime) -> None:
        self.last_beat = now

    def expired(self, now: datetime) -> bool:
        if self.last_beat is None:
            return False
        return now - self.last_beat > self.timeout

    def fallback_command(self, config: RootConfig) -> ActuatorCommand:
        """Compressor allowed at its safe-mode fraction, store allowed to discharge."""
        fraction = config.controller.safe_mode_compressor_fraction
        return ActuatorCommand(
            compressor_kw_electric=config.compressor.max_kw_electric * fraction,
            compressor_target=CompressorTarget.TANK,
            tank_cooling_kw_thermal=config.tank.max_heat_removal_kw_thermal,
            store_draw_kw_thermal=config.cold_store.max_draw_kw_thermal,
            aux_kw=config.aux.load_kw,
            allow_backup_compressor=True,
            allow_battery_aux=True,
            allow_backup_aux=config.aux.backup_allowed,
            charge_battery_from_surplus=False,
        )

    def fallback_reason(self) -> str:
        return (
            "Controller heartbeat is late. Watchdog is allowing the compressor to run "
            "and the cold store to discharge until the controller recovers."
        )

    def fallback_action(self) -> ControlActionKind:
        return ControlActionKind.SAFE_MODE_COOL

    def fallback_mode(self) -> ControlMode:
        return ControlMode.SAFE
