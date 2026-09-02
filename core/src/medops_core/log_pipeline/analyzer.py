"""Log pipeline analyzer: RuleHit batches -> LLM attribution -> Alert rows."""

from __future__ import annotations

from medops_common.constants import AlertLevel
from sqlalchemy.orm import Session

from medops_core.agents.llm import FakeLLM, LLMClient, LLMUnavailableError, Message
from medops_core.log_pipeline.rules import RuleHit
from medops_core.models import Alert


def _hit_level_to_alert(level: str) -> str:
    if level in ("error", "critical"):
        return AlertLevel.CRITICAL.value
    return AlertLevel.WARNING.value


def _fallback_summary(hits: list[RuleHit]) -> str:
    parts = [f"[{h.rule}] {h.message}" for h in hits]
    return "[规则降级] " + " | ".join(parts)


async def attribute_and_store(
    hits: list[RuleHit],
    session: Session,
    llm: LLMClient | FakeLLM,
) -> list[Alert]:
    """Group hits by device, get LLM attribution (rule fallback on failure),
    write Alert rows. Returns the created alerts."""
    if not hits:
        return []
    by_device: dict[str, list[RuleHit]] = {}
    for h in hits:
        by_device.setdefault(h.device_id, []).append(h)

    alerts: list[Alert] = []
    for device_id, device_hits in by_device.items():
        top_level = max(
            (h.level for h in device_hits),
            key=lambda lv: {"warning": 0, "error": 1, "critical": 2}.get(lv, 0),
        )
        prompt = (
            f"设备 {device_id} 的日志触发规则告警：\n"
            + "\n".join(f"- {h.message}（规则 {h.rule}）" for h in device_hits)
            + "\n请用不超过两句话给出可能的根因与建议处置。"
        )
        attribution: str
        try:
            result = await llm.chat([Message(role="user", content=prompt)])
            attribution = f"[{result.provider_used}] {result.text}"
        except LLMUnavailableError:
            attribution = _fallback_summary(device_hits)

        alert = Alert(
            device_id=device_id,
            level=_hit_level_to_alert(top_level),
            message=device_hits[0].message,
            attribution=attribution,
        )
        session.add(alert)
        alerts.append(alert)
    session.commit()
    return alerts
