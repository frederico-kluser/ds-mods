"""Self-test: build an original homebrew ROM and drive the whole factory pipeline end to end.

The ROM (tools/selftest/*.s) mimics a retail NitroSDK binary closely enough for dsd, ndspy and BizHawk,
and has known observable behaviour (see the header of selftest_arm9.s), so every stage can be asserted:
ROM inspection → dsd extract → mod build (data + code patch) → BizHawk runs from boot and from a
savestate (with RAM hotpatch) → RAM watches → frame/video analysis → A/B comparison → freeze detection
→ static RE commands (dis/xrefs/find/hookcheck) → interactive record mode → mwccarm compile + match →
objdiff → live melonDS GDB → Ghidra.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import time
import traceback
from pathlib import Path

from . import ndsbuild, paths, rom
from .util import info, read_json, run, table, warn, write_json

SRC = paths.TOOLS / "selftest"
OUT = paths.RUNTIME / "selftest"
GAME_ID = "DMST"

# stable addresses defined in selftest_arm9.s
ADDR = {"config_base_color": 0x02000100, "score_step": 0x02000A00, "state": 0x02100000}

# NitroFS content of the test ROM (lets the file-replacement mod step be exercised)
SELFTEST_FILES = {
    "data/message.txt": b"DS-MODS SELFTEST FILE v1\n",
    "data/levels/level1.bin": bytes(range(64)),
    "readme.txt": b"factory selftest rom - original homebrew, no Nintendo code\n",
}


def _armips(src: Path, out_dir: Path, sym: Path) -> None:
    run([paths.armips(), src, "-root", out_dir, "-sym", sym], quiet=True)


def read_sym(path: Path) -> dict[str, int]:
    syms = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        addr, _, name = line.partition(" ")
        if name and not name.startswith((".", "@@")):
            try:
                syms[name] = int(addr, 16)
            except ValueError:
                pass
    return syms


def build_selftest_rom(out_dir: Path = OUT) -> Path:
    import ndspy.rom  # type: ignore

    out_dir.mkdir(parents=True, exist_ok=True)
    _armips(SRC / "selftest_ov0.s", out_dir, out_dir / "ov0.sym")    # imported by the ARM9 program
    _armips(SRC / "selftest_arm9.s", out_dir, out_dir / "arm9.sym")
    _armips(SRC / "selftest_arm7.s", out_dir, out_dir / "arm7.sym")
    syms = read_sym(out_dir / "arm9.sym")
    data = ndsbuild.build_rom(
        overlays=[{"id": 0, "ram": 0x02080000, "data": (out_dir / "ov0.bin").read_bytes()}],
        arm9=(out_dir / "arm9.bin").read_bytes(), arm7=(out_dir / "arm7.bin").read_bytes(),
        title="DSMODSTEST", gamecode=GAME_ID, arm9_entry=syms["_start"], arm7_entry=0x02380000,
        arm7_ram=0x02380000, module_params_offset=syms["module_params"] - 0x02000000,
        arm9_autoload_hook=syms["autoload_done_callback"], logo=ndspy.rom.NintendoDSRom().nintendoLogo,
        files=SELFTEST_FILES)
    out = out_dir / "selftest.nds"
    out.write_bytes(data)
    write_json(out_dir / "symbols.json", {k: f"0x{v:08X}" for k, v in sorted(syms.items(), key=lambda kv: kv[1])})
    return out


def ensure_selftest_rom() -> Path:
    out = OUT / "selftest.nds"
    newest_src = max(p.stat().st_mtime for p in SRC.glob("*.s"))
    if not out.exists() or out.stat().st_mtime < newest_src:
        build_selftest_rom()
    return out


# ── the end-to-end test ───────────────────────────────────────────────────────

class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name: str, ok: bool | None, detail: str = "") -> bool:
        status = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
        self.rows.append((name, status, detail))
        print(f"[selftest] {status:4}  {name}  {detail}", flush=True)
        return bool(ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r[1] == "FAIL")


def _step(checks: Checks, name: str, fn):
    try:
        return fn()
    except SystemExit as exc:
        checks.add(name, False, f"SystemExit {exc}")
    except Exception as exc:
        checks.add(name, False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    return None


def cmd_selftest(args) -> int:
    from . import game as game_mod

    checks = Checks()
    t0 = time.time()
    # isolate everything under toolchain/runtime/selftest (never touches the user's games/)
    work = OUT / "work"
    if work.exists() and not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    paths.GAMES = work / "games"
    paths.ROMS = work / "roms"

    rom_path = _step(checks, "build ROM (armips + ndsbuild)", lambda: build_selftest_rom())
    if not rom_path:
        print(table(checks.rows, ["teste", "status", "detalhe"]))
        return 1
    ri = rom.inspect(rom_path)
    checks.add("rom.inspect", ri.header_crc_ok and ri.logo_crc_ok and ri.secure_area == "decrypted"
               and ri.module_params is not None, f"crc={ri.header_crc_ok} logo={ri.logo_crc_ok} "
               f"secure={ri.secure_area} module_params={'sim' if ri.module_params else 'não'}")

    class A:  # argparse-like namespace for reusing command functions
        pass

    a = A()
    a.rom, a.id, a.no_extract, a.force = str(rom_path), GAME_ID, False, True
    rc = _step(checks, "factory new", lambda: game_mod.cmd_new(a))
    g = game_mod.load_game(GAME_ID)
    checks.add("factory new + dsd rom extract", rc == 0 and g.extract_config.exists(), paths.rel(g.extract))

    if not args.quick:
        _step(checks, "dsd init (análise)", lambda: _dsd_init_smoke(checks, g))
    _step(checks, "RE estática (dis/xrefs/find/hookcheck)", lambda: _static_re_smoke(checks))

    # scenario: from boot, press A three times, capture, watch RAM
    scen = {
        "description": "selftest: 3 toques em A a partir do boot",
        "start": {"type": "boot"}, "frames": 150,
        "inputs": [{"frame": 30, "hold": 4, "buttons": ["A"]}, {"frame": 60, "hold": 4, "buttons": ["A"]},
                   {"frame": 90, "hold": 4, "buttons": ["A"]}],
        "capture": {"every": 25, "frames": [1, 32, 150]},
        "watches": [{"name": "frames", "addr": "0x02100000", "type": "u32"},
                    {"name": "score", "addr": "0x02100004", "type": "u32"},
                    {"name": "magic", "addr": "0x02100010", "type": "u32"},
                    {"name": "ovcolor", "addr": "0x02080010", "type": "u16"}],
        "watch_every": 5,
        "save_states": [{"frame": 120, "name": "after_three"}],
        "ram_dumps": [1, 150],
        "expect": {"no_freeze": True, "no_black": True,
                   "ram": [{"watch": "score", "at": "end", "op": "==", "value": 3}],
                   "visual": [], "notes": "tela azul com barra branca descendo e barra verde de pontos"},
    }
    write_json(g.scenarios / "press_a.json", scen)
    from . import emu

    base_run = _step(checks, "BizHawk run (boot, base)", lambda: emu.run_scenario(g, "press_a"))
    base_res = read_json(base_run / "run.json")["result"] if base_run else {}
    checks.add("run base: result ok", bool(base_res.get("ok")), str(base_res.get("error") or ""))
    lw = base_res.get("last_watch") or {}
    checks.add("RAM watch: score == 3, magic DMST", lw.get("score") == 3 and lw.get("magic") == 0x54534D44, str(lw))
    if base_run:
        from . import ramsearch
        found = _step(checks, "ramsearch", lambda: ramsearch.search(base_run, ["f1 == 0", "f150 == 3"], dtype="u32")[0])
        addrs = [0x02000000 + 4 * int(i) for i in (found if found is not None else [])]
        checks.add("ramsearch acha o placar (0x02100004)", 0x02100004 in addrs, f"{len(addrs)} candidato(s)")
    st = base_run / "after_three.State" if base_run else None
    if st and st.exists():
        emu.register_state(g, "after_three", st, source="selftest", rom="base", record_frame=120)
    checks.add("savestate salvo no frame 120", bool(st and st.exists()))

    # mod: data patch (green background) + code patch (score step 2) built through the mods pipeline
    from . import mods as mods_mod

    mdir = g.mods / "green"
    mdir.mkdir(parents=True, exist_ok=True)
    write_json(mdir / "mod.json", {
        "name": "green", "description": "selftest: fundo verde + 2 pontos por toque",
        "steps": [{"type": "armips", "file": "patch.asm"},
                  {"type": "bytes", "patches": [{"module": "arm9", "addr": "0x02000104", "hex": "1F00"}]}]})
    (mdir / "patch.asm").write_text(
        ".nds\n.open ARM9_BIN, 0x02000000\n.arm\n"
        ".org 0x02000100\n    .halfword 0x03E0   ; base colour -> green\n"
        ".org 0x02000A00\n    add r1, r1, #2     ; score step 1 -> 2\n.close\n\n"
        ".open OV0_BIN, 0x02080000\n.org 0x02080010\n    .halfword 0x03FF   ; overlay: score bar -> yellow\n.close\n",
        encoding="utf-8")
    build = _step(checks, "factory mod build green", lambda: mods_mod.build_mod(g, "green"))
    hp = read_json(g.build / "mods" / "green" / "hotpatch.json", {}) or {}
    hunks = hp.get("patches", [])
    checks.add("mod build: ROM + hotpatch (arm9 + overlay com guarda)", bool(build) and len(hunks) >= 3
               and any(h.get("module") == "ov0" and h.get("guard") for h in hunks), f"{len(hunks)} hunks")

    mod_run = _step(checks, "BizHawk run (boot, mod)", lambda: emu.run_scenario(g, "press_a", variant="mod:green"))
    mres = read_json(mod_run / "run.json")["result"] if mod_run else {}
    lw = mres.get("last_watch") or {}
    checks.add("mod do boot: score == 6 e overlay amarelo", lw.get("score") == 6 and lw.get("ovcolor") == 0x03FF, str(lw))

    # savestate made on the BASE rom + mod rom → hotpatch must re-apply the mod in RAM
    write_json(g.scenarios / "from_state.json", {
        "description": "selftest: continua do state base e aperta A 2x", "start": {"type": "state", "state": "after_three"},
        "frames": 60, "inputs": [{"frame": 10, "hold": 4, "buttons": ["A"]}, {"frame": 30, "hold": 4, "buttons": ["A"]}],
        "capture": {"every": 20, "frames": [1, 60]},
        "watches": [{"name": "score", "addr": "0x02100004", "type": "u32"},
                    {"name": "ovcolor", "addr": "0x02080010", "type": "u16"}], "watch_every": 5,
        "expect": {"ram": [{"watch": "score", "at": "end", "op": "==", "value": 7}]}})
    hot_run = _step(checks, "BizHawk run (state base + mod + hotpatch)",
                    lambda: emu.run_scenario(g, "from_state", variant="mod:green"))
    hres = read_json(hot_run / "run.json")["result"] if hot_run else {}
    hstat = hres.get("hotpatch") or {}
    checks.add("hotpatch aplicado após load (3 hunks, 0 divergências)", hstat.get("applied", 0) >= 3
               and hstat.get("mismatch", 1) == 0, str(hstat))
    hw = hres.get("last_watch") or {}
    checks.add("state+hotpatch: score 3 → 7 e overlay amarelo", hw.get("score") == 7 and hw.get("ovcolor") == 0x03FF, str(hw))
    nohot_run = _step(checks, "BizHawk run (state base + mod, SEM hotpatch)",
                      lambda: emu.run_scenario(g, "from_state", variant="mod:green", no_hotpatch=True, tag="nohot"))
    nres = read_json(nohot_run / "run.json")["result"] if nohot_run else {}
    nw = nres.get("last_watch") or {}
    checks.add("controle: sem hotpatch o código do state roda (score 5, overlay verde)",
               nw.get("score") == 5 and nw.get("ovcolor") == 0x03E0, str(nw))

    # analysis: base vs mod (visual + RAM), video, freeze
    from . import analyze

    rep = _step(checks, "analyze base vs mod", lambda: analyze.analyze_run(mod_run, ref=base_run))
    if rep:
        cmp_ = rep.get("comparison") or {}
        checks.add("comparação detecta mudança visual", (cmp_.get("max_changed_pct") or 0) >= 40,
                   f"max_changed_pct={cmp_.get('max_changed_pct')}")
        checks.add("veredito: expect.ram avaliado", "expect" in rep, json.dumps(rep.get("expect"), ensure_ascii=False)[:160])
        sheet = Path(rep.get("contact_sheet", ""))
        checks.add("contact sheet gerado", sheet.exists() if rep.get("contact_sheet") else False, rep.get("contact_sheet", ""))
    same_rep = _step(checks, "analyze base vs base (controle)",
                     lambda: analyze.analyze_run(_step(checks, "run base 2", lambda: emu.run_scenario(g, "press_a", tag="det")),
                                                 ref=base_run))
    if same_rep:
        checks.add("determinismo: base vs base idênticos", (same_rep.get("comparison") or {}).get("max_changed_pct", 1) == 0,
                   str((same_rep.get("comparison") or {}).get("max_changed_pct")))

    write_json(g.scenarios / "crash.json", {
        "description": "selftest: START congela o programa", "start": {"type": "boot"}, "frames": 240,
        "inputs": [{"frame": 60, "hold": 2, "buttons": ["Start"]}], "capture": {"all": True, "video": True},
        "expect": {"no_freeze": True}})
    crash_run = _step(checks, "BizHawk run com vídeo (crash simulado)", lambda: emu.run_scenario(g, "crash"))
    if crash_run:
        rj = read_json(crash_run / "run.json")
        checks.add("video.mp4 gerado", bool(rj.get("video")) and (paths.ROOT / rj["video"]).exists(), str(rj.get("video")))
        crep = _step(checks, "analyze crash", lambda: analyze.analyze_run(crash_run, ref=None))
        if crep:
            fr = crep.get("freezes") or []
            checks.add("detecção de congelamento (só depois do START)", any(f["frames"] >= 100 for f in fr)
                       and not any(f["from"] < 55 for f in fr), json.dumps(fr)[:200])
            checks.add("captura frame-exata (barra = frame-1)", _bar_tracks_frames(crash_run), "frames 2..50")
            checks.add("expect.no_freeze reprovado", (crep.get("expect") or {}).get("passed") is False,
                       json.dumps(crep.get("expect"), ensure_ascii=False)[:160])
        if rj.get("video"):
            vrep = _step(checks, "analyze vídeo externo (mp4)", lambda: analyze.analyze_video(paths.ROOT / rj["video"]))
            checks.add("vídeo: frames amostrados + freeze", bool(vrep) and vrep.get("sampled", 0) > 5
                       and bool(vrep.get("freezes")), f"sampled={vrep and vrep.get('sampled')}")

    _step(checks, "factory play (modo gravação)", lambda: _record_smoke(checks, g))

    if not args.quick:
        _step(checks, "mwccarm compila C", lambda: _mwcc_smoke(checks))
        _step(checks, "objdiff-cli", lambda: checks.add("objdiff-cli --version", run([paths.objdiff_cli(), "--version"],
                                                                                       capture=True, quiet=True).returncode == 0))
        _step(checks, "melonDS GDB ao vivo", lambda: _gdb_smoke(checks))
        if args.ghidra:
            from . import ghidra as ghidra_mod
            _step(checks, "Ghidra headless decompile", lambda: _ghidra_smoke(checks, g, ghidra_mod))

    print()
    print(table(checks.rows, ["teste", "status", "detalhe"]))
    info(f"selftest: {len(checks.rows)} verificações, {checks.failed} falha(s), {time.time() - t0:.0f}s")
    write_json(OUT / "last_selftest.json", {"rows": checks.rows, "failed": checks.failed,
                                             "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    return 1 if checks.failed else 0


def _bar_tracks_frames(run_dir: Path) -> bool:
    """The white bar row follows the frame counter: every captured frame must show it one row further
    down than the previous one (constant offset), i.e. no stale or skipped screenshots."""
    import numpy as np
    from PIL import Image

    offset = None
    for f in range(2, 51):
        p = run_dir / "frames" / f"f{f:06d}.png"
        if not p.exists():
            return False
        top = np.asarray(Image.open(p).convert("RGB"))[:192]
        rows = np.where(top.min(axis=(1, 2)) > 240)[0].tolist()
        if len(rows) != 1:
            return False
        offset = rows[0] - f if offset is None else offset
        if rows[0] - f != offset:
            return False
    return offset in (-1, 0)


def _dsd_init_smoke(checks: Checks, g) -> None:
    out = g.decomp / "config"
    p = run([paths.dsd(), "init", "--rom-config", g.extract_config, "--output-path", out,
             "--build-path", g.decomp / "build"], check=False, capture=True, quiet=True)
    msg = (p.stderr or p.stdout or "").strip().splitlines()
    checks.add("dsd init no ROM sintético", p.returncode == 0 or None,
               "ok" if p.returncode == 0 else f"(esperado em homebrew) {msg[-1] if msg else p.returncode}")


def _run_cli(argv: list[str]) -> tuple[int, str]:
    """Run a factory command in-process and capture what it prints."""
    from . import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, buf.getvalue()


def _static_re_smoke(checks: Checks) -> None:
    rc, out = _run_cli(["dis", "0x02000A00", "--game", GAME_ID, "--count", "4"])
    checks.add("dis score_step (add + bx lr)", rc == 0 and "add" in out and "bx" in out)
    rc, out = _run_cli(["xrefs", "0x02000A00", "--game", GAME_ID])
    checks.add("xrefs (scan bruto) acha o BL para score_step", rc == 0 and "arm bl" in out)
    rc, out = _run_cli(["find", "--string", "DSMODS-SELFTEST", "--game", GAME_ID, "--files"])
    rc2, out2 = _run_cli(["find", "--u32", "0x54534D44", "--game", GAME_ID])
    checks.add("find --string / --u32", rc == 0 and "0x02000108" in out and rc2 == 0 and "arm9" in out2)
    _, arm = _run_cli(["hookcheck", "0x02000A00", "--kind", "hook", "--game", GAME_ID])
    _, thumb = _run_cli(["hookcheck", "0x02000A00", "--kind", "hook", "--thumb", "--game", GAME_ID])
    checks.add("hookcheck: hook ARM seguro, a partir de THUMB proibido",
               ("SEGURO" in arm or "ATENÇÃO" in arm) and "PROIBIDO" in thumb)


def _record_smoke(checks: Checks, g) -> None:
    from . import emu

    rom_path, _, _ = emu.resolve_rom(g, "base")
    out_dir = g.runs / f"{time.strftime('%Y%m%d-%H%M%S')}_playtest"
    res = emu.launch(rom_path, {"mode": "record", "out_dir": out_dir, "sound": False, "exit": True, "max_frames": 90},
                     out_dir, interactive=True, encrypted=False, timeout=120)
    inputs = out_dir / "inputs.txt"
    checks.add("play/record: termina sozinho e grava inputs.txt", bool(res.get("ok")) and res.get("frames") == 90
               and inputs.exists() and inputs.read_text(encoding="utf-8").startswith("#"),
               f"frames={res.get('frames')} {res.get('error') or ''}")


def _mwcc_smoke(checks: Checks) -> None:
    from . import mwcc

    src = OUT / "mw_smoke.c"
    src.write_text("int add_score(int s, int step) { return s + step; }\n", encoding="utf-8")
    obj = mwcc.compile_c(src, OUT / "mw_smoke.o", version="2.0/sp2p4")
    checks.add("mwccarm 2.0/sp2p4 → .o", obj.exists() and obj.stat().st_size > 0, paths.rel(obj))
    # match oracle: an equivalent rewrite must MATCH, a different body must show the side-by-side diff
    tmp = OUT / "mwmatch"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "target.c").write_text("int add_step(int s, int step) { return s + step * 2; }\n", encoding="utf-8")
    target = mwcc.compile_c(tmp / "target.c", tmp / "target.o")
    (tmp / "same.c").write_text("int add_step(int score, int st) { return score + (st << 1); }\n", encoding="utf-8")
    (tmp / "diff.c").write_text("int add_step(int s, int step) { return s - step; }\n", encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        same = mwcc.match(tmp / "same.c", target, "add_step")
        diff = mwcc.match(tmp / "diff.c", target, "add_step")
    out = buf.getvalue()
    checks.add("mwcc match: equivalente → MATCH, diferente → diff", same and not diff and ">>> MATCH <<<" in out
               and "SEU CÓDIGO" in out)


def _gdb_smoke(checks: Checks) -> None:
    from . import debug, gdbproxy

    st = read_json(debug.PID_FILE) or {}
    if st.get("pid") and debug._alive(int(st["pid"])):
        checks.add("melonDS GDB", None, "pulado: há uma sessão `factory debug` aberta")
        return

    class A:
        pass

    a = A()
    a.game, a.rom, a.port, a.proxy_port, a.break_on_start, a.no_gdb = GAME_ID, "base", 3333, debug.PROXY_PORT, False, False
    try:
        if not checks.add("melonDS + stub GDB + ponte", debug.cmd_start(a) == 0):
            return
        r1 = gdbproxy.request(a.proxy_port, {"op": "read", "addr": 0x02100010, "size": 4})
        r2 = gdbproxy.request(a.proxy_port, {"op": "read", "addr": 0x02100010, "size": 4})
        checks.add("gdb: 2 leituras na mesma sessão (magic DMST)", r1.get("data") == r2.get("data") == "444d5354",
                   f"{r1.get('data') or r1.get('error')} {r2.get('data') or r2.get('error')}")
        b = gdbproxy.request(a.proxy_port, {"op": "break", "addr": 0x02000A08, "wait": 10}, timeout=40)
        pc = (b.get("regs") or {}).get("pc")
        checks.add("gdb: breakpoint em wait_vblank para e segura a CPU", bool(b.get("hit")) and pc == 0x02000A08
                   and bool(b.get("halted")), f"hit={b.get('hit')} pc={pc:08X}" if pc else str(b.get("error")))
        gdbproxy.request(a.proxy_port, {"op": "write", "addr": 0x02100004, "hex": "09000000"})
        w = gdbproxy.request(a.proxy_port, {"op": "read", "addr": 0x02100004, "size": 4})
        c = gdbproxy.request(a.proxy_port, {"op": "continue"})
        checks.add("gdb: write + read + continue", w.get("data") == "09000000" and c.get("ok") and not c.get("halted"),
                   f"placar={w.get('data')} halted={c.get('halted')}")
    finally:
        debug.cmd_stop(None)


def _ghidra_smoke(checks: Checks, g, ghidra_mod) -> None:
    ghidra_mod.ghidra_import(g, force=True, analyze=False)
    layout = read_json(g.dir / "ghidra" / "layout.json") or {}
    checks.add("Ghidra: layout com overlay space", any(m["kind"] == "overlay" for m in layout.get("modules", [])),
               ", ".join(m["name"] for m in layout.get("modules", [])))
    res = ghidra_mod.decompile(g, ["0x02000800", "0x02000A00"])
    ok = "0x2080000" in res and "FUN_02000800" in res
    checks.add("Ghidra decompila _start (cópia do overlay p/ 0x02080000)", ok, res[:100].replace("\n", " "))


def register(sub) -> None:
    p = sub.add_parser("selftest", help="teste ponta-a-ponta da fábrica com uma ROM homebrew própria")
    p.add_argument("--quick", action="store_true", help="pular dsd init / mwccarm / objdiff / melonDS GDB")
    p.add_argument("--ghidra", action="store_true", help="incluir decompilação headless no Ghidra (lento)")
    p.add_argument("--keep", action="store_true", help="não apagar o workspace anterior")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("selftest-rom", help="só montar a ROM de teste (toolchain/runtime/selftest/selftest.nds)")
    p.set_defaults(func=lambda a: (print(build_selftest_rom()), 0)[1])
