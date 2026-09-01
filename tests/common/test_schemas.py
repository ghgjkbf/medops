"""Tests for medops_common constants and schemas (Pydantic v2)."""

from datetime import UTC, datetime

import pytest
from medops_common.constants import (
    ActionRisk,
    AlertLevel,
    DeviceType,
    WorkOrderStatus,
)
from medops_common.schemas import (
    DeviceStatus,
    Heartbeat,
    LogEvent,
    LogLevel,
    MetricPoint,
)
from pydantic import ValidationError

# --- constants ---


def test_device_type_values():
    assert DeviceType.CT == "ct"
    assert DeviceType.DR == "dr"
    assert DeviceType.VENTILATOR == "ventilator"
    assert DeviceType.ECG == "ecg"


def test_alert_level_values():
    assert AlertLevel.INFO == "info"
    assert AlertLevel.WARNING == "warning"
    assert AlertLevel.CRITICAL == "critical"


def test_work_order_status_values():
    assert WorkOrderStatus.PENDING == "pending"
    assert WorkOrderStatus.IN_PROGRESS == "in_progress"
    assert WorkOrderStatus.AWAITING_VERIFICATION == "awaiting_verification"
    assert WorkOrderStatus.CLOSED == "closed"


def test_action_risk_values():
    assert ActionRisk.READ_ONLY == "read_only"
    assert ActionRisk.LOW_RISK_WRITE == "low_risk_write"
    assert ActionRisk.HIGH_RISK_WRITE == "high_risk_write"


# --- MetricPoint ---


def test_metric_point_roundtrip():
    mp = MetricPoint(device_id="ct-01", metric_name="tube_temp", value=62.5)
    assert mp.ts.tzinfo is not None
    assert mp.ts.utcoffset() == UTC.utcoffset(None)
    assert mp.tags == {}
    dumped = mp.model_dump()
    restored = MetricPoint.model_validate(dumped)
    assert restored == mp
    # JSON roundtrip
    restored_json = MetricPoint.model_validate_json(mp.model_dump_json())
    assert restored_json == mp


def test_metric_point_with_tags_and_explicit_ts():
    ts = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)
    mp = MetricPoint(
        device_id="dr-02",
        metric_name="exposure_count",
        value=3.0,
        ts=ts,
        tags={"room": "radiology-1"},
    )
    assert mp.ts == ts
    assert mp.tags == {"room": "radiology-1"}


def test_metric_point_rejects_non_numeric_value():
    with pytest.raises(ValidationError):
        MetricPoint(device_id="ct-01", metric_name="tube_temp", value="not-a-float")


def test_metric_point_rejects_missing_required_fields():
    with pytest.raises(ValidationError):
        MetricPoint(metric_name="tube_temp", value=1.0)  # missing device_id


# --- LogEvent ---


def test_log_event_roundtrip():
    ev = LogEvent(device_id="ecg-01", level=LogLevel.ERROR, message="lead off")
    assert ev.ts.tzinfo is not None
    assert ev.source == ""
    assert LogEvent.model_validate_json(ev.model_dump_json()) == ev


def test_log_event_rejects_invalid_level():
    with pytest.raises(ValidationError):
        LogEvent(device_id="ecg-01", level="FATAL-ish", message="boom")


# --- Heartbeat ---


def test_heartbeat_roundtrip():
    hb = Heartbeat(device_id="vent-01", seq=42)
    assert hb.ts.tzinfo is not None
    assert Heartbeat.model_validate_json(hb.model_dump_json()) == hb


def test_heartbeat_rejects_non_int_seq():
    with pytest.raises(ValidationError):
        Heartbeat(device_id="vent-01", seq="forty-two")


# --- DeviceStatus ---


def test_device_status_roundtrip():
    ds = DeviceStatus(device_id="ct-01", device_type=DeviceType.CT, online=True)
    assert ds.scenario is None
    assert ds.last_heartbeat is None
    assert DeviceStatus.model_validate_json(ds.model_dump_json()) == ds


def test_device_status_full_fields():
    ts = datetime(2026, 8, 31, 8, 0, 0, tzinfo=UTC)
    ds = DeviceStatus(
        device_id="vent-03",
        device_type="ventilator",
        online=False,
        scenario="alarm_storm",
        last_heartbeat=ts,
    )
    assert ds.device_type is DeviceType.VENTILATOR
    restored = DeviceStatus.model_validate_json(ds.model_dump_json())
    assert restored == ds


def test_device_status_rejects_invalid_device_type():
    with pytest.raises(ValidationError):
        DeviceStatus(device_id="x-01", device_type="mri", online=True)
