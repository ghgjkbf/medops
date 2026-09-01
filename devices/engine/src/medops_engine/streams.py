"""Heartbeat and log streams for simulated devices.

``HeartbeatStream`` emits periodic :class:`~medops_common.schemas.Heartbeat`
samples with a monotonically increasing sequence number.
``LogStream`` emits :class:`~medops_common.schemas.LogEvent` objects and can
format them as single-line CSV rows. ``drain_logs`` forwards the log buffer
accumulated by a :class:`~medops_engine.scenario.ScenarioEngine` into a
``LogStream`` (and its optional sink).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from medops_common.schemas import Heartbeat, LogEvent, LogLevel

if TYPE_CHECKING:
    from medops_engine.scenario import ScenarioEngine

LogSink = Callable[[LogEvent], None]


def _t_to_utc(t: float) -> datetime:
    """Convert scenario seconds (epoch offset) to a UTC datetime."""
    return datetime.fromtimestamp(t, UTC)


class HeartbeatStream:
    """Emits heartbeats for one device; ``seq`` increments on every tick."""

    def __init__(self, device_id: str, seq_start: int = 0) -> None:
        self.device_id = device_id
        self._next_seq = seq_start

    def tick(self, t: float) -> Heartbeat:
        """Produce the heartbeat due at scenario time ``t`` (epoch seconds)."""
        hb = Heartbeat(
            device_id=self.device_id,
            seq=self._next_seq,
            ts=_t_to_utc(t),
        )
        self._next_seq += 1
        return hb


class LogStream:
    """Emits log events for one device and formats them as CSV rows."""

    def __init__(self, device_id: str, sink: LogSink | None = None) -> None:
        self.device_id = device_id
        self._sink = sink

    def emit(self, level: LogLevel, message: str, t: float) -> LogEvent:
        """Create a LogEvent at scenario time ``t`` and push it to the sink."""
        event = LogEvent(
            device_id=self.device_id,
            level=level,
            message=message,
            ts=_t_to_utc(t),
        )
        if self._sink is not None:
            self._sink(event)
        return event

    def format_csv(self, event: LogEvent) -> str:
        """Format as ``ts_iso,device_id,level,message`` (commas in the
        message are replaced with spaces to keep exactly 4 columns)."""
        message = event.message.replace(",", " ")
        return f"{event.ts.isoformat()},{event.device_id},{event.level.value},{message}"


def drain_logs(engine: ScenarioEngine, log_stream: LogStream) -> int:
    """Forward the engine's buffered logs through ``log_stream``'s sink.

    The events are re-emitted with the stream's device identity removed —
    they keep their original ``device_id``/``level``/``message``/``ts`` —
    then the engine buffer is cleared. Returns the number forwarded.
    """
    count = 0
    while engine.logs:
        event = engine.logs.pop(0)
        if log_stream._sink is not None:  # noqa: SLF001 - intentional coupling
            log_stream._sink(event)  # noqa: SLF001
        count += 1
    return count


__all__ = ["HeartbeatStream", "LogStream", "drain_logs"]
