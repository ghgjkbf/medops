"""Tests for medops_engine.drift.DriftModel (TDD)."""

from __future__ import annotations

import pytest
from medops_engine.drift import DriftModel


def _series(model: DriftModel, n: int, dt: float = 1.0) -> list[float]:
    return [model.next(i * dt) for i in range(n)]


class TestDeterminism:
    def test_same_seed_same_sequence(self) -> None:
        a = DriftModel(baseline=37.0, amplitude=0.5, noise_sigma=0.2, seed=42)
        b = DriftModel(baseline=37.0, amplitude=0.5, noise_sigma=0.2, seed=42)
        assert _series(a, 100) == _series(b, 100)

    def test_different_seeds_differ(self) -> None:
        a = DriftModel(baseline=37.0, noise_sigma=0.5, seed=1)
        b = DriftModel(baseline=37.0, noise_sigma=0.5, seed=2)
        assert _series(a, 100) != _series(b, 100)


class TestBaseline:
    def test_mean_near_baseline_without_fault(self) -> None:
        m = DriftModel(baseline=100.0, noise_sigma=0.5, seed=7)
        values = [m.next(t) for t in range(0, 600, 5)]
        mean = sum(values) / len(values)
        assert mean == pytest.approx(100.0, abs=0.5)

    def test_sine_and_trend_components(self) -> None:
        # zero noise: exact deterministic formula
        m = DriftModel(baseline=10.0, amplitude=2.0, period_s=4.0, trend=0.1, noise_sigma=0.0)
        # t=1: sin(pi/2)=1 -> 10 + 2 + 0.1 = 12.1
        assert m.next(1.0) == pytest.approx(12.1)
        # t=3: sin(3pi/2)=-1 -> 10 - 2 + 0.3 = 8.3
        assert m.next(3.0) == pytest.approx(8.3)


class TestFaultInjection:
    def test_step_fault_shifts_mean(self) -> None:
        m = DriftModel(baseline=50.0, noise_sigma=0.1, seed=3)
        before = _series(m, 50)
        offset = 5.0
        m.inject_fault({"kind": "step", "offset": offset})
        after = _series(m, 50)
        shift = (sum(after) / len(after)) - (sum(before) / len(before))
        assert shift > offset * 0.9

    def test_ramp_fault_grows_over_time(self) -> None:
        m = DriftModel(baseline=0.0, noise_sigma=0.01, seed=5)
        _series(m, 10)  # settle
        m.inject_fault({"kind": "ramp", "rate": 1.0})
        values = _series(m, 100)
        early = sum(values[:20]) / 20
        late = sum(values[-20:]) / 20
        assert late - early > 50.0

    def test_clear_fault_restores_baseline(self) -> None:
        m = DriftModel(baseline=80.0, noise_sigma=0.05, seed=9)
        m.inject_fault({"kind": "step", "offset": 10.0})
        _series(m, 10)
        m.clear_fault()
        values = _series(m, 100)
        mean = sum(values) / len(values)
        assert mean == pytest.approx(80.0, abs=0.5)

    def test_reinjection_overrides_previous_fault(self) -> None:
        m = DriftModel(baseline=0.0, noise_sigma=0.0, seed=1)
        m.inject_fault({"kind": "step", "offset": 100.0})
        m.inject_fault({"kind": "step", "offset": 2.0})
        assert m.next(10.0) == pytest.approx(2.0)

    def test_unknown_fault_kind_raises(self) -> None:
        m = DriftModel(baseline=0.0)
        with pytest.raises(ValueError):
            m.inject_fault({"kind": "explode"})
