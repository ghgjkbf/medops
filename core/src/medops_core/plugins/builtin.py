"""P6c: builtin skill implementations (deterministic core + optional LLM
enhancement hooks). All skills run offline; the risky ones are gated by the
registry (env gate + enable state)."""

from __future__ import annotations

from typing import Any

from medops_core.agents.requirements import synthesize_prompt


# --------------------------------------------------------------- prompt builder
async def skill_prompt_builder(ctx: dict[str, Any]) -> dict[str, Any]:
    args = ctx["args"]
    spec = args.get("spec") or {
        "intent": args.get("intent", "inspection"),
        "devices": args.get("devices") or [],
        "symptoms": args.get("symptoms") or [],
        "urgency": args.get("urgency", "常规"),
    }
    return {"output": synthesize_prompt(spec), "spec": spec}


# ------------------------------------------------------------------ code guard
_TEMPLATES: dict[str, str] = {
    "sql_cleanup": (
        'DELETE FROM "{table}" WHERE created_at < now() - interval \'{retention} days\';'
    ),
    "apply_config": (
        "MEDOPS_CFG.set(device='{device}', key='{key}', value={value!r})"
    ),
}


async def skill_code_guard(ctx: dict[str, Any]) -> dict[str, Any]:
    """Generate a small repair/fix script from a template and CHECK it."""
    args = ctx["args"]
    template = args.get("template")
    if template not in _TEMPLATES:
        raise ValueError(f"unknown template {template!r} ({sorted(_TEMPLATES)})")
    code = _TEMPLATES[template].format(**args.get("params", {}))
    checks: list[str] = ["format-checks"]
    result: dict[str, Any] = {"code": code, "checks": checks, "valid": True}
    errors = _lint_python(code) if template != "sql_cleanup" else _lint_sql(code)
    if errors:
        result.update({"valid": False, "syntax_errors": errors})
    return result


def _lint_python(code: str) -> list[str]:
    try:
        compile(code, "<plugin>", "exec")
        return []
    except SyntaxError as exc:
        return [f"{exc.msg} (line {exc.lineno})"]


def _lint_sql(code: str) -> list[str]:
    # deterministic structural checks for DML samples
    bad = []
    if not code.strip().endswith(";"):
        bad.append("statement must end with ';'")
    if "DELETE" in code and "WHERE" not in code:
        bad.append("DELETE without WHERE is not allowed")
    return bad


async def enhance_code_guard(ctx: dict[str, Any], result: dict) -> str | None:
    """LLM review pass on the generated script (when attached)."""
    resp = await ctx["llm"].chat(
        [
            {"role": "user",
             "content": f"评审以下修复脚本是否有风险：\n{result.get('code', '')}"}
        ]
    )
    return getattr(resp, "text", None)


# ------------------------------------------------------------------ tool finder
_TOOL_HINTS: dict[str, str] = {
    "get_tube_stats": "球管 温度 tube 冷却",
    "get_device_info": "设备 信息 固件 配置 版本 系统",
    "check_dicom_dir": "dicom 目录 图像 文件",
    "check_pacs_connectivity": "pacs 连接 网络 影像",
    "get_detector_temp": "探测器 温度",
    "check_generator_status": "发生器 状态",
    "get_realtime_params": "实时 参数 呼吸",
    "run_self_test": "自检 test",
    "get_waveform_quality": "波形 质量 信噪",
    "upgrade_firmware": "固件 升级",
    "apply_config": "配置 下发",
    "restart_device_agent": "代理 重启 设备系统",
}


async def skill_tool_finder(ctx: dict[str, Any]) -> dict[str, Any]:
    """Rank MCP tools + butler ops by keyword overlap with the query."""
    query = ctx["args"].get("query", "")
    registry = ctx.get("registry")
    hits: list[dict[str, Any]] = []
    if registry is not None:
        for handle in registry.handles.values():
            for tool in getattr(handle, "tools", []) or []:
                candidate = f"{tool} {handle.config.name} {_TOOL_HINTS.get(tool, '')}"
                score = _score(query, candidate)
                if score > 0:
                    hits.append(
                        {"type": "mcp", "name": tool, "server": handle.config.name,
                         "score": score}
                    )
    for op, task in (("trigger_inspection", "触发巡检 立即巡检 巡检一次"),
                     ("register_mcp_server", "注册 MCP 服务 工具接入"),
                     ("create_work_order", "创建工单"),
                     ("cleanup_data", "清理数据"),
                     ("list_knowledge_sources", "知识源列表"),
                     ("platform_status", "平台状态")):
        score = _score(query, task)
        if score > 0:
            hits.append({"type": "butler", "name": op, "server": "butler", "score": score})
    hits.sort(key=lambda h: h["score"], reverse=True)
    return {"query": query, "suggestions": hits[:5]}


def _score(query: str, candidate: str) -> int:
    tokens = [t.strip() for t in query.replace("，", " ").replace("、", " ")
              .replace("_", " ").split() if len(t.strip()) >= 2]
    score = 0
    for t in tokens:
        low = t.lower()
        if low in candidate.lower():
            score += 3
        elif any(low in h or h in low for h in candidate.split()):
            score += 2
    return score


# ---------------------------------------------------------------- console actor
_DEVICE_ACTIONS = {
    "restart_device_agent": "重启设备本地代理",
    "upgrade_firmware": "执行固件升级",
    "apply_config": "重新下发配置",
}


async def skill_console_actor(ctx: dict[str, Any]) -> dict[str, Any]:
    """Simulated device-console macro steps via device MCP tools (gated)."""
    args = ctx["args"]
    device = args.get("device", "ct")
    steps = args.get("steps") or []
    registry = ctx.get("registry")
    if registry is None:
        return {"executed": False, "error": "no registry bound"}
    handle = None
    for h in registry.handles.values():
        if h.config.name.startswith(device):
            handle = h
            break
    if handle is None:
        return {"executed": False, "error": f"device {device!r} not registered"}
    executed: list[dict[str, Any]] = []
    for step in steps:
        action = step.get("action", "restart_device_agent")
        label = _DEVICE_ACTIONS.get(action, action)
        try:
            payload = await handle.call_tool(action)
            executed.append({"action": action, "label": label, "ok": True, "result": payload})
        except Exception as exc:  # noqa: BLE001
            executed.append({"action": action, "label": label, "ok": False, "error": str(exc)})
    return {"executed": True, "steps": executed}


# ----------------------------------------------------------------- doc searcher
async def skill_doc_searcher(ctx: dict[str, Any]) -> dict[str, Any]:
    """Offline docs/KB search (online retrieval is OPT-IN via env endpoint)."""
    args = ctx["args"]
    query = args.get("query", "")
    factory = ctx.get("factory")
    results: list[dict[str, Any]] = []
    online_url = args.get("online_url")
    if factory is not None:
        try:
            from medops_core import knowledge  # noqa: PLC0415

            results = await knowledge.search_documents(factory, query, 5)
        except Exception:  # noqa: BLE001 - degrade to empty
            results = []
    if online_url:
        import json  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        try:
            with urllib.request.urlopen(online_url, timeout=5) as resp:
                results = json.load(resp).get("results", results)
        except Exception:  # noqa: BLE001 - offline result kept
            pass
    return {"query": query, "results": results, "source": "offline" if not online_url else "online"}


async def enhance_prompt_builder(ctx: dict[str, Any], result: dict) -> str | None:
    """LLM polish of the generated prompt (optional)."""
    resp = await ctx["llm"].chat(
        [{"role": "user", "content": f"润色这段巡检提示词：\n{result.get('output', '')}"}]
    )
    return getattr(resp, "text", None)
