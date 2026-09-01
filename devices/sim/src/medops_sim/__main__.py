"""medops-sim CLI: run a simulated medical device from the command line.

Usage:
    python -m medops_sim <device_type> [--scenario NAME|PATH] [--outbox DIR]
                         [--metrics-port PORT] [--list-scenarios]

Assembles the engine (DriftModels + ScenarioEngine + streams), prints
metrics / heartbeats / logs to stdout, writes DICOM studies for CT devices,
and exits cleanly on scenario completion or Ctrl-C.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from medops_engine.dicom_writer import write_study
from medops_engine.drift import DriftModel
from medops_engine.scenario import Scenario, ScenarioEngine
from medops_engine.streams import HeartbeatStream, LogStream, drain_logs

_SEED = 42
_METRIC_EVERY_S = 5
_TICK_S = 1.0
_DICOM_STUDY_FILES = 3

# Built-in per-device metric configuration:
# device_id -> {metric_name: (baseline, amplitude)}
DEVICE_CONFIGS: dict[str, dict[str, tuple[float, float]]] = {
    "ct": {
        "tube_temp": (35.0, 2.0),
        "tube_exposure_count": (0.0, 0.0),
    },
    "ventilator": {
        "o2_concentration": (93.0, 0.5),
        "tidal_volume": (500.0, 10.0),
        "airway_pressure": (15.0, 2.0),
    },
    "dr": {
        "detector_temp": (28.0, 1.0),
        "disk_free_gb": (500.0, 0.0),
    },
    "ecg": {
        "waveform_snr": (30.0, 1.0),
        "heart_rate": (72.0, 5.0),
    },
}


def _repo_root() -> Path:
    # devices/sim/src/medops_sim/__main__.py -> repo root is parents[4]
    return Path(__file__).resolve().parents[4]


def _scenarios_dir() -> Path:
    return _repo_root() / "devices" / "engine" / "scenarios"


def _list_scenarios() -> int:
    found: list[tuple[str, Scenario]] = []
    for path in sorted(_scenarios_dir().glob("*.yaml")):
        engine = ScenarioEngine.load(path)
        found.append((path.name, engine.scenario))
    if not found:
        print("no built-in scenarios found")
        return 1
    print("built-in scenarios:")
    for filename, scenario in found:
        print(
            f"  {scenario.scenario} ({filename})"
            f"  device={scenario.device.value}  duration={scenario.duration_s:.0f}s"
        )
    return 0


def _resolve_scenario_path(name: str) -> Path:
    candidate = Path(name)
    if candidate.is_file():
        return candidate
    builtin = _scenarios_dir() / (name if name.endswith(".yaml") else f"{name}.yaml")
    if builtin.is_file():
        return builtin
    raise FileNotFoundError(
        f"scenario {name!r} not found (tried {candidate} and {builtin})"
    )


def _build_models(device: str) -> dict[str, DriftModel]:
    return {
        name: DriftModel(baseline, amplitude, noise_sigma=amplitude * 0.1, seed=_SEED)
        for name, (baseline, amplitude) in DEVICE_CONFIGS[device].items()
    }


def _print_log_line(line: str) -> None:
    print(f"[LOG] {line}", flush=True)


def run_sim(args: argparse.Namespace) -> int:
    device: str = args.device_type
    device_id = f"{device}-sim-01"
    models = _build_models(device)
    heartbeat = HeartbeatStream(device_id)
    log_stream = LogStream(device_id, sink=lambda e: _print_log_line(log_stream.format_csv(e)))

    engine: ScenarioEngine | None = None
    if args.scenario:
        engine = ScenarioEngine.load(_resolve_scenario_path(args.scenario), models=models)

    if device == "ct":
        paths = write_study(args.outbox, n_files=_DICOM_STUDY_FILES, patient_prefix="SIM")
        print(f"[DICOM] wrote {len(paths)} files to {args.outbox}", flush=True)

    if args.metrics_port is not None:
        print(f"[INFO] metrics HTTP exposure on port {args.metrics_port} is planned for P1 "
              "(argument accepted, no server started)", flush=True)

    print(f"[INFO] starting {device_id}"
          + (f" scenario={engine.scenario.scenario}" if engine else " (no scenario)"),
          flush=True)

    start = time.monotonic()
    t = 0.0
    ticks = 0
    metrics_emitted = 0
    try:
        while True:
            hb = heartbeat.tick(t)
            print(f"[HB] {hb.device_id} seq={hb.seq} ts={hb.ts.isoformat()}", flush=True)

            if engine is not None:
                engine.tick(t)
                drained = drain_logs(engine, log_stream)
                if drained == 0 and t >= engine.scenario.duration_s:
                    print(f"[INFO] scenario {engine.scenario.scenario} finished "
                          f"at t={t:.0f}s", flush=True)
                    break

            if ticks % _METRIC_EVERY_S == 0:
                for name, model in models.items():
                    value = model.next(t)
                    metrics_emitted += 1
                    print(f"[METRIC] {name} {value:.3f}", flush=True)

            ticks += 1
            t = time.monotonic() - start
            time.sleep(max(0.0, start + ticks * _TICK_S - time.monotonic()))
    except KeyboardInterrupt:
        print("\n[INFO] interrupted by user", flush=True)

    elapsed = time.monotonic() - start
    print(f"[SUMMARY] device={device_id} ticks={ticks} metrics_emitted={metrics_emitted} "
          f"elapsed={elapsed:.1f}s", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="medops_sim", description=__doc__)
    parser.add_argument("device_type", choices=sorted(DEVICE_CONFIGS))
    parser.add_argument("--scenario", help="scenario name (built-in) or path to a YAML file")
    parser.add_argument("--outbox", default="outbox", help="DICOM output directory (ct only)")
    parser.add_argument("--metrics-port", type=int, default=None,
                        help="reserved for P1 metrics HTTP exposure")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="list built-in scenarios and exit")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        return _list_scenarios()
    try:
        return run_sim(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
