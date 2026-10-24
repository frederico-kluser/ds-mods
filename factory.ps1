# Fábrica de mods/recompilação de DS — atalho para o CLI no PowerShell:  .\factory.ps1 <comando> ...
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "[factory] .venv ausente — rode .\bootstrap.ps1"
    exit 3
}
$env:PYTHONIOENCODING = "utf-8"
& $py (Join-Path $root "tools\factory_cli.py") @args
exit $LASTEXITCODE
