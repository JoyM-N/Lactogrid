"""Step a site through time and publish sensor readings, including injected faults."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from shared.config import LoadedScenario, RootConfig, Scenario
from shared.enums import FaultType
from shared.keys import normalise_ts
from shared.physics import (
    PlantState,
    PowerAllocation,
    ambient_c,
    cloud_multiplier,
    crosses_collection,
    milk_arriving_litres,
    solar_kw,
    step_plant,
)
from shared.schemas import ActuatorCommand, FaultSpec, Reading

Policy = Callable[[Reading], ActuatorCommand]


@dataclass
class StepResult:
    """One simulated minute.

    ``observation`` is what a sensor would have reported at the start of the
    step (this is what the controller should see). ``reading`` is the sample
    at the end of the step, including the power that actually flowed.
    """

    observation: Reading
    reading: Reading
    command: ActuatorCommand
    link_up: bool
    milk_added_l: float
    spilled_l: float
    collected: bool
    truth_tank_temp_c: float
    truth_milk_volume_l: float
    truth_store_kwh: float


@dataclass
class _FaultMemory:
    stuck: dict[str, float] = field(default_factory=dict)


class SiteSimulator:
    """Deterministic site. Call ``advance`` with an actuator command, or ``run`` with a policy."""

    def __init__(self, loaded: LoadedScenario) -> None:
        scenario = loaded.scenario
        self.scenario: Scenario = scenario
        self.config: RootConfig = loaded.config
        self.timezone = ZoneInfo(scenario.timezone)
        self.start = scenario.start
        self.end = scenario.start + timedelta(hours=scenario.duration_hours)
        self.step_seconds = scenario.step_seconds
        self.now = scenario.start
        self._previous = scenario.start - timedelta(seconds=scenario.step_seconds)
        store_kwh = scenario.site.cold_store_kwh_thermal * scenario.initial_state.store_soc_pct / 100.0
        battery_kwh = scenario.site.battery_kwh * scenario.initial_state.battery_soc_pct / 100.0
        self.state = PlantState(
            tank_temp_c=scenario.initial_state.tank_temp_c,
            milk_volume_l=scenario.initial_state.milk_volume_l,
            store_kwh=store_kwh,
            battery_kwh=battery_kwh,
        )
        self._flows = PowerAllocation()
        self._thermal = (0.0, 0.0)
        self._faults = _FaultMemory()
        self._spilled_last = 0.0

    def finished(self) -> bool:
        return self.now >= self.end

    def link_up_at(self, moment: datetime) -> bool:
        return not any(fault.type == FaultType.INTERNET_OUTAGE for fault in self._active(moment))

    def observe(self) -> Reading:
        """Sensor view at ``self.now``, with flows from the step that just ended."""
        return self._publish(self.now, self.state, self._flows, self._thermal, self._spilled_last)

    def advance(self, command: ActuatorCommand | None = None) -> StepResult:
        if self.finished():
            raise RuntimeError("scenario has already finished")
        command = command or ActuatorCommand()
        observation = self.observe()
        local_now = self.now.astimezone(self.timezone)
        local_next = (self.now + timedelta(seconds=self.step_seconds)).astimezone(self.timezone)
        step_hours = self.step_seconds / 3600.0
        env = self._environment(self.now, local_now, local_next, step_hours)
        new_state, energy = step_plant(
            self.state,
            ambient=env["ambient"],
            solar_kw_available=env["solar"],
            grid_available=env["grid"],
            diesel_available=self.scenario.site.diesel_available,
            compressor_failed=env["compressor_failed"],
            milk_added_l=env["milk"],
            collect=env["collect"],
            command=command,
            physics=self.config.physics,
            compressor=self.config.compressor,
            cold_store=self.config.cold_store,
            tank=self.config.tank,
            battery=self.config.battery,
            store_capacity_kwh=self.scenario.site.cold_store_kwh_thermal,
            tank_capacity_l=self.scenario.site.tank_litres,
            step_hours=step_hours,
        )
        self.state = new_state
        self._flows = energy.allocation
        self._thermal = (
            energy.from_store_kwh / step_hours if step_hours else 0.0,
            energy.to_store_kwh / step_hours if step_hours else 0.0,
        )
        self._spilled_last = energy.spilled_l
        self._previous = self.now
        self.now = normalise_ts(self.now + timedelta(seconds=self.step_seconds))
        reading = self._publish(self.now, self.state, self._flows, self._thermal, energy.spilled_l)
        return StepResult(
            observation=observation,
            reading=reading,
            command=command,
            link_up=self.link_up_at(self._previous),
            milk_added_l=energy.milk_added_l,
            spilled_l=energy.spilled_l,
            collected=energy.collected,
            truth_tank_temp_c=self.state.tank_temp_c,
            truth_milk_volume_l=self.state.milk_volume_l,
            truth_store_kwh=self.state.store_kwh,
        )

    def run(self, policy: Policy | None = None) -> list[StepResult]:
        """Run until the scenario ends. ``policy`` sees the pre-step observation."""
        choose = policy or (lambda _reading: ActuatorCommand())
        results: list[StepResult] = []
        while not self.finished():
            observation = self.observe()
            results.append(self.advance(choose(observation)))
        return results

    def _active(self, moment: datetime) -> list[FaultSpec]:
        stamp = normalise_ts(moment)
        return [fault for fault in self.scenario.faults if fault.start <= stamp < fault.end]

    def _environment(
        self,
        moment: datetime,
        local_now: datetime,
        local_next: datetime,
        step_hours: float,
    ) -> dict:
        physics = self.config.physics
        active = self._active(moment)
        cloud = cloud_multiplier(moment, self.scenario.seed, self.scenario.weather.cloudiness, physics)
        for fault in active:
            if fault.type == FaultType.HEAVY_CLOUD and fault.factor is not None:
                cloud = fault.factor
        available = solar_kw(local_now, self.scenario.site.solar_kwp, cloud, physics)
        ambient = ambient_c(local_now, physics)
        grid = self.scenario.site.grid_connected
        compressor_failed = False
        for fault in active:
            if fault.type == FaultType.GRID_OUTAGE:
                grid = False
            elif fault.type == FaultType.COMPRESSOR_FAILURE:
                compressor_failed = True
        milk = milk_arriving_litres(
            local_now,
            step_hours,
            self.scenario.site.daily_milk_litres,
            physics,
        )
        collect = crosses_collection(
            local_now,
            local_next,
            physics.collection_hour_local,
            physics.collection_minute_local,
        )
        return {
            "solar": available,
            "ambient": ambient,
            "grid": grid,
            "compressor_failed": compressor_failed,
            "milk": milk,
            "collect": collect,
        }

    def _publish(
        self,
        moment: datetime,
        state: PlantState,
        flows: PowerAllocation,
        thermal: tuple[float, float],
        spilled: float,
    ) -> Reading:
        local = moment.astimezone(self.timezone)
        physics = self.config.physics
        active = self._active(moment)
        cloud = cloud_multiplier(moment, self.scenario.seed, self.scenario.weather.cloudiness, physics)
        grid = self.scenario.site.grid_connected
        for fault in active:
            if fault.type == FaultType.HEAVY_CLOUD and fault.factor is not None:
                cloud = fault.factor
            if fault.type == FaultType.GRID_OUTAGE:
                grid = False
        solar = solar_kw(local, self.scenario.site.solar_kwp, cloud, physics)
        ambient = ambient_c(local, physics)
        capacity = self.scenario.site.cold_store_kwh_thermal
        battery_capacity = self.scenario.site.battery_kwh
        store_soc = 100.0 * state.store_kwh / capacity if capacity else 0.0
        battery_soc = 100.0 * state.battery_kwh / battery_capacity if battery_capacity else 0.0
        flags: list[str] = []
        if spilled > 0:
            flags.append("tank_overflow")
        for fault in active:
            if fault.type == FaultType.COMPRESSOR_FAILURE:
                flags.append("compressor_failure")
        draw_kw, charge_kw = thermal
        reading = Reading(
            site_id=self.scenario.site.id,
            timestamp=moment,
            tank_temp_c=state.tank_temp_c,
            store_temp_c=None,
            store_soc_pct=store_soc,
            store_kwh=state.store_kwh,
            ambient_c=ambient,
            solar_kw=solar,
            solar_used_kw=flows.solar_used_kw,
            compressor_kw=flows.compressor_kw,
            compressor_from_solar_kw=flows.compressor_from_solar_kw,
            compressor_from_backup_kw=flows.compressor_from_backup_kw,
            load_kw=flows.load_kw,
            aux_from_solar_kw=flows.aux_from_solar_kw,
            aux_from_battery_kw=flows.aux_from_battery_kw,
            aux_from_backup_kw=flows.aux_from_backup_kw,
            battery_soc_pct=battery_soc,
            battery_kwh=state.battery_kwh,
            battery_kw=flows.battery_discharge_kw - flows.battery_charge_ac_kw,
            grid_available=grid,
            backup_kw=flows.backup_kw,
            backup_from_grid_kw=flows.backup_from_grid_kw,
            backup_from_diesel_kw=flows.backup_from_diesel_kw,
            store_draw_kw_thermal=draw_kw,
            store_charge_kw_thermal=charge_kw,
            milk_volume_l=state.milk_volume_l,
            door_open=False,
            fault_flags=flags,
        )
        return _apply_sensor_faults(reading, active, self._faults)


def _apply_sensor_faults(reading: Reading, faults: list[FaultSpec], memory: _FaultMemory) -> Reading:
    """Faults change the published reading only. The plant state is left alone."""
    data = reading.model_dump()
    flags = list(data["fault_flags"])
    active_stuck: set[str] = set()
    for fault in faults:
        if fault.type == FaultType.SENSOR_DROPOUT and fault.sensor:
            data[fault.sensor] = None
            flags.append(f"sensor_dropout:{fault.sensor}")
        elif fault.type == FaultType.STUCK_SENSOR and fault.sensor:
            active_stuck.add(fault.sensor)
            if fault.sensor not in memory.stuck:
                memory.stuck[fault.sensor] = (
                    fault.value if fault.value is not None else data[fault.sensor]
                )
            data[fault.sensor] = memory.stuck[fault.sensor]
            flags.append(f"stuck_sensor:{fault.sensor}")
    for sensor in list(memory.stuck):
        if sensor not in active_stuck:
            del memory.stuck[sensor]
    data["fault_flags"] = sorted(set(flags))
    return Reading.model_validate(data)
