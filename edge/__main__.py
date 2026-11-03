"""Run the rule controller against a scenario and print a short summary.

Example: python -m edge --scenario config/scenarios/sunny_day.yaml
"""

from __future__ import annotations

import argparse
from zoneinfo import ZoneInfo

from shared.config import load_scenario
from shared.paths import repo_root
from edge.loop import run_closed_loop


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LactoGrid controller on a scenario")
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()
    path = repo_root() / args.scenario if not str(args.scenario).startswith("/") else args.scenario
    loaded = load_scenario(path)
    result = run_closed_loop(loaded)
    zone = ZoneInfo(loaded.scenario.timezone)
    hours = loaded.scenario.step_seconds / 3600.0
    with_milk = [
        reading.tank_temp_c
        for reading in result.readings
        if (reading.milk_volume_l or 0) > 0 and reading.tank_temp_c is not None
    ]
    backup = sum((reading.backup_kw or 0.0) * hours for reading in result.readings)
    days = loaded.scenario.duration_hours / 24.0
    peak = max(with_milk) if with_milk else float("nan")
    print(f"{loaded.scenario.name}: {len(result.decisions)} decisions, {len(result.events)} events")
    print(f"peak tank temperature with milk: {peak:.2f} C")
    print(f"backup energy: {backup:.2f} kWh over {days:.1f} days ({backup / days:.2f} kWh/day)")
    print("last reasons:")
    seen: list[str] = []
    for decision in result.decisions:
        if not seen or seen[-1] != decision.reason:
            seen.append(decision.reason)
    for reason in seen[-8:]:
        print(f"  - {reason}")
    if result.events:
        last = result.events[-1]
        local = last.timestamp.astimezone(zone)
        print(f"last event {local.strftime('%Y-%m-%d %H:%M')}: {last.event_type.value}: {last.message}")


if __name__ == "__main__":
    main()
