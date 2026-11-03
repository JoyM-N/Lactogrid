"""Guarantee figures from stored readings.

Every number is recomputed from the samples. A missing tank temperature while
milk is present counts as not safe, and it lowers completeness. The annual
fee is left blank until a pilot prices it. The credit share comes from the
configured tiers and applies to milking-window uptime.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from io import BytesIO, StringIO
from zoneinfo import ZoneInfo

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

from shared.config import RootConfig, SlaConfig


MINUTE_HOURS = 1 / 60


@dataclass
class WindowReport:
    name: str
    litres: float = 0.0
    milk_samples: int = 0
    safe_samples: int = 0
    missing_samples: int = 0
    peak_tank_c: float | None = None
    failures: int = 0
    _unsafe_run: bool = field(default=False, repr=False)

    @property
    def uptime_pct(self) -> float | None:
        if self.milk_samples == 0:
            return None
        return 100.0 * self.safe_samples / self.milk_samples

    def add(self, temp: float | None, limit: float, litres: float) -> None:
        self.litres += litres
        self.milk_samples += 1
        unsafe = temp is None or temp > limit
        if temp is None:
            self.missing_samples += 1
        else:
            self.peak_tank_c = temp if self.peak_tank_c is None else max(self.peak_tank_c, temp)
        if unsafe and not self._unsafe_run:
            self.failures += 1
        self._unsafe_run = unsafe
        if not unsafe:
            self.safe_samples += 1


@dataclass
class GuaranteeReport:
    period: str
    title: str
    recorded_days: int
    span_days: int
    milk_safe_uptime_pct: float | None
    completeness_pct: float | None
    solar_kwh: float
    backup_kwh: float
    peak_tank_c: float | None
    morning: WindowReport
    evening: WindowReport
    window_uptime_pct: float | None
    failures: int
    credit_share_pct: float
    fee: None
    summary: str

    def as_dict(self) -> dict:
        return {
            "period": self.period,
            "title": self.title,
            "recorded_days": self.recorded_days,
            "span_days": self.span_days,
            "milk_safe_uptime_pct": _round(self.milk_safe_uptime_pct),
            "completeness_pct": _round(self.completeness_pct),
            "solar_kwh": round(self.solar_kwh, 2),
            "backup_kwh": round(self.backup_kwh, 2),
            "peak_tank_c": _round(self.peak_tank_c, 1),
            "morning": _window_dict(self.morning),
            "evening": _window_dict(self.evening),
            "window_uptime_pct": _round(self.window_uptime_pct),
            "failures": self.failures,
            "credit_share_pct": self.credit_share_pct,
            "fee": self.fee,
            "fee_note": "Fee not set. The share is the credit rule. The amount waits on a priced annual plan.",
            "summary": self.summary,
        }


def build_guarantee(readings: list, config: RootConfig, timezone_name: str, period: str) -> GuaranteeReport:
    """Add up one day, the week that contains it, or that calendar month."""
    if period not in {"day", "week", "month"}:
        raise ValueError("period must be day, week, or month")
    zone = ZoneInfo(timezone_name)
    ordered = sorted(readings, key=lambda row: row.timestamp)
    chosen, title, recorded, span = _slice(ordered, period, zone)
    physics = config.physics
    limit = config.sla.milk_safe_temp_c
    morning = WindowReport("Morning delivery")
    evening = WindowReport("Evening delivery")
    milk_samples = 0
    safe_samples = 0
    known_samples = 0
    solar = 0.0
    backup = 0.0
    peak = None
    previous_volume = None
    for row in chosen:
        local = row.timestamp.astimezone(zone)
        hour = local.hour + local.minute / 60.0
        volume = row.milk_volume_l or 0.0
        arrived = 0.0
        if previous_volume is not None and volume > previous_volume:
            arrived = volume - previous_volume
        previous_volume = volume
        solar += (row.solar_used_kw or 0.0) * MINUTE_HOURS
        backup += (row.backup_kw or 0.0) * MINUTE_HOURS
        if volume <= 0:
            morning._unsafe_run = False
            evening._unsafe_run = False
            continue
        temp = row.tank_temp_c
        milk_samples += 1
        if temp is None:
            pass
        else:
            known_samples += 1
            peak = temp if peak is None else max(peak, temp)
            if temp <= limit:
                safe_samples += 1
        if physics.morning_start_hour <= hour < physics.morning_end_hour:
            morning.add(temp, limit, arrived)
        elif physics.evening_start_hour <= hour < physics.evening_end_hour:
            evening.add(temp, limit, arrived)
    uptime = (100.0 * safe_samples / milk_samples) if milk_samples else None
    completeness = (100.0 * known_samples / milk_samples) if milk_samples else None
    window_samples = morning.milk_samples + evening.milk_samples
    window_safe = morning.safe_samples + evening.safe_samples
    window_uptime = (100.0 * window_safe / window_samples) if window_samples else None
    failures = morning.failures + evening.failures
    share = credit_share(window_uptime, config.sla)
    summary = _summary(window_uptime, failures, share)
    return GuaranteeReport(
        period=period,
        title=title,
        recorded_days=recorded,
        span_days=span,
        milk_safe_uptime_pct=uptime,
        completeness_pct=completeness,
        solar_kwh=solar,
        backup_kwh=backup,
        peak_tank_c=peak,
        morning=morning,
        evening=evening,
        window_uptime_pct=window_uptime,
        failures=failures,
        credit_share_pct=share,
        fee=None,
        summary=summary,
    )


def credit_share(uptime_pct: float | None, sla: SlaConfig) -> float:
    """Highest tier whose floor the milking-window uptime still clears."""
    if uptime_pct is None:
        return 0.0
    tiers = sorted(sla.discount_tiers, key=lambda tier: tier.min_uptime_pct, reverse=True)
    for tier in tiers:
        if uptime_pct >= tier.min_uptime_pct:
            return tier.discount_pct
    return tiers[-1].discount_pct


def render_csv(report: GuaranteeReport) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "window",
            "milk_safe_pct",
            "litres",
            "failures",
            "solar_kwh",
            "backup_kwh",
            "peak_tank_c",
            "credit_share_pct",
            "fee",
        ]
    )
    body = report.as_dict()
    writer.writerow(
        [
            "all hours with milk",
            body["milk_safe_uptime_pct"],
            "",
            body["failures"],
            body["solar_kwh"],
            body["backup_kwh"],
            body["peak_tank_c"],
            body["credit_share_pct"],
            "",
        ]
    )
    for key in ("morning", "evening"):
        window = body[key]
        writer.writerow(
            [
                window["name"],
                window["uptime_pct"],
                window["litres"],
                window["failures"],
                "",
                "",
                window["peak_tank_c"],
                "",
                "",
            ]
        )
    writer.writerow(["note", body["fee_note"]])
    return buffer.getvalue()


def render_pdf(site_name: str, report: GuaranteeReport) -> bytes:
    body = report.as_dict()
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, title=f"{site_name} guarantee")
    styles = getSampleStyleSheet()
    rows = [
        ["", "Milk-safe %", "Litres", "Failures", "Peak °C"],
        [
            "All hours with milk",
            _blank(body["milk_safe_uptime_pct"]),
            "",
            str(body["failures"]),
            _blank(body["peak_tank_c"]),
        ],
        [
            body["morning"]["name"],
            _blank(body["morning"]["uptime_pct"]),
            _blank(body["morning"]["litres"]),
            str(body["morning"]["failures"]),
            _blank(body["morning"]["peak_tank_c"]),
        ],
        [
            body["evening"]["name"],
            _blank(body["evening"]["uptime_pct"]),
            _blank(body["evening"]["litres"]),
            str(body["evening"]["failures"]),
            _blank(body["evening"]["peak_tank_c"]),
        ],
        ["Solar used (kWh)", _blank(body["solar_kwh"]), "", "", ""],
        ["Backup (kWh)", _blank(body["backup_kwh"]), "", "", ""],
        ["Credit share of next payment", f"{body['credit_share_pct']:.0f}%", "", "", ""],
        ["Annual fee", "Not set", "", "", ""],
    ]
    story = [
        Paragraph(site_name, styles["Title"]),
        Paragraph(body["title"], styles["Heading2"]),
        Paragraph(
            f"{body['recorded_days']} of {body['span_days']} days on record. "
            "A missing temperature while milk is present counts as not safe.",
            styles["Normal"],
        ),
        Spacer(1, 4 * mm),
        Paragraph(body["summary"], styles["Normal"]),
        Spacer(1, 6 * mm),
        Table(rows),
        Spacer(1, 6 * mm),
        Paragraph(body["fee_note"], styles["Normal"]),
    ]
    document.build(story)
    return buffer.getvalue()


def _slice(readings: list, period: str, zone: ZoneInfo) -> tuple[list, str, int, int]:
    counts: dict[date, int] = defaultdict(int)
    for row in readings:
        counts[row.timestamp.astimezone(zone).date()] += 1
    if not counts:
        return [], "No readings", 0, 0
    full = sorted(day for day, count in counts.items() if count >= 60)
    days = full or sorted(counts)
    latest = days[-1]
    if period == "week":
        start = latest - timedelta(days=latest.weekday())
        end = start + timedelta(days=6)
        span = 7
        title = f"Week · {_pretty(start)} to {_pretty(end)}"
    elif period == "month":
        start = latest.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)
        span = end.day
        title = f"Month · {start.strftime('%B %Y')}"
    else:
        start = end = latest
        span = 1
        title = f"Day · {_pretty(latest)}"
    chosen = [row for row in readings if start <= row.timestamp.astimezone(zone).date() <= end]
    recorded = sum(1 for day in days if start <= day <= end)
    return chosen, title, recorded, span


def _summary(uptime: float | None, failures: int, share: float) -> str:
    if uptime is None:
        return "No milk was recorded in the morning or evening windows. No credit applies. The fee is not set."
    return (
        f"Milking windows were milk-safe {uptime:.1f}% of the time, across {failures} "
        f"failure{'s' if failures != 1 else ''}. That credits {share:.0f}% of the next payment. "
        "The fee is not set, so the amount is blank."
    )


def _window_dict(window: WindowReport) -> dict:
    return {
        "name": window.name,
        "litres": round(window.litres, 1),
        "uptime_pct": _round(window.uptime_pct),
        "missing_samples": window.missing_samples,
        "peak_tank_c": _round(window.peak_tank_c, 1),
        "failures": window.failures,
    }


def _pretty(day: date) -> str:
    return day.strftime("%-d %B %Y")


def _round(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _blank(value) -> str:
    if value is None:
        return "—"
    return str(value)
