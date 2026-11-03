"""Enumerations shared by the edge, the cloud, and the simulator."""

from __future__ import annotations

from enum import Enum


class ControlMode(str, Enum):
    NORMAL = "normal"
    SAFE = "safe"
    MANUAL_OVERRIDE = "manual_override"


class ControlActionKind(str, Enum):
    """Primary thing the controller did on this tick. The command holds the full split."""

    COOL_TANK = "cool_tank"
    CHARGE_STORE = "charge_store"
    RUN_AUX = "run_aux"
    BACKUP_COOLING = "backup_cooling"
    BACKUP_AUX = "backup_aux"
    SAFE_MODE_COOL = "safe_mode_cool"
    IDLE = "idle"
    MANUAL = "manual"


class CompressorTarget(str, Enum):
    OFF = "off"
    TANK = "tank"
    STORE = "store"


class EventType(str, Enum):
    BACKUP_STARTED = "backup_started"
    BACKUP_STOPPED = "backup_stopped"
    SENSOR_LOST = "sensor_lost"
    SENSOR_STUCK = "sensor_stuck"
    TEMP_LIMIT_EXCEEDED = "temp_limit_exceeded"
    TEMP_LIMIT_CLEARED = "temp_limit_cleared"
    STORE_EMPTY = "store_empty"
    STORE_RECOVERED = "store_recovered"
    COMMS_LOST = "comms_lost"
    COMMS_RESTORED = "comms_restored"
    MANUAL_OVERRIDE = "manual_override"
    MANUAL_OVERRIDE_EXPIRED = "manual_override_expired"
    SAFE_MODE_ENTERED = "safe_mode_entered"
    SAFE_MODE_CLEARED = "safe_mode_cleared"
    WATCHDOG_RESTART = "watchdog_restart"
    COMPRESSOR_FAULT = "compressor_fault"
    TANK_OVERFLOW = "tank_overflow"
    EDGE_OFFLINE = "edge_offline"
    EDGE_ONLINE = "edge_online"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertChannelKind(str, Enum):
    CONSOLE = "console"
    SMS = "sms"


class AlertStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    ACKED = "acked"
    RESOLVED = "resolved"


class Role(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    VIEWER = "viewer"


class FaultType(str, Enum):
    SENSOR_DROPOUT = "sensor_dropout"
    STUCK_SENSOR = "stuck_sensor"
    COMPRESSOR_FAILURE = "compressor_failure"
    INTERNET_OUTAGE = "internet_outage"
    GRID_OUTAGE = "grid_outage"
    HEAVY_CLOUD = "heavy_cloud"


class RecordType(str, Enum):
    READING = "reading"
    READING_RAW = "reading_raw"
    CONTROL_ACTION = "control_action"
    MILK_BATCH = "milk_batch"
    SLA_PERIOD = "sla_period"
