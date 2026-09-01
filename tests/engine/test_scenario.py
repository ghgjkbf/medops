"""Tests for medops_engine.scenario.ScenarioEngine (TDD)."""

from __future__ import annotations

from pathlib import Path

import pytest
from medops_common.constants import DeviceType
from medops_common.schemas import LogEvent, LogLevel
from medops_engine.drift import DriftModel
from medops_engine.scenario import Scenario, ScenarioEngine, ScenarioEvent
from pydantic import ValidationError

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "devices" / "engine" / "scenarios"
TUBE_OVERHEAT = SCENARIOS_DIR / "tube_overheat.yaml"


def _make_engine(
    models: dict[str, DriftModel] | None = None,
    log_sink=None,
) -> ScenarioEngine:
    return ScenarioEngine.load(
        TUBE_OVERHEAT,
        models=models if models is not None else {"tube_temp": DriftModel(baseline=35.0)},
        log_sink=log_sink,
    )


class TestScenarioModel:
    def test_load_tube_overheat_yaml(self) -> None:
        engine = _make_engine()
        sc = engine.scenario
        assert isinstance(sc, Scenario)
        assert sc.scenario == "tube_overheat"
        assert sc.device == DeviceType.CT
        assert sc.duration_s == 600.0
        assert len(sc.events) >= 3
        actions = [e.action for e in sc.events]
        assert "inject_fault" in actions
        assert "log" in actions

    def test_events_sorted_by_at_s(self) -> None:
        engine = _make_engine()
        at_times = [e.at_s for e in engine.scenario.events]
        assert at_times == sorted(at_times)

    def test_missing_device_raises_validation_error(self) -> None:
        bad_yaml = (
            "scenario: broken\n"
            "duration_s: 60\n"
            "events: []\n"
        )
        with pytest.raises(ValidationError):
            ScenarioEngine.load(bad_yaml, models={})

    def test_missing_events_raises_validation_error(self) -> None:
        bad_yaml = "scenario: broken\ndevice: ct\nduration_s: 60\n"
        with pytest.raises(ValidationError):
            ScenarioEngine.load(bad_yaml, models={})

    def test_event_outside_duration_rejected(self) -> None:
        bad_yaml = (
            "scenario: broken\n"
            "device: ct\n"
            "duration_s: 60\n"
            "events:\n"
            "  - at_s: 120\n"
            "    action: log\n"
            "    params: {message: too late}\n"
        )
        with pytest.raises(ValidationError):
            ScenarioEngine.load(bad_yaml, models={})

    def test_negative_at_s_rejected(self) -> None:
        bad_yaml = (
            "scenario: broken\n"
            "device: ct\n"
            "duration_s: 60\n"
            "events:\n"
            "  - at_s: -1\n"
            "    action: log\n"
            "    params: {message: negative}\n"
        )
        with pytest.raises(ValidationError):
            ScenarioEngine.load(bad_yaml, models={})

    def test_unknown_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScenarioEvent(at_s=1.0, action="explode")  # type: ignore[arg-type]


class TestTick:
    def test_tick_returns_due_inject_fault_event(self) -> None:
        engine = _make_engine()
        inject_at = min(
            e.at_s for e in engine.scenario.events if e.action == "inject_fault"
        )
        # tick just before: nothing due
        due_early = engine.tick(inject_at - 0.5)
        assert all(e.action != "inject_fault" for e in due_early)
        # tick past the injection point
        due = engine.tick(inject_at)
        assert any(e.action == "inject_fault" for e in due)
        inject_evt = next(e for e in due if e.action == "inject_fault")
        assert inject_evt.target == "tube_temp"
        assert inject_evt.params.get("kind") == "ramp"

    def test_tick_interval_exclusive_of_previous(self) -> None:
        engine = _make_engine()
        first_at = engine.scenario.events[0].at_s
        due = engine.tick(first_at)
        assert len(due) >= 1
        # ticking again at same t returns nothing (half-open interval (prev, t])
        assert engine.tick(first_at) == []

    def test_inject_fault_shifts_metric_model(self) -> None:
        model = DriftModel(baseline=35.0, noise_sigma=0.0, seed=1)
        engine = _make_engine(models={"tube_temp": model})
        inject_at = min(
            e.at_s for e in engine.scenario.events if e.action == "inject_fault"
        )
        before = [model.next(t) for t in (inject_at - 2, inject_at - 1)]
        engine.tick(inject_at)
        # ramp anchors at first next() after injection; let it grow
        after = [model.next(t) for t in (inject_at + 100.0, inject_at + 200.0)]
        assert after[-1] > max(before) + 1.0

    def test_clear_fault_event_clears_model(self) -> None:
        model = DriftModel(baseline=35.0, noise_sigma=0.0, seed=1)
        engine = _make_engine(models={"tube_temp": model})
        clear_events = [e for e in engine.scenario.events if e.action == "clear_fault"]
        assert clear_events, "tube_overheat must include an auto-protection clear_fault"
        clear_at = clear_events[0].at_s
        engine.tick(clear_at)
        value = model.next(clear_at + 1.0)
        assert value == pytest.approx(35.0, abs=0.01)

    def test_log_event_produces_log_event_to_sink(self) -> None:
        captured: list[LogEvent] = []
        engine = _make_engine(log_sink=captured.append)
        log_events = [e for e in engine.scenario.events if e.action == "log"]
        assert log_events
        first_log_at = log_events[0].at_s
        due = engine.tick(first_log_at)
        assert any(e.action == "log" for e in due)
        assert len(captured) == 1
        evt = captured[0]
        assert isinstance(evt, LogEvent)
        assert evt.level in (LogLevel.WARNING, LogLevel.ERROR)
        assert evt.message
        assert evt.device_id

    def test_log_event_buffered_without_sink(self) -> None:
        engine = _make_engine()
        last_at = engine.scenario.events[-1].at_s
        engine.tick(last_at)
        logs = [log for log in engine.logs if isinstance(log, LogEvent)]
        assert len(logs) >= 2  # WARNING + ERROR at minimum
        levels = [log.level for log in logs]
        assert LogLevel.WARNING in levels
        assert LogLevel.ERROR in levels

    def test_tick_beyond_duration_returns_nothing_new(self) -> None:
        engine = _make_engine()
        engine.tick(600.0)
        assert engine.tick(900.0) == []


class TestLoadFromText:
    def test_load_accepts_yaml_text(self) -> None:
        text = (
            "scenario: mini\n"
            "device: dr\n"
            "duration_s: 10\n"
            "events:\n"
            "  - at_s: 5\n"
            "    action: log\n"
            "    params: {level: info, message: hello}\n"
        )
        engine = ScenarioEngine.load(text, models={})
        assert engine.scenario.scenario == "mini"
        assert engine.scenario.device == DeviceType.DR
        due = engine.tick(5.0)
        assert len(due) == 1
        assert engine.logs[0].message == "hello"
