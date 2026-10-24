<#
.SYNOPSIS
  Prepara a fábrica de mods/recompilação de DS nesta máquina (idempotente; não precisa de admin).

.DESCRIPTION
  1. Garante Python 3.11+ (usa o existente; senão instala o Python 3.12 do usuário via winget).
  2. Cria .venv e instala requirements.txt; num clone novo, recria a memória CoALA a partir do dump
     versionado (`factory memory restore`).
  3. `factory setup`: baixa a toolchain Windows fixada em tools/toolchain.json (dsd, objdiff, ninja,
     mwccarm, BizHawk, melonDS, armips, NCPatcher, Fireflower, xPack arm-none-eabi, Ghidra 11.2.1 +
     dsd-ghidra, JDK 21, DeSmuME, neondst) para toolchain/ (gitignored), conferindo sha256.
  4. (opcional, -Wsl) REA + Ghidra 12.1.4 + JDK 21 dentro do WSL2 e registro do MCP em .mcp.json.
  5. `factory doctor` e (opcional, -SelfTest) o teste ponta-a-ponta com a ROM homebrew própria.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Wsl -SelfTest
#>
param(
    [switch]$Wsl,          # provisionar REA no WSL2
    [switch]$SelfTest,     # rodar `factory selftest` no fim
    [switch]$SkipTools,    # não baixar a toolchain
    [string[]]$Group       # grupos do toolchain.json (core, extra, ghidra); padrão: todos
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Find-Python {
    foreach ($cand in @("py -3.12", "py -3.13", "py -3.11", "python")) {
        $parts = $cand.Split(" ")
        $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $args = @($parts | Select-Object -Skip 1) + @("-c", "import sys; print(sys.version_info >= (3, 11))")
        try { $ok = & $parts[0] @args 2>$null } catch { $ok = $null }
        if ($ok -eq "True") { return ,$parts }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host "[bootstrap] Python 3.11+ não encontrado — instalando Python 3.12 (escopo do usuário) via winget…"
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    $env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts;$env:PATH"
    $py = Find-Python
    if (-not $py) { throw "Python 3.11+ ainda indisponível; instale manualmente e rode de novo." }
}
Write-Host "[bootstrap] Python: $($py -join ' ')"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $pyArgs = @($py | Select-Object -Skip 1) + @("-m", "venv", ".venv")
    & $py[0] @pyArgs
}
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r requirements.txt --quiet
$env:PYTHONIOENCODING = "utf-8"
$cli = Join-Path $root "tools\factory_cli.py"

# memória CoALA: a base SQLite é local (fora do git); num clone novo, recria-a a partir do dump versionado
if (-not (Test-Path ".agents\ds-mods-coala-agent-skill\memory\coala.sqlite")) {
    & $venvPy $cli memory restore
}

if (-not $SkipTools) {
    $setupArgs = @("setup")
    if ($Group) { $setupArgs += @("--group") + $Group }
    & $venvPy $cli @setupArgs
}
if ($Wsl) {
    & $venvPy $cli ghidra wsl-setup
}
& $venvPy $cli doctor --no-wsl
if ($SelfTest) {
    & $venvPy $cli selftest
}
Write-Host "[bootstrap] pronto. Coloque uma ROM em roms\ e rode: .\factory.ps1 new roms\<arquivo>.nds"
