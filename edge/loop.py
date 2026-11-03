"""Close the controller around the simulator and write the local log."""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.config import LoadedScenario
from shared.enums import EventType, Severity
from shared.schemas import Event
from edge.controller import ControlDecision, Controller
from edge.store import EdgeStore
from edge.watchdog import Watchdog
from simulator.engine import SiteSimulator


@dataclass
class LoopResult:
    readings: list = field(default_factory=list)
    decisions: list[ControlDecision] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


def run_closed_loop(loaded: LoadedScenario, database: EdgeStore | None = None, strategy=None) -> LoopResult:
    """Run one scenario. The controller sees the pre-step sensors, including faults.

    A crash in the strategy logs a watchdog event and applies the safe command
    for that step, then the loop continues.
    """
    simulator = SiteSimulator(loaded)
    controller = Controller(
        loaded.config,
        loaded.scenario.site,
        strategy=strategy,
        step_hours=loaded.scenario.step_seconds / 3600.0,
    )
    watchdog = Watchdog(loaded.config.watchdog.timeout_seconds)
    outcome = LoopResult()
    link_was_up = True
    while not simulator.finished():
        observation = simulator.observe()
        try:
            decision = controller.step(observation)
            watchdog.beat(observation.timestamp)
        except Exception as exc:  # a crashed scan must not open the compressor contactor
            decision = ControlDecision(
                site_id=loaded.scenario.site.id,
                timestamp=observation.timestamp,
                mode=watchdog.fallback_mode(),
                action=watchdog.fallback_action(),
                reason=watchdog.fallback_reason() + f" ({type(exc).__name__})",
                command=watchdog.fallback_command(loaded.config),
                inputs_snapshot={"error": str(exc)},
                events=[
                    Event(
                        site_id=loaded.scenario.site.id,
                        timestamp=observation.timestamp,
                        event_type=EventType.WATCHDOG_RESTART,
                        severity=Severity.CRITICAL,
                        message=watchdog.fallback_reason(),
                        details={"error": str(exc)},
                    )
                ],
            )
        step = simulator.advance(decision.command)
        link_event = None
        if step.link_up != link_was_up:
            link_event = _link_event(observation, step.link_up)
            outcome.events.append(link_event)
            link_was_up = step.link_up
        outcome.events.extend(decision.events)
        outcome.readings.append(step.reading)
        outcome.decisions.append(decision)
        if database is not None:
            database.add_reading(step.reading)
            database.add_action(decision.to_action())
            for event in decision.events:
                database.add_event(event)
            if link_event is not None:
                database.add_event(link_event)
    return outcome


def _link_event(observation, link_up: bool) -> Event:
    if link_up:
        return Event(
            site_id=observation.site_id,
            timestamp=observation.timestamp,
            event_type=EventType.COMMS_RESTORED,
            severity=Severity.INFO,
            message="Internet link is back. Queued records can sync.",
        )
    return Event(
        site_id=observation.site_id,
        timestamp=observation.timestamp,
        event_type=EventType.COMMS_LOST,
        severity=Severity.WARNING,
        message="Internet link is down. The site keeps controlling and logging locally.",
    )
