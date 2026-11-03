"""Guarantee figures are recomputed from readings. The fee stays blank."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from shared.config import load_root_config
from shared.schemas import Reading
from cloud.guarantee import build_guarantee, credit_share, render_csv, render_pdf

NAIROBI = ZoneInfo("Africa/Nairobi")


def _sample(hour: int, minute: int, tank: float | None, litres: float, day: int = 1) -> Reading:
    return Reading(
        site_id="demo-site",
        timestamp=datetime(2026, 6, day, hour, minute, tzinfo=NAIROBI),
        tank_temp_c=tank,
        milk_volume_l=litres,
        solar_used_kw=1.0,
        backup_kw=0.0,
    )


def test_windows_litres_missing_temperature_and_a_blank_fee() -> None:
    config = load_root_config()
    readings = [
        _sample(5, 59, 4.0, 0),
        _sample(6, 0, 7.5, 100),
        _sample(6, 1, None, 100),
        _sample(6, 2, 4.0, 100),
        _sample(17, 0, 5.0, 180),
    ]
    report = build_guarantee(readings, config, "Africa/Nairobi", "day")
    assert report.morning.litres == 100
    assert report.morning.failures == 1
    assert report.morning.missing_samples == 1
    assert report.morning.safe_samples == 1
    assert report.morning.milk_samples == 3
    assert report.evening.litres == 80
    assert report.evening.failures == 0
    assert report.evening.uptime_pct == 100
    assert report.fee is None
    assert report.credit_share_pct == 20
    assert "Fee" in report.summary or "fee" in report.summary
    text = render_csv(report)
    assert "Morning delivery" in text
    assert "Not set" not in text or "fee" in text.lower()
    pdf = render_pdf("Demo Collection Centre", report)
    assert pdf.startswith(b"%PDF")


def test_credit_share_follows_the_configured_tiers() -> None:
    sla = load_root_config().sla
    assert credit_share(99, sla) == 0
    assert credit_share(96, sla) == 5
    assert credit_share(91, sla) == 10
    assert credit_share(50, sla) == 20
    assert credit_share(None, sla) == 0


def test_week_counts_recorded_days_and_does_not_invent_the_rest() -> None:
    config = load_root_config()
    readings = []
    for day in (1, 2):
        start = datetime(2026, 6, day, 6, 0, tzinfo=NAIROBI)
        for minute in range(60):
            readings.append(
                Reading(
                    site_id="demo-site",
                    timestamp=start + timedelta(minutes=minute),
                    tank_temp_c=4.0,
                    milk_volume_l=50,
                )
            )
    report = build_guarantee(readings, config, "Africa/Nairobi", "week")
    assert report.recorded_days == 2
    assert report.span_days == 7
    assert report.title.startswith("Week")
