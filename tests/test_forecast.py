"""Forecast mode raises the store target and still loses to safety."""

from edge.controller import Controller
from edge.forecast import FixedOutlook, ForecastStrategy
from shared.enums import ControlMode
from tests.test_controller import _reading


def test_cloudy_outlook_raises_the_store_target_and_writes_it_down(defaults, demo_site) -> None:
    controller = Controller(defaults, demo_site, strategy=ForecastStrategy(FixedOutlook(0.8)))
    decision = controller.step(_reading(demo_site, tank_temp_c=4.0, store_kwh=45.0, solar_kw=8.0))
    assert decision.mode == ControlMode.NORMAL
    assert "target 100%" in decision.reason
    assert "80%" in decision.reason
    assert decision.command.allow_backup_compressor is False


def test_a_low_store_trims_pumps_and_lights(defaults, demo_site) -> None:
    controller = Controller(defaults, demo_site, strategy=ForecastStrategy(FixedOutlook(0.2)))
    decision = controller.step(_reading(demo_site, tank_temp_c=4.0, store_kwh=10.0, solar_kw=8.0))
    assert decision.command.aux_kw == defaults.aux.load_kw * (1 - defaults.controller.forecast_aux_trim_fraction)
    assert "trimmed" in decision.reason


def test_safety_still_wins_over_the_forecast(defaults, demo_site) -> None:
    controller = Controller(defaults, demo_site, strategy=ForecastStrategy(FixedOutlook(1.0)))
    decision = controller.step(_reading(demo_site, tank_temp_c=None))
    assert decision.mode == ControlMode.SAFE
    assert decision.reason.startswith("Safe mode")
    assert decision.command.compressor_kw_electric > 0
