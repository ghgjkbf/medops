"""P2-3: log pipeline tests (rules / collector / analyzer with FakeLLM)."""

from __future__ import annotations

import pytest
from medops_common.constants import AlertLevel
from medops_core.agents.llm import FakeLLM
from medops_core.log_pipeline import (
    RuleHit,
    attribute_and_store,
    collect_new_lines,
    match_event,
    match_events,
    parse_log_line,
)
from medops_core.models import Base
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


# ------------------------------------------------------------------- parser
def test_parse_log_line_valid() -> None:
    d = parse_log_line("2026-09-01T00:00:00+00:00,ct-sim-01,error,tube overheat")
    assert d is not None
    assert d["device_id"] == "ct-sim-01"
    assert d["level"] == "error"
    assert d["message"] == "tube overheat"


def test_parse_log_line_malformed() -> None:
    assert parse_log_line("not a log line") is None


# ----------------------------------------------------------------- collector
def test_collect_new_lines_tracks_offset(tmp_path) -> None:  # noqa: ANN001
    log = tmp_path / "ct.log"
    marker = tmp_path / "ct.log.offset"
    log.write_text(
        "2026-09-01T00:00:01+00:00,ct-sim-01,warning,first\n"
        "2026-09-01T00:00:02+00:00,ct-sim-01,error,second\n",
        encoding="utf-8",
    )
    first = collect_new_lines(log, marker)
    assert len(first) == 2
    # nothing new -> empty
    assert collect_new_lines(log, marker) == []
    # append -> only new lines
    with log.open("a", encoding="utf-8") as fh:
        fh.write("2026-09-01T00:00:03+00:00,ct-sim-01,info,third\n")
    third = collect_new_lines(log, marker)
    assert len(third) == 1 and third[0]["message"] == "third"


def test_collect_missing_file(tmp_path) -> None:  # noqa: ANN001
    assert collect_new_lines(tmp_path / "nope.log", tmp_path / "m") == []


# -------------------------------------------------------------------- rules
def test_match_error_level() -> None:
    hit = match_event({"device_id": "ct", "level": "error", "message": "anything", "ts": "t"})
    assert hit is not None and hit.level == "error"


def test_match_keyword_warning() -> None:
    hit = match_event(
        {"device_id": "ct", "level": "warning", "message": "temperature trending below", "ts": "t"}
    )
    assert hit is not None
    assert hit.rule.startswith("keyword:")  # first matching keyword in priority order


def test_match_plain_info_no_hit() -> None:
    assert match_event({"device_id": "ct", "level": "info", "message": "all normal", "ts": "t"}) is None


def test_match_events_batch() -> None:
    events = [
        {"device_id": "a", "level": "error", "message": "x", "ts": "t"},
        {"device_id": "b", "level": "info", "message": "all normal", "ts": "t"},
    ]
    hits = match_events(events)
    assert len(hits) == 1 and hits[0].device_id == "a"


# ----------------------------------------------------------------- analyzer
@pytest.fixture()
async def db_engine(tmp_path) -> AsyncEngine:  # noqa: ANN001
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'p2.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def test_analyzer_llm_attribution(db_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    hits = [
        RuleHit(device_id="ct-sim-01", level="error", message="tube overheat", rule="level:error", ts="t"),
        RuleHit(device_id="ct-sim-01", level="warning", message="temp trending", rule="keyword:trending", ts="t"),
    ]
    async with factory() as session:
        alerts = await attribute_and_store(hits, session, FakeLLM(text="球管散热风扇故障，建议更换"))
    assert len(alerts) == 1
    assert alerts[0].device_id == "ct-sim-01"
    assert alerts[0].level == AlertLevel.CRITICAL.value
    assert "球管散热风扇故障" in alerts[0].attribution
    assert alerts[0].attribution.startswith("[fake]")


async def test_analyzer_fallback_on_llm_down(db_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    hits = [
        RuleHit(device_id="vent-sim-01", level="warning", message="o2 below threshold", rule="level:warning", ts="t"),
    ]
    fake = FakeLLM(provider_name="fake", fail_providers={"fake"})
    async with factory() as session:
        alerts = await attribute_and_store(hits, session, fake)
    assert len(alerts) == 1
    assert alerts[0].attribution.startswith("[规则降级]")
    assert alerts[0].level == AlertLevel.WARNING.value


async def test_analyzer_groups_by_device(db_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    hits = [
        RuleHit(device_id="ct-1", level="error", message="a", rule="r", ts="t"),
        RuleHit(device_id="ct-2", level="warning", message="b", rule="r", ts="t"),
    ]
    async with factory() as session:
        alerts = await attribute_and_store(hits, session, FakeLLM())
    assert {a.device_id for a in alerts} == {"ct-1", "ct-2"}


async def test_analyzer_empty_hits(db_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        assert await attribute_and_store([], session, FakeLLM()) == []
