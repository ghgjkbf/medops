# Start the medops backend with NO visible console window (P4c).
# The cmd wrapper (and uvicorn inside it) runs detached & hidden;
# logs go to deploy\api.log. Port-kill still works (start.bat/stop.bat
# kill by TCP port, not by window).
param(
    [int]$Port = 8123,
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)
Start-Process -WindowStyle Hidden cmd -ArgumentList @(
    "/c",
    "uv run uvicorn medops_core.app:app --host 127.0.0.1 --port $Port > `"$Root\deploy\api.log`" 2>&1"
)
