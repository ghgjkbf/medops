"""Scenario engine: load YAML playbooks and drive DriftModel state over time.

A scenario YAML looks like::

    scenario: tube_overheat
    device: ct
    duration_s: 600
    events:
      - at_s: 60
        action: inject_fault        # inject_fault | clear_fault | log
        target: tube_temp           # metric name (or module name for logs)
        params: {kind: ramp, rate: 0.05}

``ScenarioEngine.tick(t)`` returns the events falling due in the half-open
interval ``(prev_t, t]`` and applies their side effects: fault injection /
clearing on the corresponding :class:`~medops_engine.drift.DriftModel`, and
:class:`~medops_common.schemas.LogEvent` emission for ``log`` events.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import yaml
from medops_common.constants import DeviceType
from medops_common.schemas import LogEvent, LogLevel
from pydantic import BaseModel, Field, ValidationError, model_validator

from medops_engine.drift import DriftModel

LogSink = Callable[[LogEvent], None]


class ScenarioEvent(BaseModel):
    """One scripted action fired at ``at_s`` seconds into the scenario."""

    at_s: float = Field(ge=0.0)
    action: Literal["inject_fault", "clear_fault", "log"]
    target: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class Scenario(BaseModel):
    """A validated scenario definition."""

    scenario: str
    device: DeviceType
    duration_s: float = Field(gt=0.0)
    events: list[ScenarioEvent]

    @model_validator(mode="after")
    def _events_within_duration(self) -> Scenario:
        for event in self.events:
            if event.at_s > self.duration_s:
                raise ValueError(
                    f"event at_s={event.at_s} exceeds duration_s={self.duration_s}"
                )
        return self

    def sorted_events(self) -> list[ScenarioEvent]:
        """Events ordered by firing time (stable for equal timestamps)."""
        return sorted(self.events, key=lambda e: e.at_s)


class ScenarioEngine:
    """Executes a :class:`Scenario` against a set of DriftModels."""

    def __init__(
        self,
        scenario: Scenario,
        models: dict[str, DriftModel],
        log_sink: LogSink | None = None,
    ) -> None:
        self.scenario = scenario
        self._models = models
        self._log_sink = log_sink
        self.logs: list[LogEvent] = []
        self._events = scenario.sorted_events()
        self._prev_t: float = 0.0
        self._cursor: int = 0  # next event index to consider

    @classmethod
    def load(
        cls,
        source: str | Path,
        models: dict[str, DriftModel] | None = None,
        log_sink: LogSink | None = None,
    ) -> ScenarioEngine:
        """Load a scenario from a YAML file path or raw YAML text.

        Raises:
            ValidationError: if the YAML is not a valid scenario definition.
            yaml.YAMLError: if the source is not parseable YAML at all.
        """
        if isinstance(source, Path):
            text = source.read_text(encoding="utf-8")
        else:
            candidate = Path(source)
            text = (
                candidate.read_text(encoding="utf-8")
                if candidate.is_file()
                else source
            )
        data = yaml.safe_load(text)
        scenario = Scenario.model_validate(data)
        return cls(scenario, models=models or {}, log_sink=log_sink)

    def tick(self, t: float) -> list[ScenarioEvent]:
        """Advance to time ``t``; return and execute events in (prev_t, t]."""
        due: list[ScenarioEvent] = []
        while (
            self._cursor < len(self._events)
            and self._events[self._cursor].at_s <= t
        ):
            event = self._events[self._cursor]
            if event.at_s > self._prev_t:
                due.append(event)
                self._execute(event, t)
            self._cursor += 1
        self._prev_t = max(self._prev_t, t)
        return due

    def _execute(self, event: ScenarioEvent, t: float) -> None:
        if event.action == "inject_fault":
            model = self._models.get(event.target)
            if model is not None:
                model.inject_fault(event.params)
        elif event.action == "clear_fault":
            model = self._models.get(event.target)
            if model is not None:
                model.clear_fault()
        elif event.action == "log":
            self._emit_log(event, t)

    def _emit_log(self, event: ScenarioEvent, t: float) -> None:
        level_raw = event.params.get("level", "info")
        log = LogEvent(
            device_id=f"{self.scenario.device.value}-sim-01",
            level=LogLevel(str(level_raw).lower()),
            message=str(event.params.get("message", "")),
            source=event.target or self.scenario.scenario,
        )
        self.logs.append(log)
        if self._log_sink is not None:
            self._log_sink(log)


__all__ = ["Scenario", "ScenarioEngine", "ScenarioEvent", "ValidationError"]
