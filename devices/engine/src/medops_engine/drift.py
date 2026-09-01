"""Metric drift model: sinusoidal drift + Gaussian noise + trend + fault injection.

Zero-dependency (stdlib only). Deterministic when a seed is provided.
"""

from __future__ import annotations

import math
import random
from typing import Any

_TAU = 2.0 * math.pi


class DriftModel:
    """Generates a drifting metric time series.

    value(t) = baseline + amplitude * sin(2*pi*t / period_s) + trend * t
               + gauss(0, noise_sigma) + active_fault_offset(t)
    """

    def __init__(
        self,
        baseline: float,
        amplitude: float = 0.0,
        period_s: float = 60.0,
        noise_sigma: float = 0.0,
        trend: float = 0.0,
        seed: int | None = None,
    ) -> None:
        self.baseline = float(baseline)
        self.amplitude = float(amplitude)
        self.period_s = float(period_s)
        self.noise_sigma = float(noise_sigma)
        self.trend = float(trend)
        self._rng = random.Random(seed)
        self._fault: dict[str, Any] | None = None

    def next(self, t: float) -> float:
        """Return the metric value at time ``t`` (seconds)."""
        value = self.baseline + self.trend * t
        if self.amplitude:
            value += self.amplitude * math.sin(_TAU * t / self.period_s)
        if self.noise_sigma:
            value += self._rng.gauss(0.0, self.noise_sigma)
        value += self._fault_offset(t)
        return value

    def inject_fault(self, fault_spec: dict[str, Any]) -> None:
        """Activate a fault. Only one fault is active at a time; reinjection overrides.

        Supported kinds:
            {"kind": "step", "offset": x}  -- constant offset
            {"kind": "ramp", "rate": r}    -- linear ramp, r per second from injection time
        """
        kind = fault_spec.get("kind")
        if kind == "step":
            self._fault = {"kind": "step", "offset": float(fault_spec["offset"])}
        elif kind == "ramp":
            self._fault = {
                "kind": "ramp",
                "rate": float(fault_spec["rate"]),
                "t0": float(fault_spec.get("t0", 0.0)),
                "started": False,
            }
        else:
            raise ValueError(f"unknown fault kind: {kind!r}")

    def clear_fault(self) -> None:
        """Remove the active fault, restoring the unfaulted series."""
        self._fault = None

    def _fault_offset(self, t: float) -> float:
        fault = self._fault
        if fault is None:
            return 0.0
        if fault["kind"] == "step":
            return fault["offset"]
        # ramp: anchor t0 on first next() call after injection
        if not fault["started"]:
            fault["t0"] = t
            fault["started"] = True
        return fault["rate"] * max(0.0, t - fault["t0"])
