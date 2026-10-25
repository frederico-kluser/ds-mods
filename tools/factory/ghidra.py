"""Ghidra integration.

Windows (toolchain/ghidra = Ghidra 11.2.1 + dsd-ghidra 0.7.0, JDK 21):
  factory ghidra import      — headless DS program built by FactoryDsLayout.java: arm9 + ITCM/DTCM + each
                               overlay in its own overlay space + dsd symbols (THUMB-aware)
  factory ghidra sync        — re-apply symbols.txt after renaming things in the dsd config
  factory ghidra decompile   — C of functions/addresses (name | 0x0201.. | ov12:0x0214.. | t:0x.. for THUMB)
  factory ghidra gui         — opens the GUI (the human can also import the .nds with dsd-ghidra there:
                               its loader asks "ARM7/ARM9" in a dialog, so it cannot run headless;
                               FactorySyncDsd.java is the headless SyncDsd for programs made that way)
WSL2 (REA MCP, Ghidra 12.1.4): factory ghidra wsl-setup / wsl-doctor / mcp-config / wslpath
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import paths
from .game import Game, load_game
from .util import fail, info, read_json, write_json

SCRIPTS = paths.TOOLS / "ghidra_scripts"
WSL_DISTRO = os.environ.get("DSMODS_WSL_DISTRO", "Ubuntu")


def _headless(project_dir: Path, project_name: str, extra: list[str], *, timeout: float = 1800) -> subprocess.CompletedProcess:
    project_dir.mkdir(parents=True, exist_ok=True)
    env = paths.tool_env({"JAVA_HOME": str(paths.java_home())})
    argv = [str(paths.ghidra_headless()), str(project_dir), project_name, *extra]
    info("$ analyzeHeadless " + " ".join(extra[:6]) + (" …" if len(extra) > 6 else ""))
    p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                       timeout=timeout)
    log = project_dir / "last_headless.log"
    log.write_text((p.stdout or "") + "\n--- stderr ---\n" + (p.stderr or ""), encoding="utf-8")
    return p


def gproj(g: Game) -> tuple[Path, str]:
    return g.dir / "ghidra", g.id


def build_layout(g: Game) -> Path:
    """JSON consumed by FactoryDsLayout.java: module binaries (decompressed, from the dsd extract) and
    the dsd symbols when a decomp project exists."""
    from .dsdproj import find_project
    from .mods import WorkTree

    mods = WorkTree(g.extract).modules()
    modules = []
    for name, m in mods.items():
        if name == "arm7" or m.get("base") is None:
            continue
        if name == "arm9":
            entry = {"name": "arm9", "kind": "main"}
        elif m["kind"] == "overlay":
            entry = {"name": f"ov{m['id']:03d}", "kind": "overlay"}
        else:
            entry = {"name": name, "kind": name}
        entry.update({"base": int(m["base"]), "file": str(m["bin"])})
        modules.append(entry)
    modules.sort(key=lambda e: (e["kind"] != "main", e["name"]))
    symbols = []
    proj = find_project(g.dir)
    if proj:
        for mod in proj.load().modules.values():
            mname = "arm9" if mod.name == "main" else mod.name
            for s in mod.symbols:
                symbols.append({"module": mname, "addr": s.addr, "kind": "function" if s.kind == "function" else "data",
                                "mode": s.mode or "arm", "size": s.size, "name": s.name})
    out = g.dir / "ghidra" / "layout.json"
    write_json(out, {"modules": modules, "symbols": symbols})
    return out


def ghidra_import(g: Game, *, force: bool = False, analyze: bool | None = None) -> str:
    """Headless DS program: arm9 + ITCM/DTCM + every overlay in its own overlay space (+ dsd symbols).

    The dsd-ghidra loader is not used here because it opens a modal "Choose CPU program" dialog, which
    blocks analyzeHeadless; it stays available for the human in the GUI (File → Import → choose ARM9)."""
    from .mods import WorkTree

    pdir, pname = gproj(g)
    marker = pdir / "import.json"
    program = "arm9.bin"
    if marker.exists() and not force:
        info(f"programa já importado ({paths.rel(pdir)}); use --force para reimportar")
        return (read_json(marker) or {}).get("program", program)
    if pdir.exists():
        shutil.rmtree(pdir)
    layout = build_layout(g)
    nsyms = len((read_json(layout) or {}).get("symbols", []))
    if analyze is None:
        analyze = nsyms == 0
    arm9 = WorkTree(g.extract).modules()["arm9"]["bin"]
    args = ["-import", str(arm9), "-loader", "BinaryLoader", "-loader-baseAddr", "0x02000000",
            "-processor", "ARM:LE:32:v5t", "-noanalysis", "-overwrite", "-scriptPath", str(SCRIPTS),
            "-postScript", "FactoryDsLayout.java", str(layout)] + (["analyze"] if analyze else [])
    p = _headless(pdir, pname, args, timeout=7200)
    out = (p.stdout or "") + (p.stderr or "")
    if "Import succeeded" not in out:
        fail("import do Ghidra falhou — veja " + paths.rel(pdir / "last_headless.log"))
    write_json(marker, {"program": program, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "symbols": nsyms,
                        "analyzed": analyze, "layout": paths.rel(layout)})
    info(f"programa Ghidra pronto: {paths.rel(pdir)} ({nsyms} símbolos dsd, auto-análise={'sim' if analyze else 'não'})")
    return program


def sync_symbols(g: Game) -> None:
    """Re-apply dsd symbols to the existing program (after editing symbols.txt)."""
    pdir, pname = gproj(g)
    if not (pdir / "import.json").exists():
        ghidra_import(g)
        return
    layout = build_layout(g)
    _headless(pdir, pname, ["-process", "arm9.bin", "-noanalysis", "-scriptPath", str(SCRIPTS),
                            "-postScript", "FactoryDsLayout.java", str(layout)], timeout=3600)
    info("símbolos do dsd reaplicados no programa Ghidra")


def decompile(g: Game, targets: list[str], *, out: Path | None = None) -> str:
    pdir, pname = gproj(g)
    if not (pdir / "import.json").exists():
        ghidra_import(g)
    program = (read_json(pdir / "import.json") or {}).get("program", g.rom.name)
    out = out or (pdir / "decompiled.c")
    _headless(pdir, pname, ["-process", program, "-noanalysis", "-readOnly", "-scriptPath", str(SCRIPTS),
                            "-postScript", "FactoryDecompile.java", str(out), *targets])
    if not out.exists():
        fail("decompilação não produziu saída — veja " + paths.rel(pdir / "last_headless.log"))
    return out.read_text(encoding="utf-8", errors="replace")


def decompile_raw(g: Game, *, module: str, functions: list[str], timeout: float = 1800) -> str:
    """Import one extracted module as a raw ARMv5T binary at its base and decompile addresses."""
    from .mods import WorkTree
    mods = WorkTree(g.extract).modules()
    if module not in mods:
        fail(f"módulo {module} não existe")
    m = mods[module]
    pdir = g.dir / "ghidra" / "raw"
    name = f"{g.id}_{module}"
    program = m["bin"].name
    marker = pdir / f"{module}.json"
    if not marker.exists():
        p = _headless(pdir, name, ["-import", str(m["bin"]), "-loader", "BinaryLoader", "-loader-baseAddr",
                                   f"0x{m['base']:08X}", "-processor", "ARM:LE:32:v5t", "-overwrite"],
                      timeout=timeout)
        if "Import succeeded" not in (p.stdout or "") + (p.stderr or ""):
            fail("import raw falhou — veja " + paths.rel(pdir / "last_headless.log"))
        write_json(marker, {"program": program, "base": f"0x{m['base']:08X}"})
    out = pdir / f"decompiled_{module}.c"
    p = _headless(pdir, name, ["-process", program, "-noanalysis", "-readOnly", "-scriptPath", str(SCRIPTS),
                               "-postScript", "FactoryDecompile.java", str(out), *functions], timeout=timeout)
    return out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""


def cmd_import(args) -> int:
    g = load_game(args.game)
    analyze = True if args.analyze else (False if args.no_analyze else None)
    ghidra_import(g, force=args.force, analyze=analyze)
    return 0


def cmd_sync(args) -> int:
    sync_symbols(load_game(args.game))
    return 0


def cmd_decompile(args) -> int:
    g = load_game(args.game)
    if args.raw:
        txt = decompile_raw(g, module=args.raw, functions=args.targets)
    else:
        txt = decompile(g, args.targets)
    print(txt)
    return 0


def cmd_gui(args) -> int:
    g = load_game(args.game)
    run_bat = paths.ghidra_home() / "ghidraRun.bat"
    pdir, pname = gproj(g)
    gpr = pdir / f"{pname}.gpr"
    env = paths.tool_env({"JAVA_HOME": str(paths.java_home())})
    subprocess.Popen([str(run_bat), str(gpr)] if gpr.exists() else [str(run_bat)], env=env,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    info(f"Ghidra GUI aberto ({paths.rel(gpr) if gpr.exists() else 'sem projeto'}). Não rode Auto-Analyze antes do "
         "SyncDsd (menu Analysis → Sync DSD) — recomendação do dsd-ghidra.")
    return 0


# ── WSL2 / REA ────────────────────────────────────────────────────────────────

def wslpath(p: Path | str) -> str:
    p = Path(p).resolve()
    drive = p.drive.rstrip(":").lower()
    rest = p.as_posix().split(":", 1)[1]
    return f"/mnt/{drive}{rest}"


def _wsl(cmd: str, *, root: bool = False, timeout: float = 3600) -> subprocess.CompletedProcess:
    argv = ["wsl", "-d", WSL_DISTRO]
    if root:
        argv += ["-u", "root"]
    argv += ["--", "bash", "-lc", cmd]
    env = dict(os.environ, WSL_UTF8="1")
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)


def cmd_wsl_setup(args) -> int:
    if not shutil.which("wsl"):
        fail("WSL não está instalado (wsl.exe ausente)")
    script = wslpath(paths.TOOLS / "wsl" / "provision-rea.sh")
    lock_dir = wslpath(paths.TOOLS / "wsl" / "rea")      # package.json + package-lock.json (npm ci)
    info(f"provisionando REA no WSL ({WSL_DISTRO}) como root — pode levar alguns minutos…")
    p = _wsl(f"tr -d '\\r' < '{script}' > /tmp/provision-rea.sh && REA_LOCK_DIR='{lock_dir}' bash /tmp/provision-rea.sh",
             root=True)
    print(p.stdout[-4000:])
    if p.returncode != 0:
        print(p.stderr[-4000:])
        fail("provisionamento falhou")
    write_json(paths.INSTALLED / "wsl-rea.json", {"distro": WSL_DISTRO, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    write_mcp_config()
    return cmd_wsl_doctor(args)


def cmd_wsl_doctor(args) -> int:
    p = _wsl("rea doctor --json 2>/dev/null | head -c 20000", timeout=300)
    out = p.stdout.strip()
    try:
        data = json.loads(out)
        summary = data.get("summary") or data.get("status") or {k: data[k] for k in list(data)[:5]}
        print(json.dumps(summary, indent=2, ensure_ascii=False)[:3000])
    except Exception:
        print(out[:3000] or p.stderr[:2000])
    return 0 if p.returncode == 0 else 1


def write_mcp_config() -> Path:
    """Project-scoped MCP registration (.mcp.json) for Claude Code: REA runs inside WSL via stdio."""
    mcp_path = paths.ROOT / ".mcp.json"
    data = read_json(mcp_path, {}) or {}
    servers = data.setdefault("mcpServers", {})
    servers["rea"] = {
        "type": "stdio",
        "command": "wsl.exe",
        "args": ["-d", WSL_DISTRO, "--", "bash", "-lc", "exec rea mcp"],
        "env": {},
    }
    write_json(mcp_path, data)
    info(f"MCP 'rea' registrado em {paths.rel(mcp_path)} (via WSL '{WSL_DISTRO}'); reinicie o Claude Code")
    return mcp_path


def cmd_mcp_config(args) -> int:
    write_mcp_config()
    return 0


def cmd_wslpath(args) -> int:
    print(wslpath(args.path))
    return 0


def register(sub) -> None:
    p = sub.add_parser("ghidra", help="Ghidra (Windows, dsd-ghidra) e REA MCP (WSL)")
    s = p.add_subparsers(dest="ghidra_cmd", metavar="<ação>")
    q = s.add_parser("import", help="montar o programa DS headless (arm9+ITCM/DTCM+overlays+símbolos dsd)")
    q.add_argument("--game")
    q.add_argument("--force", action="store_true")
    q.add_argument("--analyze", action="store_true", help="forçar auto-análise (padrão: só sem símbolos dsd)")
    q.add_argument("--no-analyze", action="store_true")
    q.set_defaults(func=cmd_import)
    q = s.add_parser("sync", help="reaplicar symbols.txt do dsd no programa Ghidra")
    q.add_argument("--game")
    q.set_defaults(func=cmd_sync)
    q = s.add_parser("decompile", help="decompilar funções (nome | 0x.. | ovN:0x.. | t:0x..)")
    q.add_argument("targets", nargs="+")
    q.add_argument("--game")
    q.add_argument("--raw", metavar="MÓDULO", help="usar import raw do módulo (arm9, itcm, ovN) em vez do loader")
    q.set_defaults(func=cmd_decompile)
    q = s.add_parser("gui", help="abrir o Ghidra (GUI) no projeto do jogo")
    q.add_argument("--game")
    q.set_defaults(func=cmd_gui)
    q = s.add_parser("wsl-setup", help="instalar REA + Ghidra 12.1.4 + JDK 21 no WSL e registrar o MCP")
    q.set_defaults(func=cmd_wsl_setup)
    q = s.add_parser("wsl-doctor", help="rea doctor dentro do WSL")
    q.set_defaults(func=cmd_wsl_doctor)
    q = s.add_parser("mcp-config", help="(re)escrever .mcp.json com o servidor REA via WSL")
    q.set_defaults(func=cmd_mcp_config)
    q = s.add_parser("wslpath", help="converter caminho Windows → /mnt/... (para passar ao REA)")
    q.add_argument("path")
    q.set_defaults(func=cmd_wslpath)
