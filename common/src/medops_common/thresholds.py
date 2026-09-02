"""Shared metric/log thresholds for ct / dr / ventilator / ecg (P2-4).

Single source of truth: the device MCP servers AND the inspector/log
pipeline both import from here, so a threshold change never drifts
between the detection layer and the alerting layer.
"""

from __future__ import annotations

THRESHOLD_RULES: dict[str, dict[str, float]] = {
    # ct
    "tube_temp": {"warn_high": 40.0, "error_high": 45.0},
    # dr
    "detector_temp": {"warn_high": 40.0, "error_high": 45.0},
    "generator_kvp": {"warn_high": 125.0, "error_high": 130.0},
    "disk_free_gb": {"warn_low": 50.0, "error_low": 20.0},
    # ventilator
    "o2_concentration": {"warn_low": 90.0, "error_low": 85.0},
    "tidal_volume": {"warn_low": 350.0, "error_low": 250.0},
    "airway_pressure": {"warn_low": 10.0, "error_low": 8.0},
    # ecg
    "waveform_snr": {"warn_low": 20.0, "error_low": 12.0},
    "battery_voltage": {"warn_low": 11.5, "error_low": 11.0},
}

# Highest-severity keyword mapping for rule-engine log matching.
LOG_KEYWORD_RULES: list[dict[str, str]] = [
    {"keyword": "LEAD OFF", "level": "error"},
    {"keyword": "overheat", "level": "error"},
    {"keyword": "thermal shutdown", "level": "error"},
    {"keyword": "circuit leak", "level": "error"},
    {"keyword": "critically low", "level": "error"},
    {"keyword": "connection lost", "level": "error"},
    {"keyword": "below", "level": "warning"},
    {"keyword": "trending", "level": "warning"},
    {"keyword": "check", "level": "warning"},
]
