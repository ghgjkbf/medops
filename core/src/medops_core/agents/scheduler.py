"""APScheduler wrapper: periodic inspection runs inside the FastAPI loop."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from medops_core.agents.inspector import InspectionResult, InspectorAgent
from medops_core.db import async_session_factory
from medops_core.reminders import scan_and_remind


class InspectionScheduler:
    """Owns the AsyncIOScheduler; idempotent start/stop (uvicorn lifespan safe)."""

    def __init__(
        self,
        inspector: InspectorAgent,
        interval_s: int = 60,
    ) -> None:
        self._inspector = inspector
        self._interval_s = interval_s
        self._scheduler: AsyncIOScheduler | None = None
        self.last_result: InspectionResult | None = None

    @property
    def inspector(self) -> InspectorAgent:
        return self._inspector

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def start(self) -> None:
        if self.running:
            return  # idempotent
        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self._run_once, "interval", seconds=self._interval_s, id="medops-inspection"
        )
        self._scheduler.add_job(
            self._run_reminders, "interval", hours=1, id="medops-reminders"
        )
        self._scheduler.start()

    def stop(self) -> None:
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._scheduler = None

    async def _run_once(self) -> None:
        try:
            self.last_result = await self._inspector.run_inspection()
        except Exception:  # noqa: BLE001 - scheduled job must never crash the app
            self.last_result = None

    async def _run_reminders(self) -> None:
        """Hourly maintenance-due scan; must never crash the app."""
        try:
            await scan_and_remind(async_session_factory)
        except Exception:  # noqa: BLE001 - scheduled job must never crash the app
            pass

    async def run_now(self) -> InspectionResult:
        """Manual trigger (API endpoint); also refreshes last_result."""
        self.last_result = await self._inspector.run_inspection()
        return self.last_result
