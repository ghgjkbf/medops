@echo off
REM ============================================================
REM medops quick start - PG + migrations + backend + browser
REM Double-click me, or run from a terminal. Stop: scripts\stop.bat
REM ============================================================
setlocal enabledelayedexpansion
set "ROOT=%~dp0.."
cd /d "%ROOT%"

set "PGBIN=D:\ai-use\tools\pg16\pgsql\bin"
set "PGDATA=%ROOT%\deploy\pgdata"
set "PORT=8123"

echo [1/4] PostgreSQL ...
set "NEEDPG=0"
"%PGBIN%\pg_isready.exe" -h 127.0.0.1 -p 55432 >nul 2>&1 || set "NEEDPG=1"
if "%NEEDPG%"=="1" start "medops-pg" /min cmd /c ""%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -l "%PGDATA%\logfile.log" -o "-p 55432" start"

set /a tries=0
:waitpg
"%PGBIN%\pg_isready.exe" -h 127.0.0.1 -p 55432 >nul 2>&1
if errorlevel 1 (
    set /a tries+=1
    if !tries! lss 30 goto waitpg
    echo ERROR: PostgreSQL not ready in 30s. See %PGDATA%\logfile.log
    pause
    exit /b 1
)
echo       PostgreSQL OK (port 55432)

echo [2/4] Migrations ...
set "PYTHONPATH=%ROOT%\core\src;%ROOT%\common\src"
if not defined MEDOPS_LLM_MODE set "MEDOPS_LLM_MODE=fake"
uv run alembic -c core\alembic.ini upgrade head >nul 2>&1
if errorlevel 1 (
    echo ERROR: alembic upgrade failed.
    pause
    exit /b 1
)
echo       Schema OK

echo [3/4] Web console ...
if not exist "web\dist\index.html" (
    echo       Building web console, first run only ...
    pushd web
    call npm run build
    popd
)
echo       web\dist OK

echo [4/4] Backend ...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }" >nul 2>&1
start "medops-api" /min cmd /c "uv run uvicorn medops_core.app:app --host 127.0.0.1 --port %PORT% > "%ROOT%\deploy\api.log" 2>&1"

set /a tries=0
:waitapi
curl -sf -o nul http://127.0.0.1:%PORT%/api/v1/health 2>nul
if errorlevel 1 (
    set /a tries+=1
    if !tries! lss 20 goto waitapi
    echo ERROR: backend not up in 20s. Check the medops-api window.
    pause
    exit /b 1
)
echo       medops is running at http://127.0.0.1:%PORT%/
if not defined MEDOPS_NO_BROWSER start "" http://127.0.0.1:%PORT%/
echo (stop: scripts\stop.bat, or close the medops-api window)
timeout /t 3 /nobreak >nul
