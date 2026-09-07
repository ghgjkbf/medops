@echo off
REM medops quick stop — backend, then PostgreSQL
setlocal
set "ROOT=%~dp0.."
set "PGBIN=D:\ai-use\tools\pg16\pgsql\bin"
set "PGDATA=%ROOT%\deploy\pgdata"

echo Stopping backend ...
taskkill /FI "WINDOWTITLE eq medops-api*" /T /F >nul 2>&1

echo Stopping PostgreSQL ...
"%PGBIN%\pg_ctl.exe" -D "%PGDATA%" stop -m fast >nul 2>&1

echo medops stopped.
timeout /t 2 /nobreak >nul
