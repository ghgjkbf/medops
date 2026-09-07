@echo off
REM medops quick stop — backend, then PostgreSQL
setlocal
set "ROOT=%~dp0.."
REM Portable PostgreSQL bin dir — EDIT for your machine, or set MEDOPS_PGBIN.
if defined MEDOPS_PGBIN set "PGBIN=%MEDOPS_PGBIN%"
if not defined PGBIN set "PGBIN=D:\ai-use\tools\pg16\pgsql\bin"
set "PGDATA=%ROOT%\deploy\pgdata"

echo Stopping backend ...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8123 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo Stopping PostgreSQL ...
"%PGBIN%\pg_ctl.exe" -D "%PGDATA%" stop -m fast >nul 2>&1

echo medops stopped.
timeout /t 2 /nobreak >nul
