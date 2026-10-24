"""Well-known locations of the factory and resolution of toolchain executables."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
TOOLCHAIN = ROOT / "toolchain"
GAMES = ROOT / "games"
ROMS = ROOT / "roms"
BIOS = ROMS / "bios"
LUA = TOOLS / "lua"
MANIFEST = TOOLS / "toolchain.json"
LOCKFILE = TOOLS / "toolchain.lock.json"
INSTALLED = TOOLCHAIN / ".installed"
DOWNLOADS = TOOLCHAIN / ".downloads"
RUNTIME = TOOLCHAIN / "runtime"          # generated configs (BizHawk ini, melonDS toml, ...)

IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""


class ToolMissing(RuntimeError):
    """Raised when a toolchain executable is not installed."""


def _need(path: Path, tool_id: str) -> Path:
    if not path.exists():
        raise ToolMissing(f"{path} não encontrado — rode `factory setup --only {tool_id}`")
    return path


def dsd() -> Path:
    return _need(TOOLCHAIN / "bin" / f"dsd{EXE}", "dsd")


def objdiff_cli() -> Path:
    return _need(TOOLCHAIN / "bin" / f"objdiff-cli{EXE}", "objdiff-cli")


def sjiswrap() -> Path | None:
    p = TOOLCHAIN / "bin" / f"sjiswrap{EXE}"
    return p if p.exists() else None


def ninja() -> Path:
    return _need(TOOLCHAIN / "bin" / f"ninja{EXE}", "ninja")


def neondst() -> Path:
    return _need(TOOLCHAIN / "bin" / f"neondst{EXE}", "neondst")


def mwcc_root() -> Path:
    return _need(TOOLCHAIN / "mwccarm", "mwccarm")


def mwcc_versions() -> list[str]:
    """Every '<series>/<patch>' directory that contains mwccarm.exe (e.g. '2.0/sp2p4')."""
    root = mwcc_root()
    out = []
    for exe in sorted(root.rglob("mwccarm.exe")):
        out.append(exe.parent.relative_to(root).as_posix())
    return out


def mwcc_dir(version: str) -> Path:
    d = mwcc_root() / version
    if not (d / "mwccarm.exe").exists():
        raise ToolMissing(f"compilador mwccarm '{version}' não existe em {mwcc_root()} "
                          f"(disponíveis: {', '.join(mwcc_versions())})")
    return d


def mwcc_license() -> Path:
    """license.dat used through LM_LICENSE_FILE (CodeWarrior FlexLM check)."""
    root = mwcc_root()
    for cand in (root / "license.dat", *root.rglob("license.dat")):
        if cand.exists():
            return cand
    return root / "license.dat"


def emuhawk() -> Path:
    return _need(TOOLCHAIN / "bizhawk" / "EmuHawk.exe", "bizhawk")


def melonds() -> Path:
    return _need(TOOLCHAIN / "melonds" / "melonDS.exe", "melonds")


def desmume() -> Path:
    d = TOOLCHAIN / "desmume"
    for cand in sorted(d.glob("DeSmuME*.exe")) if d.exists() else []:
        return cand
    raise ToolMissing("DeSmuME não instalado — rode `factory setup --only desmume`")


def armips() -> Path:
    d = TOOLCHAIN / "armips"
    for cand in (d / "armips.exe", *d.rglob("armips.exe")):
        if cand.exists():
            return cand
    raise ToolMissing("armips não instalado — rode `factory setup --only armips`")


def ncpatcher() -> Path:
    d = TOOLCHAIN / "ncpatcher"
    for cand in (d / "ncpatcher.exe", *d.rglob("ncpatcher*.exe")):
        if cand.exists():
            return cand
    raise ToolMissing("NCPatcher não instalado — rode `factory setup --only ncpatcher`")


def fireflower_tool(name: str) -> Path:
    """nds-extract / nds-build shipped with Fireflower."""
    d = TOOLCHAIN / "fireflower"
    for cand in (d / f"{name}.exe", *d.rglob(f"{name}.exe")):
        if cand.exists():
            return cand
    raise ToolMissing(f"{name} não instalado — rode `factory setup --only fireflower`")


def arm_gnu_bin() -> Path:
    return _need(TOOLCHAIN / "arm-gnu" / "bin", "arm-gnu")


def arm_tool(name: str) -> Path:
    """arm-none-eabi-<name> (gcc, g++, as, ld, objdump, objcopy, gdb, ...)."""
    return _need(arm_gnu_bin() / f"arm-none-eabi-{name}{EXE}", "arm-gnu")


def java_home() -> Path:
    return _need(TOOLCHAIN / "jdk21", "jdk21")


def ghidra_home() -> Path:
    return _need(TOOLCHAIN / "ghidra", "ghidra")


def ghidra_headless() -> Path:
    return _need(ghidra_home() / "support" / ("analyzeHeadless.bat" if IS_WINDOWS else "analyzeHeadless"), "ghidra")


def ffmpeg() -> Path:
    exe = shutil.which("ffmpeg")
    if exe:
        return Path(exe)
    try:
        import imageio_ffmpeg  # type: ignore
        return Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception as exc:  # pragma: no cover - depends on environment
        raise ToolMissing(f"ffmpeg indisponível ({exc}) — rode `pip install imageio-ffmpeg` no .venv") from exc


def python() -> Path:
    return Path(sys.executable)


def tool_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for child processes: toolchain on PATH and the CodeWarrior license."""
    env = dict(os.environ)
    path_parts = [str(TOOLCHAIN / "bin")]
    arm = TOOLCHAIN / "arm-gnu" / "bin"
    if arm.exists():
        path_parts.append(str(arm))
    env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH", "")])
    lic = None
    try:
        lic = mwcc_license()
    except ToolMissing:
        pass
    if lic is not None:
        env["LM_LICENSE_FILE"] = str(lic)
    if (TOOLCHAIN / "jdk21").exists():
        env["JAVA_HOME"] = str(TOOLCHAIN / "jdk21")
        env["PATH"] = str(TOOLCHAIN / "jdk21" / "bin") + os.pathsep + env["PATH"]
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env


def rel(path: Path | str) -> str:
    """Path relative to the repo root when possible (stable, readable in reports)."""
    p = Path(path).resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)
