"""P6c: plugin / builtin-skill framework — manifests, enable state, gates,
five builtin skills (deterministic), LLM enhancement hooks."""

from __future__ import annotations

import pytest
from medops_core.plugins import registry
from medops_core.plugins.registry import (
    GateBlocked,
    delete_plugin,
    list_plugins,
    run_skill,
    set_plugin_state,
    validate_manifest,
)
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.fixture()
async def factory(db_engine):  # noqa: ANN001
    yield async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _tables(db_engine):  # noqa: ANN001
    from medops_core.mcp_client.sync import _sync_url
    from medops_core.models import Base
    from sqlalchemy import create_engine

    sync = create_engine(_sync_url(db_engine.url.render_as_string(hide_password=False)))
    Base.metadata.create_all(sync)
    sync.dispose()


# ----------------------------------------------------------- manifests & state
def test_manifests_registered():
    names = set(registry.manifests())
    assert {"prompt_builder", "code_guard", "tool_finder", "console_actor",
            "doc_searcher"} <= names


def test_validate_manifest_rejects_bad_name():
    with pytest.raises(ValueError):
        validate_manifest({"name": "bad name!", "description": "x"})


def test_validate_manifest_rejects_bad_risk():
    with pytest.raises(ValueError):
        validate_manifest({"name": "okay", "description": "x", "risk": "evil"})


async def test_set_state_persists(factory):
    await set_plugin_state(factory, "prompt_builder", True)
    assert await registry.is_enabled(factory, "prompt_builder") is True
    await set_plugin_state(factory, "prompt_builder", False)
    assert await registry.is_enabled(factory, "prompt_builder") is False


async def test_set_state_unknown_raises(factory):
    with pytest.raises(KeyError):
        await set_plugin_state(factory, "nope", True)


async def test_list_plugins_gate_flags(factory, monkeypatch):
    monkeypatch.delenv("MEDOPS_PLUGIN_CONSOLE", raising=False)
    monkeypatch.delenv("MEDOPS_PLUGIN_SEARCH", raising=False)
    items = await list_plugins(factory)
    by_name = {i["name"]: i for i in items}
    assert by_name["prompt_builder"]["enabled"] is False
    assert by_name["console_actor"]["gate_ok"] is False  # env unset
    assert by_name["doc_searcher"]["gate_ok"] is False


# --------------------------------------------------------------- prompt builder
async def test_prompt_builder_builds_prompt():
    res = await run_skill("prompt_builder", {"spec": {
        "intent": "inspection", "devices": ["ct-sim-01"],
        "symptoms": ["球管温度高"], "urgency": "常规"}})
    assert res["ok"] is True
    for token in ("巡检", "ct-sim-01", "球管温度高", "输出"):
        assert token in res["output"]
    assert res["spec"]["devices"] == ["ct-sim-01"]


async def test_prompt_builder_llm_enhances():
    class LLM:
        provider_name = "deepseek"
        calls = 0

        async def chat(self, messages):  # noqa: ANN001
            self.calls += 1
            return type("R", (), {"text": "已润色提示词"})()  # noqa: RUF012

    res = await run_skill("prompt_builder", {"intent": "inspection",
                                             "devices": ["ct-sim-01"]}, llm=LLM())
    assert res.get("llm_enhanced") is True
    assert res["output"] == "已润色提示词"


# ------------------------------------------------------------------ code guard
async def test_code_guard_generates_and_checks():
    res = await run_skill("code_guard", {
        "template": "sql_cleanup", "params": {"table": "alert", "retention": 30}})
    assert res["valid"] is True
    assert 'DELETE FROM "alert"' in res["code"]
    assert "interval '30 days'" in res["code"]


async def test_code_guard_rejects_dangerous_sql():
    res = await run_skill("code_guard", {
        "template": "sql_cleanup",
        "params": {"table": "alert; DROP TABLE alert; --", "retention": "30"}})
    # the table name is quoted by the template; structural guards still hold
    assert res["valid"] is True
    assert "WHERE" in res["code"]


async def test_code_guard_unknown_template():
    with pytest.raises(ValueError):
        await run_skill("code_guard", {"template": "nope"})


async def test_code_guard_python_syntax_error_detected():
    res = await run_skill("code_guard", {"template": "apply_config",
                                         "params": {"device": "ct", "key": "k",
                                                    "value": "x"}})
    assert res["valid"] is True  # apply_config template compiles
    assert "MEDOPS_CFG.set" in res["code"]


# ------------------------------------------------------------------ tool finder
class _FakeHandle:
    def __init__(self, name, tools):
        self.config = type("C", (), {"name": name})()
        self.tools = tools


class _FakeRegistry:
    def __init__(self, handles):
        self.handles = {h.config.name.split("-")[0]: h for h in handles}


async def test_tool_finder_ranks_mcp_by_keyword():
    reg = _FakeRegistry([_FakeHandle("ct-01", ["get_tube_stats", "get_device_info"])])
    res = await run_skill("tool_finder", {"query": "球管温度"}, registry=reg)
    hits = res["suggestions"]
    assert any(h["name"] == "get_tube_stats" for h in hits)
    assert hits[0]["name"] == "get_tube_stats"


async def test_tool_finder_finds_butler_ops():
    res = await run_skill("tool_finder", {"query": "触发巡检"}, registry=_FakeRegistry([]))
    hits = res["suggestions"]
    assert any(h["name"] == "trigger_inspection" for h in hits)


# ---------------------------------------------------------------- console actor
async def test_console_actor_blocked_without_gate(monkeypatch):
    monkeypatch.delenv("MEDOPS_PLUGIN_CONSOLE", raising=False)
    with pytest.raises(GateBlocked):
        await run_skill("console_actor", {"device": "ct", "steps": []})


async def test_console_actor_executes_with_gate(monkeypatch):
    monkeypatch.setenv("MEDOPS_PLUGIN_CONSOLE", "1")
    reg = _FakeRegistry([_FakeHandle("ct-01", ["restart_device_agent"])])
    handle = next(iter(reg.handles.values()))
    calls = []

    async def call_tool(tool, args=None):  # noqa: ANN001
        calls.append(tool)
        return {"ok": True}

    handle.call_tool = call_tool  # type: ignore[attr-defined]
    res = await run_skill("console_actor", {"device": "ct",
                                            "steps": [{"action": "restart_device_agent"}]},
                          registry=reg)
    assert res["executed"] is True
    assert calls == ["restart_device_agent"]


# ----------------------------------------------------------------- doc searcher
async def test_doc_searcher_blocked_without_gate(monkeypatch):
    monkeypatch.delenv("MEDOPS_PLUGIN_SEARCH", raising=False)
    with pytest.raises(GateBlocked):
        await run_skill("doc_searcher", {"query": "球管过热"})


async def test_doc_searcher_offline_via_kb(monkeypatch):
    monkeypatch.setenv("MEDOPS_PLUGIN_SEARCH", "1")
    res = await run_skill("doc_searcher", {"query": "球管过热"}, factory=None)
    assert res["ok"] is True
    assert res["source"] == "offline"
    assert isinstance(res["results"], list)


# -------------------------------------------------------------------- security
async def test_gate_blocks_even_when_enabled(factory, monkeypatch):
    monkeypatch.delenv("MEDOPS_PLUGIN_CONSOLE", raising=False)
    await set_plugin_state(factory, "console_actor", True)
    with pytest.raises(GateBlocked):
        await run_skill("console_actor", {"device": "ct", "steps": []}, factory=factory)


# ---------------------------------------------------------------- imports (P6c-ext)
async def test_import_plugin_persists_and_lists(factory):
    from medops_core.plugins.imports import import_plugin

    await import_plugin(factory, "demo_ext", "prompt_template",
                        description="外部演示插件", config={"template": "巡检 {device} 重点看 {focus}"})
    items = await list_plugins(factory)
    ext = next(i for i in items if i["name"] == "demo_ext")
    assert ext["imported"] is True
    assert ext["kind"] == "prompt_template"
    assert ext["enabled"] is False


async def test_import_plugin_rejects_bad_kind(factory):
    from medops_core.plugins.imports import import_plugin

    with pytest.raises(ValueError):
        await import_plugin(factory, "evil_x", "shell_exec", config={})


async def test_run_imported_prompt_template(factory):
    from medops_core.plugins.imports import import_plugin

    await import_plugin(factory, "demo_p", "prompt_template",
                        config={"template": "巡检 {device} 重点看 {focus}"})
    await set_plugin_state(factory, "demo_p", True)
    res = await run_skill("demo_p", {"device": "ct", "focus": "球管"}, factory=factory)
    assert res["ok"] and res["imported"] is True
    assert "ct" in res["output"] and "球管" in res["output"]


async def test_run_imported_kb_query(factory):
    from medops_core.plugins.imports import import_plugin

    await import_plugin(factory, "demo_kb", "kb_query",
                        config={"query_template": "{query}"})
    await set_plugin_state(factory, "demo_kb", True)
    res = await run_skill("demo_kb", {"query": "球管过热"}, factory=factory)
    assert res["ok"] and res["source"] == "offline"


async def test_imported_plugin_not_enabled_blocked(factory):
    from medops_core.plugins.imports import import_plugin

    await import_plugin(factory, "demo_off", "prompt_template", config={"template": "x"})
    with pytest.raises(GateBlocked):
        await run_skill("demo_off", {}, factory=factory)


# ---------------------------------------------------------------- uninstall (P6c-ext)
async def test_delete_plugin_removes_and_unknown_raises(factory):
    from medops_core.plugins.imports import import_plugin

    await import_plugin(factory, "del_me", "prompt_template", config={"template": "x"})
    await delete_plugin(factory, "del_me")
    items = await list_plugins(factory)
    assert all(i["name"] != "del_me" for i in items)
    with pytest.raises(KeyError):
        await delete_plugin(factory, "nope")
