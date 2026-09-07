# Start PostgreSQL with NO visible console window (P4c).
# Uses the cmd /c ""..." wrapper (same quoting as the original start.bat
# line) inside a hidden window — pg_ctl's "-p 55432" arg survives intact.
param(
    [Parameter(Mandatory = $true)][string]$PgBin,
    [Parameter(Mandatory = $true)][string]$PgData,
    [int]$Port = 55432
)
$inner = "`"$PgBin\pg_ctl.exe`" -D `"$PgData`" -l `"$PgData\logfile.log`" -o `"-p $Port`" start"
Start-Process -WindowStyle Hidden cmd -ArgumentList @("/c", "`"$inner`"")
