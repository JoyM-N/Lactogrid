"""Print a short trace from a scenario. Useful before the dashboard exists.

Example: python -m simulator --scenario config/scenarios/sunny_day.yaml --steps 8
"""

from __future__ import annotations

import argparse
from zoneinfo import ZoneInfo

from shared.config import load_scenario
from shared.paths import repo_root
from simulator.engine import SiteSimulator


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a LactoGrid site scenario open-loop")
    parser.add_argument("--scenario", required=True, help="Path to a scenario YAML file")
    parser.add_argument("--steps", type=int, default=15, help="How many steps to print")
    args = parser.parse_args()
    path = repo_root() / args.scenario if not args.scenario.startswith("/") else args.scenario
    loaded = load_scenario(path)
    sim = SiteSimulator(loaded)
    zone = ZoneInfo(loaded.scenario.timezone)
    print(
        f"{loaded.scenario.name}: {loaded.scenario.description}\n"
        f"site={loaded.scenario.site.id} step={loaded.scenario.step_seconds}s"
    )
    print(f"{'local time':<22} {'tank C':>8} {'milk L':>8} {'solar kW':>9} {'store %':>8} {'link':>6}")
    shown = 0
    while not sim.finished() and shown < args.steps:
        result = sim.advance()
        local = result.reading.timestamp.astimezone(zone)
        tank = result.reading.tank_temp_c
        solar = result.reading.solar_kw
        milk = result.reading.milk_volume_l
        store = result.reading.store_soc_pct
        tank_s = f"{tank:.2f}" if tank is not None else "—"
        solar_s = f"{solar:.2f}" if solar is not None else "—"
        milk_s = f"{milk:.1f}" if milk is not None else "—"
        store_s = f"{store:.1f}" if store is not None else "—"
        print(
            f"{local.strftime('%Y-%m-%d %H:%M'):<22} {tank_s:>8} {milk_s:>8} "
            f"{solar_s:>9} {store_s:>8} {'up' if result.link_up else 'down':>6}"
        )
        shown += 1


if __name__ == "__main__":
    main()
