"""BizHawk (EmuHawk + melonDS core) automation: scenarios, captures, interactive recording, savestates.

Scenario file (games/<ID>/scenarios/<name>.json) — every field optional except `frames`:
{
  "description": "o que este cenário testa",
  "start": {"type": "state", "state": "battle_start"}            # savestate by name (states/index.json) or path
         | {"type": "boot", "inputs_file": "inputs/boot.txt"},   # power-on + recorded input replay
  "frames": 600,                                                 # frames to run after the start point
  "inputs": [{"frame": 30, "hold": 5, "buttons": ["A"]},          # frame 1 = first frame after start
             {"frame": 90, "hold": 3, "touch": [128, 96]}],
  "capture": {"every": 30, "frames": [1, 600], "all": false, "video": false},
  "watches": [{"name": "hp", "addr": "0x0212F3A0", "type": "u16"}],   # type u8/s8/u16/s16/u32/s32
  "watch_every": 10,
  "save_states": [{"frame": 600, "name": "after_fight"}],
  "ram_dumps": [1, 600],                                         # Main RAM snapshots → factory ramsearch
  "expect": {"no_freeze": true, "no_black": true,
             "ram": [{"watch": "hp", "at": "end", "op": ">=", "value": 999}],
             "visual": [{"frame": 600, "region": [0, 0, 256, 192], "vs": "baseline", "min_changed_pct": 5}],
             "notes": "o que deve aparecer na tela (julgado pelo agente olhando o contact sheet)"}
}
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import time
from pathlib import Path

from . import paths
from .game import Game, bios_paths, load_game
from .util import fail, info, parse_int, read_json, table, warn, write_json

BUTTONS = {"A", "B", "X", "Y", "L", "R", "Start", "Select", "Up", "Down", "Left", "Right",
           "LidOpen", "LidClose", "Power", "Microphone"}
DEFAULT_CFG = paths.RUNTIME / "bizhawk-default.json"


# ── BizHawk configuration ─────────────────────────────────────────────────────

def _lua_literal(v) -> str:
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, Path):
        v = str(v).replace("\\", "/")
    if isinstance(v, str):
        esc = v.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")
        return "'" + esc + "'"
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(_lua_literal(x) for x in v) + "}"
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            key = k if k.isidentifier() else f"[{_lua_literal(k)}]"
            parts.append(f"{key} = {_lua_literal(x)}")
        return "{" + ", ".join(parts) + "}"
    raise TypeError(f"não serializável para Lua: {type(v)}")


def ensure_default_config() -> dict:
    """Default EmuHawk config for this BizHawk version (generated once by launching EmuHawk)."""
    data = read_json(DEFAULT_CFG)
    if data:
        return data
    paths.RUNTIME.mkdir(parents=True, exist_ok=True)
    tmp = paths.RUNTIME / "gen"
    tmp.mkdir(exist_ok=True)
    lua = tmp / "exit.lua"
    lua.write_text("client.exit()\n", encoding="utf-8")
    from .selftest import ensure_selftest_rom  # lazy: avoid import cycle
    rom = ensure_selftest_rom()
    info("gerando configuração padrão do BizHawk (abre e fecha o EmuHawk uma vez)…")
    subprocess.run([str(paths.emuhawk()), f"--config={DEFAULT_CFG}", f"--lua={lua}", str(rom)], timeout=120)
    data = read_json(DEFAULT_CFG)
    if not data:
        fail("não consegui gerar a config padrão do BizHawk")
    return data


def make_config(dest: Path, *, interactive: bool, encrypted: bool = False) -> Path:
    cfg = copy.deepcopy(ensure_default_config())
    cfg.update({
        "FirstBoot": False, "UpdateAutoCheckEnabled": False, "SkipOutdatedOsCheck": True,
        "SkipSuperuserPrivsCheck": True, "SingleInstanceMode": False, "RunInBackground": True,
        "PauseWhenMenuActivated": False, "StartPaused": False, "SaveWindowPosition": False,
        "ScreenshotCaptureOsd": False, "AviCaptureOsd": False,
        "SoundEnabled": bool(interactive), "DisplayMessages": bool(interactive),
        "RecentRoms": {"recentlist": [], "MAX_RECENT_FILES": 10, "AutoLoad": False, "Frozen": False},
        "RecentLua": {"recentlist": [], "MAX_RECENT_FILES": 8, "AutoLoad": False, "Frozen": False},
    })
    key = "BizHawk.Emulation.Cores.Consoles.Nintendo.NDS.NDS"
    sync = cfg.setdefault("CoreSyncSettings", {}).setdefault(key, {
        "$type": "BizHawk.Emulation.Cores.Consoles.Nintendo.NDS.NDS+NDSSyncSettings, BizHawk.Emulation.Cores"})
    # determinism: fixed RTC + software 3D + no JIT, so replays and A/B comparisons line up
    sync.update({"UseRealTime": False, "InitialTime": "2010-01-01T00:00:00", "ThreeDeeRenderer": 0,
                 "EnableJIT": False, "SkipFirmware": True})
    bios = bios_paths()
    if len(bios) == 3:
        cfg["FirmwareUserSpecifications"] = {
            "NDS+bios7": str(bios["bios7.bin"]), "NDS+bios9": str(bios["bios9.bin"]),
            "NDS+firmware": str(bios["firmware.bin"])}
        sync["UseRealBIOS"] = bool(encrypted)
    settings = cfg.setdefault("CoreSettings", {}).setdefault(key, {
        "$type": "BizHawk.Emulation.Cores.Consoles.Nintendo.NDS.NDS+NDSSettings, BizHawk.Emulation.Cores"})
    settings.update({"ScreenLayout": 0, "ScreenGap": 0, "ScreenInvert": False, "ScreenRotation": 0})
    write_json(dest, cfg)
    return dest


# ── states index ──────────────────────────────────────────────────────────────

def states_index(g: Game) -> dict:
    return read_json(g.states / "index.json", {"states": {}}) or {"states": {}}


def save_states_index(g: Game, idx: dict) -> None:
    write_json(g.states / "index.json", idx)


def resolve_state(g: Game, ref: str) -> Path:
    idx = states_index(g)["states"]
    if ref in idx:
        p = g.states / idx[ref]["file"]
    else:
        p = Path(ref)
        if not p.is_absolute():
            p = (g.dir / ref) if (g.dir / ref).exists() else (g.states / ref)
    if not p.exists():
        fail(f"savestate '{ref}' não encontrado (conhecidos: {', '.join(idx) or 'nenhum'})")
    return p


def register_state(g: Game, name: str, src: Path, **meta) -> Path:
    g.states.mkdir(parents=True, exist_ok=True)
    dest = g.states / f"{name}.State"
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    shot = src.with_suffix(".png")
    if shot.exists() and shot.resolve() != dest.with_suffix(".png").resolve():
        shutil.copy2(shot, dest.with_suffix(".png"))
    idx = states_index(g)
    idx["states"][name] = {"file": dest.name, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), **meta}
    save_states_index(g, idx)
    return dest


# ── ROM variants ──────────────────────────────────────────────────────────────

def resolve_rom(g: Game, variant: str | None) -> tuple[Path, str, list | None]:
    """'base' | 'original' | 'mod:<name>' | path → (rom, label, hotpatch list or None).

    'base' is the dsd round-trip of the original (same size/layout as mod ROMs, so savestates cross between
    them); 'original' is the untouched dump."""
    if variant == "original":
        return g.rom, "original", None
    if not variant or variant == "base":
        emu_base = g.meta.get("emu_base")
        if emu_base and (paths.ROOT / emu_base).exists():
            return paths.ROOT / emu_base, "base", None
        return g.rom, "base", None
    if variant.startswith("mod:"):
        name = variant[4:]
        out = g.build / "mods" / name
        manifest = read_json(out / "build.json")
        if not manifest:
            fail(f"mod '{name}' ainda não foi construído — rode `factory mod build {name}`")
        rom = paths.ROOT / manifest["rom"]
        hp = read_json(out / "hotpatch.json", {}) or {}
        return rom, f"mod-{name}", hp.get("patches")
    p = Path(variant)
    if not p.exists():
        fail(f"ROM '{variant}' não encontrada")
    return p, p.stem, None


# ── scenarios ─────────────────────────────────────────────────────────────────

def load_scenario(g: Game, ref: str) -> tuple[str, dict]:
    p = Path(ref)
    if not p.suffix:
        p = g.scenarios / f"{ref}.json"
    elif not p.is_absolute() and not p.exists():
        p = g.scenarios / ref
    data = read_json(p)
    if data is None:
        known = sorted(x.stem for x in g.scenarios.glob("*.json")) if g.scenarios.exists() else []
        fail(f"cenário '{ref}' não encontrado (conhecidos: {', '.join(known) or 'nenhum'})")
    data.setdefault("frames", 300)
    data.setdefault("start", {"type": "boot"})
    data.setdefault("capture", {"every": 30})
    return p.stem, data


def _scenario_list(items, what: str) -> list:
    if items is None:
        return []
    if not isinstance(items, list):
        fail(f"cenário: \"{what}\" deve ser uma lista, não {type(items).__name__}")
    return items


def _normalize_inputs(items: list) -> list:
    out = []
    for it in _scenario_list(items, "inputs"):
        try:
            e = {"frame": int(it["frame"]), "hold": int(it.get("hold", 1))}
            btns = it.get("buttons") or []
            touch = [int(v) for v in it["touch"]] if it.get("touch") else None
            if not isinstance(btns, list) or (touch is not None and len(touch) != 2):
                raise ValueError("buttons deve ser lista e touch deve ser [x, y]")
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
            fail(f"cenário: input inválido {it!r} ({exc})")
        bad = [b for b in btns if not isinstance(b, str) or b not in BUTTONS]
        if bad:
            fail(f"botões inválidos {bad} (válidos: {sorted(BUTTONS)})")
        e["buttons"] = btns
        if touch is not None:
            e["touch"] = touch
        out.append(e)
    return out


WATCH_TYPES = ("u8", "s8", "u16", "s16", "u32", "s32")      # readers of factory_runner.lua


def _normalize_watches(items: list) -> list:
    out = []
    for w in _scenario_list(items, "watches"):
        try:
            entry = {"name": w["name"], "addr": parse_int(w["addr"]), "type": w.get("type", "u32")}
            domain = w.get("domain")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            fail(f"cenário: watch inválido {w!r} ({exc})")
        if not isinstance(entry["name"], str) or entry["addr"] < 0 or entry["type"] not in WATCH_TYPES:
            fail(f"cenário: watch inválido {w!r} (name texto, addr ≥ 0, type em {', '.join(WATCH_TYPES)})")
        if domain:
            entry["domain"] = str(domain)
        out.append(entry)
    return out


def build_run_config(g: Game, scen: dict, out_dir: Path, *, hotpatch: list | None,
                     state_override: str | None = None, frames: int | None = None,
                     capture_all: bool = False) -> dict:
    start = scen.get("start", {"type": "boot"})
    cfg: dict = {"mode": "capture", "out_dir": out_dir, "frames": int(frames or scen["frames"]),
                 "sound": False, "speed": 6400, "exit": True}
    state_ref = state_override or (start.get("state") if start.get("type") == "state" else None)
    if state_ref:
        cfg["state"] = resolve_state(g, state_ref)
    if start.get("type") == "boot" and start.get("inputs_file"):
        cfg["input_log"] = (g.dir / start["inputs_file"]).resolve()
    cfg["inputs"] = _normalize_inputs(scen.get("inputs"))
    cap = dict(scen.get("capture") or {})
    if capture_all:
        cap["all"] = True
    cfg["capture"] = {"every": int(cap.get("every", 0)), "frames": [int(f) for f in cap.get("frames", [])],
                      "all": bool(cap.get("all", False)), "first": bool(cap.get("first", True))}
    cfg["watches"] = _normalize_watches(scen.get("watches"))
    cfg["watch_every"] = int(scen.get("watch_every", 1))
    cfg["save_states"] = [{"frame": int(s["frame"]), "path": out_dir / f"{s['name']}.State"}
                          for s in scen.get("save_states", [])]
    if scen.get("ram_dumps"):
        cfg["ram_dumps"] = [int(f) for f in scen["ram_dumps"]]
        if scen.get("ram_dump_domains"):
            cfg["ram_dump_domains"] = list(scen["ram_dump_domains"])
    if hotpatch:
        cfg["hotpatch"] = [{k: v for k, v in hp.items() if k in ("addr", "orig", "new", "module", "domain",
                                                                    "optional", "guard")} for hp in hotpatch]
        for hp in cfg["hotpatch"]:
            hp["addr"] = parse_int(hp["addr"])
            if hp.get("guard"):
                hp["guard"] = {"addr": parse_int(hp["guard"]["addr"]), "bytes": hp["guard"]["bytes"]}
        cfg["hotpatch_every"] = int(scen.get("hotpatch_every", 30))
    return cfg


def launch(rom: Path, run_cfg: dict, out_dir: Path, *, interactive: bool, encrypted: bool,
           timeout: float | None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "frames").mkdir(exist_ok=True)
    (out_dir / "ram").mkdir(exist_ok=True)
    cfg_lua = out_dir / "run_config.lua"
    cfg_lua.write_text("return " + _lua_literal(run_cfg) + "\n", encoding="utf-8")
    ini = make_config(out_dir / "bizhawk.ini", interactive=interactive, encrypted=encrypted)
    argv = [str(paths.emuhawk()), f"--config={ini}", f"--lua={paths.LUA / 'factory_runner.lua'}",
            f"--userdata=cfg:{str(cfg_lua).replace(chr(92), '/')}", str(rom)]
    info(f"EmuHawk → {paths.rel(out_dir)}")
    t0 = time.time()
    with open(out_dir / "emuhawk.stderr.txt", "w", encoding="utf-8") as err:
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=err, cwd=str(paths.emuhawk().parent))
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            warn(f"EmuHawk excedeu {timeout:.0f}s e foi encerrado")
    result = read_json(out_dir / "result.json")
    if result is None:
        tail = ""
        if (out_dir / "runner.log").exists():
            tail = (out_dir / "runner.log").read_text(encoding="utf-8", errors="replace")[-1500:]
        result = {"ok": False, "error": "result.json ausente (EmuHawk fechou antes do fim?)", "runner_log": tail}
    result["wall_seconds"] = round(time.time() - t0, 2)
    return result


def make_video(run_dir: Path, fps: int = 60) -> Path | None:
    frames = sorted((run_dir / "frames").glob("f*.png"))
    if len(frames) < 2:
        return None
    lst = run_dir / "frames.ffconcat"
    nums = [int(p.stem[1:]) for p in frames]
    lines = ["ffconcat version 1.0"]
    for i, p in enumerate(frames):
        dur = (nums[i + 1] - nums[i]) / fps if i + 1 < len(frames) else 1 / fps
        lines += [f"file '{p.name}'".replace("\\", "/"), f"duration {dur:.6f}"]
    lines.append(f"file '{frames[-1].name}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = run_dir / "video.mp4"
    cmd = [str(paths.ffmpeg()), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst.name,
           "-vf", "scale=iw*2:ih*2:flags=neighbor,format=yuv420p", "-r", str(fps), "-c:v", "libx264", "-crf", "18",
           out.name]
    p = subprocess.run(cmd, cwd=str(run_dir / "frames"), capture_output=True, text=True)
    if p.returncode != 0:
        # concat paths are relative to the list file; retry from the run dir with explicit paths
        lines2 = [ln.replace("file '", "file 'frames/") if ln.startswith("file '") else ln for ln in lines]
        lst.write_text("\n".join(lines2) + "\n", encoding="utf-8")
        cmd[cmd.index(lst.name)] = str(lst)
        cmd[-1] = str(out)
        p = subprocess.run(cmd, cwd=str(run_dir), capture_output=True, text=True)
    if p.returncode != 0:
        warn(f"ffmpeg falhou: {p.stderr.strip()[-500:]}")
        return None
    return out


def run_scenario(g: Game, scenario: str, *, variant: str | None = None, state: str | None = None,
                 frames: int | None = None, video: bool = False, tag: str | None = None,
                 out_dir: Path | None = None, timeout: float | None = None, no_hotpatch: bool = False) -> Path:
    name, scen = load_scenario(g, scenario)
    rom, label, hotpatch = resolve_rom(g, variant)
    if no_hotpatch:
        hotpatch = None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = out_dir or (g.runs / f"{stamp}_{name}_{label}{('_' + tag) if tag else ''}")
    want_video = video or bool((scen.get("capture") or {}).get("video"))
    run_cfg = build_run_config(g, scen, run_dir, hotpatch=hotpatch, state_override=state, frames=frames,
                               capture_all=want_video)
    n = run_cfg["frames"]
    if run_cfg.get("input_log"):
        n += _input_log_span(Path(run_cfg["input_log"]))
    timeout = timeout or (90 + n / 15)
    encrypted = g.meta.get("secure_area") == "encrypted" and variant in (None, "base")
    result = launch(rom, run_cfg, run_dir, interactive=False, encrypted=encrypted, timeout=timeout)
    meta = {"game": g.id, "scenario": name, "scenario_file": paths.rel(g.scenarios / f"{name}.json"),
            "variant": label, "rom": paths.rel(rom), "state": str(run_cfg.get("state") or ""),
            "frames": run_cfg["frames"], "result": result, "created_at": stamp,
            "expect": scen.get("expect"), "watches": [w["name"] for w in run_cfg["watches"]]}
    if result.get("ok") and want_video:
        v = make_video(run_dir)
        meta["video"] = paths.rel(v) if v else None
    write_json(run_dir / "run.json", meta)
    status = "OK" if result.get("ok") else "FALHOU"
    info(f"run {status}: {paths.rel(run_dir)}  frames={result.get('emu_framecount')} capturas={result.get('captured')}"
         f" fps≈{(result.get('fps') or 0):.0f}  hotpatch={result.get('hotpatch')}")
    if not result.get("ok"):
        warn(f"erro: {result.get('error')}")
        if "savestate" in str(result.get("error")) and label != "base":
            warn("o BizHawk só carrega um savestate numa ROM do MESMO tamanho. Se o mod mudou o tamanho da ROM, "
                 "recrie o state na ROM do mod: `factory state make --inputs <log> --name <x> --rom "
                 f"{variant}` e use-o no cenário (ou rode o cenário com start=boot + inputs_file).")
    return run_dir


def _input_log_span(p: Path) -> int:
    last = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            try:
                last = max(last, int(line.split()[0]))
            except ValueError:
                pass
    return last


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_run(args) -> int:
    g = load_game(args.game)
    run_dir = run_scenario(g, args.scenario, variant=args.rom, state=args.state, frames=args.frames,
                           video=args.video, tag=args.tag, timeout=args.timeout, no_hotpatch=args.no_hotpatch)
    res = read_json(run_dir / "run.json")["result"]
    if args.analyze and res.get("ok"):
        from .analyze import analyze_run
        analyze_run(run_dir, ref=None)
    print(run_dir)
    return 0 if res.get("ok") else 1


def cmd_play(args) -> int:
    """Interactive session: the human plays; inputs are logged and F11 saves named checkpoints."""
    g = load_game(args.game)
    rom, label, hotpatch = resolve_rom(g, args.rom)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = g.runs / f"{stamp}_play_{label}"
    run_cfg = {"mode": "record", "out_dir": out_dir, "sound": True, "exit": True}
    if args.state:
        run_cfg["state"] = resolve_state(g, args.state)
    if hotpatch:
        run_cfg["hotpatch"] = [{"addr": parse_int(h["addr"]), "orig": h["orig"], "new": h["new"],
                                "module": h.get("module")} for h in hotpatch]
    info("Jogue normalmente. F11 = salvar checkpoint (savestate + screenshot); End = terminar e sair.")
    info("Controles padrão: setas, X=A, Z=B, S=X, A=Y, W=L, E=R, Enter=Start, Espaço=Select, mouse=touch.")
    encrypted = g.meta.get("secure_area") == "encrypted" and label == "base"
    result = launch(rom, run_cfg, out_dir, interactive=True, encrypted=encrypted, timeout=None)
    # persist inputs and checkpoints into the game workspace
    inputs_src = out_dir / "inputs.txt"
    session = args.name or f"play_{stamp}"
    if inputs_src.exists():
        (g.dir / "inputs").mkdir(exist_ok=True)
        dest = g.dir / "inputs" / f"{session}.txt"
        shutil.copy2(inputs_src, dest)
        info(f"inputs gravados: {paths.rel(dest)} ({result.get('frames')} frames)")
    events = [json.loads(ln) for ln in (out_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()] \
        if (out_dir / "events.jsonl").exists() else []
    n = 0
    for ev in events:
        if ev.get("kind") != "checkpoint":
            continue
        n += 1
        src = out_dir / f"{ev['name']}.State"
        sname = f"{session}_cp{n:02d}"
        register_state(g, sname, src, source="play", rom=label, record_frame=ev.get("record_frame"),
                       inputs_file=f"inputs/{session}.txt", from_state=args.state)
        info(f"savestate registrado: {sname} (frame {ev.get('record_frame')})")
    if n == 0:
        info("nenhum checkpoint salvo (use F11 durante o jogo)")
    return 0 if result.get("ok") else 1


def cmd_state(args) -> int:
    g = load_game(args.game)
    idx = states_index(g)
    if args.action == "list":
        rows = [(k, v.get("file"), v.get("source", "?"), v.get("rom", "?"), v.get("record_frame", ""),
                 v.get("note", "")) for k, v in sorted(idx["states"].items())]
        print(table(rows, ["nome", "arquivo", "origem", "rom", "frame", "nota"]) if rows else "nenhum savestate")
        return 0
    if args.action == "import":
        if not args.path or not args.name:
            fail("uso: factory state import <arquivo.State> --name <nome>")
        dest = register_state(g, args.name, Path(args.path), source="import", note=args.note or "")
        info(f"importado: {paths.rel(dest)}")
        return 0
    if args.action == "note":
        if args.name not in idx["states"]:
            fail(f"savestate '{args.name}' não existe")
        idx["states"][args.name]["note"] = args.note or ""
        save_states_index(g, idx)
        return 0
    if args.action == "make":
        # boot (or start from a state), replay an input log, and save a state at the end
        if not args.name or not args.inputs:
            fail("uso: factory state make --inputs inputs/x.txt --name <nome> [--frames N] [--rom mod:<m>]")
        rom, label, _ = resolve_rom(g, args.rom)
        log = (g.dir / args.inputs) if not Path(args.inputs).is_absolute() else Path(args.inputs)
        span = args.frames or _input_log_span(log)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_dir = g.runs / f"{stamp}_makestate_{args.name}"
        run_cfg = {"mode": "capture", "out_dir": out_dir, "frames": span, "input_log": log, "capture": {"frames": [span]},
                   "final_state": out_dir / f"{args.name}.State", "speed": 6400, "exit": True}
        if args.from_state:
            run_cfg["state"] = resolve_state(g, args.from_state)
        result = launch(rom, run_cfg, out_dir, interactive=False,
                        encrypted=g.meta.get("secure_area") == "encrypted" and label == "base",
                        timeout=90 + span / 15)
        if not result.get("ok"):
            fail(f"falhou: {result.get('error')}")
        shot = out_dir / "frames" / f"f{span:06d}.png"
        if shot.exists():
            shutil.copy2(shot, out_dir / f"{args.name}.png")
        register_state(g, args.name, out_dir / f"{args.name}.State", source="make", rom=label,
                       inputs_file=paths.rel(log), record_frame=span, from_state=args.from_state)
        info(f"savestate '{args.name}' criado a partir de {paths.rel(log)} ({span} frames, rom={label})")
        return 0
    fail(f"ação desconhecida: {args.action}")


def cmd_scenario(args) -> int:
    g = load_game(args.game)
    if args.action == "list":
        rows = []
        for p in sorted(g.scenarios.glob("*.json")):
            d = read_json(p) or {}
            st = d.get("start", {})
            rows.append((p.stem, st.get("type", "boot"), st.get("state", st.get("inputs_file", "")), d.get("frames", ""),
                         (d.get("description") or "")[:60]))
        print(table(rows, ["cenário", "início", "state/inputs", "frames", "descrição"]) if rows else "nenhum cenário")
        return 0
    if args.action == "new":
        if not args.name:
            fail("uso: factory scenario new <nome> [--state S | --inputs inputs/x.txt] [--frames N]")
        p = g.scenarios / f"{args.name}.json"
        if p.exists() and not args.force:
            fail(f"{paths.rel(p)} já existe (use --force)")
        start = {"type": "state", "state": args.state} if args.state else \
            ({"type": "boot", "inputs_file": args.inputs} if args.inputs else {"type": "boot"})
        data = {"description": args.description or "descreva o que este cenário exercita",
                "start": start, "frames": args.frames or 600, "inputs": [],
                "capture": {"every": 30, "frames": [1], "video": False},
                "watches": [], "watch_every": 10,
                "expect": {"no_freeze": True, "no_black": True, "ram": [], "visual": [],
                           "notes": "o que deve mudar na tela com o mod"}}
        write_json(p, data)
        info(f"cenário criado: {paths.rel(p)} — edite inputs/watches/expect")
        return 0
    fail(f"ação desconhecida: {args.action}")


def register(sub) -> None:
    p = sub.add_parser("run", help="rodar um cenário no BizHawk (state/boot → inputs → capturas/RAM)")
    p.add_argument("scenario", help="nome em games/<ID>/scenarios/ ou caminho .json")
    p.add_argument("--game")
    p.add_argument("--rom", help="base | mod:<nome> | caminho.nds (padrão: base)")
    p.add_argument("--state", help="sobrescreve o savestate inicial do cenário")
    p.add_argument("--frames", type=int)
    p.add_argument("--video", action="store_true", help="capturar todos os frames e gerar video.mp4")
    p.add_argument("--tag", help="sufixo do diretório do run")
    p.add_argument("--timeout", type=float)
    p.add_argument("--no-hotpatch", action="store_true", help="não reaplicar o mod na RAM após carregar o state")
    p.add_argument("--analyze", action="store_true", help="rodar `factory analyze` em seguida")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("play", help="sessão interativa: você joga; inputs gravados; F11 = checkpoint; End = sair")
    p.add_argument("--game")
    p.add_argument("--rom", help="base | mod:<nome> | caminho.nds")
    p.add_argument("--state", help="começar de um savestate")
    p.add_argument("--name", help="nome da sessão (prefixo dos checkpoints)")
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("state", help="savestates: list | import | note | make")
    p.add_argument("action", choices=["list", "import", "note", "make"])
    p.add_argument("path", nargs="?", help="(import) arquivo .State")
    p.add_argument("--game")
    p.add_argument("--name")
    p.add_argument("--note")
    p.add_argument("--inputs", help="(make) log de inputs a reproduzir")
    p.add_argument("--frames", type=int, help="(make) frames a rodar (padrão: fim do log)")
    p.add_argument("--from-state", help="(make) começar deste state em vez do boot")
    p.add_argument("--rom", help="(make) base | mod:<nome>")
    p.set_defaults(func=cmd_state)

    p = sub.add_parser("scenario", help="cenários de teste: list | new")
    p.add_argument("action", choices=["list", "new"])
    p.add_argument("name", nargs="?")
    p.add_argument("--game")
    p.add_argument("--state")
    p.add_argument("--inputs")
    p.add_argument("--frames", type=int)
    p.add_argument("--description")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_scenario)
