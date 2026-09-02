"""Log pipeline: collect device logs -> rule matching -> LLM attribution.

collector: tails ``<log_dir>/*.log`` CSV lines (ts,device_id,level,message)
           and yields new LogEvent-shaped dicts.
rules:     deterministic keyword/level matching -> RuleHit (no LLM, ms-level).
analyzer:  aggregates RuleHits -> LLM attribution (fallback chain) -> Alert rows.
"""

from __future__ import annotations

from pathlib import Path

from medops_common.thresholds import LOG_KEYWORD_RULES


# ----------------------------------------------------------------- collector
def parse_log_line(line: str) -> dict | None:
    """Parse ``ts_iso,device_id,level,message`` -> dict; None on malformed."""
    parts = line.strip().split(",", 3)
    if len(parts) != 4:
        return None
    ts_iso, device_id, level, message = parts
    return {
        "ts": ts_iso,
        "device_id": device_id,
        "level": level.lower(),
        "message": message,
    }


def collect_new_lines(log_file: Path, consumed_marker: Path) -> list[dict]:
    """Read lines appended since the last run (marker file tracks offset)."""
    offset = 0
    if consumed_marker.exists():
        offset = int(consumed_marker.read_text(encoding="utf-8") or "0")
    if not log_file.exists():
        return []
    content = log_file.read_text(encoding="utf-8")
    new_lines = content[offset:].splitlines()
    consumed_marker.write_text(str(len(content)), encoding="utf-8")
    parsed = (parse_log_line(line) for line in new_lines)
    return [d for d in parsed if d is not None]


# --------------------------------------------------------------------- rules
class RuleHit:
    __slots__ = ("device_id", "level", "message", "rule", "ts")

    def __init__(self, device_id: str, level: str, message: str, rule: str, ts: str) -> None:
        self.device_id = device_id
        self.level = level
        self.message = message
        self.rule = rule
        self.ts = ts

    def __repr__(self) -> str:  # pragma: no cover
        return f"RuleHit({self.device_id}, {self.level}, {self.rule})"


def match_event(event: dict) -> RuleHit | None:
    """Deterministic keyword/level matching for one log event."""
    level = event.get("level", "")
    message = event.get("message", "")
    if level in ("error", "critical"):
        hit_level = "critical" if level == "critical" else "error"
        return RuleHit(
            device_id=event["device_id"], level=hit_level,
            message=message, rule="level:error", ts=event["ts"],
        )
    for rule in LOG_KEYWORD_RULES:
        if rule["keyword"].lower() in message.lower():
            hit_level = "warning" if level != "error" else "error"
            return RuleHit(
                device_id=event["device_id"], level=hit_level,
                message=message, rule=f"keyword:{rule['keyword']}", ts=event["ts"],
            )
    if level == "warning":
        return RuleHit(
            device_id=event["device_id"], level="warning",
            message=message, rule="level:warning", ts=event["ts"],
        )
    return None


def match_events(events: list[dict]) -> list[RuleHit]:
    hits = []
    for e in events:
        hit = match_event(e)
        if hit is not None:
            hits.append(hit)
    return hits
