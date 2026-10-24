@echo off
rem Fabrica de mods/recompilacao de DS — atalho para o CLI (Windows cmd / PowerShell / Explorer).
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [factory] .venv ausente - rode: powershell -ExecutionPolicy Bypass -File "%ROOT%bootstrap.ps1"
  exit /b 3
)
set "PYTHONIOENCODING=utf-8"
"%PY%" "%ROOT%tools\factory_cli.py" %*
exit /b %ERRORLEVEL%
