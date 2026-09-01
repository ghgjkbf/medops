"""Tests for medops_engine.streams (HeartbeatStream/LogStream) and the
o2_cell_drift / disk_full scenarios (TDD)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from medops_common.constants import DeviceType
from medops_common.schemas import Heartbeat, LogEvent, LogLevel
from medops_engine.drift import DriftModel
from medops_engine.scenario import ScenarioEngine
from medops_engine.streams import HeartbeatStream, LogStream, drain_logs

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "devices" / "engine" / "scenarios"
O2_CELL_DRIFT = SCENARIOS_DIR / "o2_cell_drift.yaml"
DISK_FULL = SCENARIOS_DIR / "disk_full.yaml"


class TestHeartbeatStream:
    def test_tick_returns_heartbeat_with_device_id(self) -> None:
        stream = HeartbeatStream(device_id="ct-sim-01")
        hb = stream.tick(1000.0)
        assert isinstance(hb, Heartbeat)
        assert hb.device_id == "ct-sim-01"

    def test_seq_increments_from_seq_start(self) -> None:
        stream = HeartbeatStream(device_id="ct-sim-01", seq_start=5)
        seqs = [stream.tick(float(i)).seq for i in range(3)]
        assert seqs == [5, 6, 7]

    def test_seq_defaults_to_zero(self) -> None:
        stream = HeartbeatStream(device_id="ct-sim-01")
        assert stream.tick(0.0).seq == 0
        assert stream.tick(1.0).seq == 1

    def test_ts_is_utc_datetime_from_t(self) -> None:
        stream = HeartbeatStream(device_id="ct-sim-01")
        hb = stream.tick(0.0)
        assert hb.ts == datetime(1970, 1, 1, tzinfo=UTC)
        hb2 = stream.tick(60.0)
        assert hb2.ts == datetime.fromtimestamp(60.0, UTC)


class TestLogStream:
    def test_emit_returns_log_event_with_fields(self) -> None:
        stream = LogStream(device_id="dr-sim-01")
        event = stream.emit(LogLevel.WARNING, "low disk", t=120.0)
        assert isinstance(event, LogEvent)
        assert event.device_id == "dr-sim-01"
        assert event.level == LogLevel.WARNING
        assert event.message == "low disk"
        assert event.ts == datetime.fromtimestamp(120.0, UTC)

    def test_emit_calls_sink(self) -> None:
        captured: list[LogEvent] = []
        stream = LogStream(device_id="dr-sim-01", sink=captured.append)
        event = stream.emit(LogLevel.ERROR, "boom", t=5.0)
        assert captured == [event]

    def test_format_csv_line(self) -> None:
        stream = LogStream(device_id="dr-sim-01")
        event = stream.emit(LogLevel.INFO, "all good", t=0.0)
        line = stream.format_csv(event)
        ts_iso = datetime.fromtimestamp(0.0, UTC).isoformat()
        assert line == f"{ts_iso},dr-sim-01,info,all good"

    def test_format_csv_neutralizes_commas_in_message(self) -> None:
        stream = LogStream(device_id="dr-sim-01")
        event = stream.emit(LogLevel.ERROR, "failed, retrying, again", t=1.0)
        line = stream.format_csv(event)
        fields = line.split(",")
        # exactly 4 columns: ts, device_id, level, message
        assert len(fields) == 4
        assert fields[2] == "error"
        assert fields[3] == "failed  retrying  again"  # commas -> spaces
        assert "," not in fields[3]


class TestDrainLogs:
    def test_drain_logs_forwards_engine_buffer_to_sink(self) -> None:
        yaml_text = (
            "scenario: mini\n"
            "device: ct\n"
            "duration_s: 10\n"
            "events:\n"
            "  - at_s: 1\n"
            "    action: log\n"
            "    params: {level: warning, message: first}\n"
            "  - at_s: 2\n"
            "    action: log\n"
            "    params: {level: error, message: second}\n"
        )
        engine = ScenarioEngine.load(yaml_text, models={})
        engine.tick(5.0)
        assert len(engine.logs) == 2

        captured: list[LogEvent] = []
        stream = LogStream(device_id="ct-sim-01", sink=captured.append)
        forwarded = drain_logs(engine, stream)
        assert forwarded == 2
        assert [e.message for e in captured] == ["first", "second"]
        # buffer drained
        assert engine.logs == []

    def test_drain_logs_empty_buffer_is_noop(self) -> None:
        yaml_text = (
            "scenario: quiet\n"
            "device: ct\n"
            "duration_s: 10\n"
            "events: []\n"
        )
        engine = ScenarioEngine.load(yaml_text, models={})
        captured: list[LogEvent] = []
        stream = LogStream(device_id="ct-sim-01", sink=captured.append)
        assert drain_logs(engine, stream) == 0
        assert captured == []


class TestO2CellDriftScenario:
    def test_loads_and_validates(self) -> None:
        engine = ScenarioEngine.load(O2_CELL_DRIFT, models={})
        sc = engine.scenario
        assert sc.scenario == "o2_cell_drift"
        assert sc.device == DeviceType.VENTILATOR
        assert sc.duration_s == 600.0

    def test_event_order_ramp_then_warning_then_error(self) -> None:
        model = DriftModel(baseline=93.0, noise_sigma=0.0, seed=1)
        engine = ScenarioEngine.load(O2_CELL_DRIFT, models={"o2_concentration": model})
        sorted_events = engine.scenario.sorted_events()

        def _log_at(level: str) -> float:
            return next(
                e.at_s for e in sorted_events
                if e.action == "log" and e.params.get("level") == level
            )

        inject_at = next(e.at_s for e in sorted_events if e.action == "inject_fault")
        assert inject_at < _log_at("warning") < _log_at("error")

        inject_evt = next(e for e in sorted_events if e.action == "inject_fault")
        assert inject_evt.target == "o2_concentration"
        assert inject_evt.params.get("kind") == "ramp"
        assert inject_evt.params.get("rate", 0) < 0  # oxygen decays

        # run the scenario: value must fall while the fault is active,
        # warning/error logs get buffered, and clear_fault restores baseline
        engine.tick(300.0)
        model.next(300.0)  # anchors the ramp
        assert model.next(400.0) < 93.0
        engine.tick(600.0)
        assert model.next(599.0) == 93.0  # O2 cell replaced
        levels = [log.level for log in engine.logs]
        assert LogLevel.WARNING in levels
        assert LogLevel.ERROR in levels


class TestDiskFullScenario:
    def test_loads_and_validates(self) -> None:
        engine = ScenarioEngine.load(DISK_FULL, models={})
        sc = engine.scenario
        assert sc.scenario == "disk_full"
        assert sc.device == DeviceType.DR
        assert sc.duration_s == 600.0

    def test_ramp_down_error_log_then_clear_fault(self) -> None:
        model = DriftModel(baseline=50.0, noise_sigma=0.0, seed=1)
        engine = ScenarioEngine.load(DISK_FULL, models={"disk_free_gb": model})
        sorted_events = engine.scenario.sorted_events()

        inject_evt = next(e for e in sorted_events if e.action == "inject_fault")
        assert inject_evt.target == "disk_free_gb"
        assert inject_evt.params.get("kind") == "ramp"
        assert inject_evt.params.get("rate", 0) < 0

        error_logs = [
            e for e in sorted_events
            if e.action == "log" and e.params.get("level") == "error"
        ]
        assert error_logs, "disk_full must log an image-save ERROR"
        messages = [str(e.params.get("message", "")).lower() for e in error_logs]
        assert any("save" in m or "影像" in m for m in messages)

        clear_events = [e for e in sorted_events if e.action == "clear_fault"]
        assert clear_events, "disk_full must end with clear_fault (admin cleanup)"
        assert clear_events[-1].at_s > error_logs[-1].at_s

        # full run: fault cleared by the end
        engine.tick(600.0)
        assert model.next(599.0) == 50.0  # cleared back to baseline
