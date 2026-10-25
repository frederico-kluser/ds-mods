"""`factory doctor`: verify every piece of the factory and say exactly what is missing."""
from __future__ import annotations

import json
import shutil
import subprocess

from . import paths
from .util import info, read_json, table

OK, WARN, FAIL = "OK", "AVISO", "FALHA"


def _probe(argv: list[str], *, timeout: float = 30, env: dict | None = None) -> tuple[bool, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=env or paths.tool_env())
    except FileNotFoundError:
        return False, "não encontrado"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    out = (p.stdout or p.stderr or "").strip().splitlines()
    return p.returncode == 0, (out[0] if out else f"exit {p.returncode}")


def check_python() -> list[tuple[str, str, str]]:
    rows = []
    for mod in ("numpy", "PIL", "cv2", "yaml", "ndspy", "capstone", "elftools", "imageio_ffmpeg"):
        try:
            m = __import__(mod)
            rows.append((f"py:{mod}", OK, getattr(m, "__version__", "importado")))
        except Exception as exc:  # pragma: no cover
            rows.append((f"py:{mod}", FAIL, f"{exc} — rode bootstrap.ps1"))
    return rows


def check_tools() -> list[tuple[str, str, str]]:
    rows = []

    def add(name: str, fn, argv_tail: list[str] | None = None, optional: bool = False, timeout: float = 30):
        try:
            exe = fn()
        except paths.ToolMissing as exc:
            rows.append((name, WARN if optional else FAIL, str(exc)))
            return
        if argv_tail is None:
            rows.append((name, OK, paths.rel(exe)))
            return
        ok, msg = _probe([str(exe), *argv_tail], timeout=timeout)
        rows.append((name, OK if ok else (WARN if optional else FAIL), msg if ok else f"{paths.rel(exe)}: {msg}"))

    add("dsd", paths.dsd, ["--version"])
    add("objdiff-cli", paths.objdiff_cli, ["--version"])
    add("ninja", paths.ninja, ["--version"])
    try:
        vers = paths.mwcc_versions()
        lic = paths.mwcc_license()
        rows.append(("mwccarm", OK if vers else FAIL,
                     f"{len(vers)} versões ({', '.join(vers[:3])} …) license={'ok' if lic.exists() else 'FALTA'}"))
        ok, msg = _probe([str(paths.mwcc_dir("2.0/sp2p4") / "mwccarm.exe"), "-version"])
        rows.append(("mwccarm 2.0/sp2p4", OK if "build 139" in msg or ok else WARN, msg))
    except paths.ToolMissing as exc:
        rows.append(("mwccarm", FAIL, str(exc)))
    add("armips", paths.armips)
    add("ncpatcher", paths.ncpatcher)
    add("nds-extract", lambda: paths.fireflower_tool("nds-extract"))
    add("nds-build", lambda: paths.fireflower_tool("nds-build"))
    add("arm-none-eabi-gcc", lambda: paths.arm_tool("gcc"), ["--version"])
    add("arm-none-eabi-gdb", lambda: paths.arm_tool("gdb"), ["--version"], optional=True)
    add("EmuHawk (BizHawk)", paths.emuhawk)
    add("melonDS", paths.melonds)
    add("DeSmuME", paths.desmume, optional=True)
    add("neondst", paths.neondst, optional=True)
    add("ffmpeg", paths.ffmpeg, ["-version"])
    add("JDK 21", lambda: paths.java_home() / "bin" / "java.exe", ["-version"], optional=True)
    try:
        gh = paths.ghidra_home()
        ext = gh / "Ghidra" / "Extensions" / "dsd-ghidra"
        rows.append(("Ghidra 11.2.1 (Windows)", OK, paths.rel(gh)))
        rows.append(("dsd-ghidra", OK if ext.exists() else WARN, paths.rel(ext) if ext.exists() else "não instalado"))
    except paths.ToolMissing as exc:
        rows.append(("Ghidra (Windows)", WARN, str(exc)))
    return rows


def check_bios() -> list[tuple[str, str, str]]:
    have = [n for n in ("bios7.bin", "bios9.bin", "firmware.bin") if (paths.BIOS / n).exists()]
    if len(have) == 3:
        return [("BIOS/firmware DS", OK, "roms/bios/{bios7,bios9,firmware}.bin")]
    return [("BIOS/firmware DS", WARN,
             "ausente (só necessário p/ ROMs com secure area criptografada; ver roms/bios/README.md)")]


def check_wsl() -> list[tuple[str, str, str]]:
    if not shutil.which("wsl"):
        return [("WSL2 (REA)", WARN, "wsl.exe ausente — REA/Ghidra 12 indisponíveis (Ghidra Windows segue ok)")]
    marker = read_json(paths.INSTALLED / "wsl-rea.json")
    ok, msg = _probe(["wsl", "-d", marker.get("distro", "Ubuntu") if marker else "Ubuntu", "--", "bash", "-lc",
                      "command -v rea >/dev/null && rea --version || echo 'rea ausente'"], timeout=60)
    state = OK if ok and "ausente" not in msg else WARN
    hint = "" if state == OK else " — rode `factory ghidra wsl-setup`"
    return [("WSL2 + REA (MCP)", state, msg + hint)]


def check_repo() -> list[tuple[str, str, str]]:
    rows = []
    from . import memory

    rows.append(("memória CoALA", OK if memory.ENGINE.exists() else FAIL, paths.rel(memory.ENGINE)))
    state, detail = memory.dump_state()
    rows.append(("memória no git (dump)", OK if state == "ok" else (FAIL if state == "error" else WARN), detail))
    for skill in ("nds-rea-modder", "nds-visual-loop"):
        p = paths.ROOT / ".claude" / "skills" / skill / "SKILL.md"
        rows.append((f"skill {skill}", OK if p.exists() else WARN, paths.rel(p)))
    games = sorted(d.name for d in paths.GAMES.iterdir() if (d / "game.json").exists()) if paths.GAMES.exists() else []
    rows.append(("jogos", OK, ", ".join(games) if games else "nenhum ainda — `factory new roms/<rom>.nds`"))
    return rows


def cmd_doctor(args) -> int:
    rows = check_python() + check_tools() + check_bios() + check_repo()
    if not args.no_wsl:
        rows += check_wsl()
    if args.json:
        print(json.dumps([{"item": r[0], "status": r[1], "detail": r[2]} for r in rows], ensure_ascii=False, indent=2))
    else:
        print(table(rows, ["item", "status", "detalhe"]))
    fails = [r for r in rows if r[1] == FAIL]
    info(f"{len(rows)} verificações · {len(fails)} falha(s) · {sum(r[1] == WARN for r in rows)} aviso(s)")
    return 1 if fails else 0


def register(sub) -> None:
    p = sub.add_parser("doctor", help="diagnóstico completo da fábrica")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-wsl", action="store_true", help="não consultar o WSL (mais rápido)")
    p.set_defaults(func=cmd_doctor)
