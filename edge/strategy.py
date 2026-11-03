"""Controller strategy interface.

``RuleStrategy`` is the Phase 1 controller. ``ForecastStrategy`` (Phase 2)
will implement the same method so the loop does not change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from shared.config import RootConfig
from shared.schemas import ActuatorCommand, Reading, Site
from shared.enums import ControlActionKind


@dataclass(frozen=True)
class StrategyContext:
    """Everything a strategy may use. Strategies do not touch the database."""

    reading: Reading
    config: RootConfig
    site: Site
    step_hours: float
    tank_cooling: bool
    store_charging: bool
    backup_cooling: bool
    store_kwh: float | None
    battery_kwh: float | None
    store_target_pct: float = 75.0


@dataclass(frozen=True)
class StrategyResult:
    action: ControlActionKind
    command: ActuatorCommand
    reason: str


class Strategy(Protocol):
    """Pick an actuator command and explain it in plain language."""

    name: str

    def decide(self, ctx: StrategyContext) -> StrategyResult:
        """Return the command for this sample. Must not have side effects."""
