"""Forecast mode.

The rule strategy still allocates power. This strategy only changes two
inputs the rules already understand: the cold-store target, and how hard the
pumps and lights may draw when the store is low.

A cloud outlook of 0 keeps the base target. A heavier outlook raises it by
``forecast_store_extra_soc_pct``, capped at 100%. Safety mode and a manual
override are applied by the controller before this strategy runs, so a
forecast cannot turn cooling off.

The outlook here is a number the caller supplies. A live weather feed is
``TODO(verify)``.
"""

from __future__ import annotations

from datetime import datetime

from shared.schemas import Site
from edge.rule import RuleStrategy
from edge.strategy import StrategyContext, StrategyResult


class FixedOutlook:
    """One cloudiness figure for the whole run. 0 is clear, 1 is overcast."""

    def __init__(self, cloudiness: float) -> None:
        self.cloudiness = min(1.0, max(0.0, cloudiness))

    def get_cloud_outlook(self, site: Site, moment: datetime) -> float:
        del site, moment
        return self.cloudiness


class ForecastStrategy(RuleStrategy):
    """Rules, with the store target lifted when cloud is expected."""

    name = "forecast"

    def __init__(self, outlook: FixedOutlook) -> None:
        self.outlook = outlook

    def store_target_pct(self, ctx: StrategyContext) -> float:
        cloud = self.outlook.get_cloud_outlook(ctx.site, ctx.reading.timestamp)
        control = ctx.config.controller
        lifted = control.forecast_store_base_soc_pct + control.forecast_store_extra_soc_pct * min(
            1.0, cloud * control.forecast_cloud_gain
        )
        return min(100.0, lifted)

    def aux_kw(self, ctx: StrategyContext) -> float:
        load = ctx.config.aux.load_kw
        if ctx.store_kwh is None or ctx.site.cold_store_kwh_thermal <= 0:
            return load
        soc = 100.0 * ctx.store_kwh / ctx.site.cold_store_kwh_thermal
        if soc < ctx.config.controller.forecast_aux_trim_store_soc_pct:
            return load * (1.0 - ctx.config.controller.forecast_aux_trim_fraction)
        return load

    def decide(self, ctx: StrategyContext) -> StrategyResult:
        result = super().decide(ctx)
        cloud = self.outlook.get_cloud_outlook(ctx.site, ctx.reading.timestamp)
        reason = (
            result.reason
            + f" Forecast cloud outlook is {cloud:.0%}, so the cold-store target is {ctx.store_target_pct:.0f}%."
        )
        if self.aux_kw(ctx) < ctx.config.aux.load_kw:
            reason += " Pumps and lights are trimmed because the store is under half."
        return StrategyResult(action=result.action, command=result.command, reason=reason)
