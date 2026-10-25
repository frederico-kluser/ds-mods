"""Via A — matching decompilation on top of dsd.

Layout (games/<ID>/decomp/, ninja runs from here):
  config/arm9/config.yaml (+ symbols/relocs/delinks per module)   ← dsd init (versioned)
  asm/            dsd dis --ual (ignored)        build/   delinks, objects, LCF, ELF, rebuilt ROM (ignored)
  src/<module>/<func>.c   one function per file (khdays-decomp style)       include/   shared headers
  decomp.json     compiler version + flags chosen for this game              functions.json  status per function

Per-function loop (fast, no linking):  scaffold → edit C → `factory decomp match <func>` → >>> MATCH <<<
The original bytes come from the extracted module binary; relocations from dsd relocs.txt are masked and
their targets compared by symbol name (so the C must call/load the same symbols that symbols.txt names).
Full rebuild (ph-style ninja graph): `factory decomp configure && factory decomp build && factory decomp check`.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from . import mwcc, paths
from .disasm import modules
from .dsdproj import Project, Symbol, find_project
from .game import Game, extract_rom, load_game
from .util import fail, info, read_json, run, table, warn, write_json

ELF_CALL_TYPES = {1, 10, 28, 29, 30}       # PC24, THM_PC22, CALL, JUMP24, THM_JUMP24
ELF_ABS_TYPES = {2}
DSD_CALL_KINDS = {"arm_call", "thumb_call", "arm_call_thumb", "thumb_call_arm", "arm_branch", "thumb_branch"}


def decomp_cfg(g: Game) -> dict:
    return read_json(g.decomp / "decomp.json", {}) or {}


def project(g: Game) -> Project:
    proj = find_project(g.dir)
    if not proj:
        fail("projeto dsd não inicializado — rode `factory decomp init`")
    return proj


# ── init ──────────────────────────────────────────────────────────────────────

def cmd_init(args) -> int:
    g = load_game(args.game)
    if not g.extract_config.exists() and not extract_rom(g):
        return 1
    d = g.decomp
    cfg_dir = d / "config"
    arm9_cfg = cfg_dir / "arm9" / "config.yaml"
    if arm9_cfg.exists() and not args.force:
        info(f"projeto dsd já existe: {paths.rel(arm9_cfg)} (use --force para recriar)")
    else:
        d.mkdir(parents=True, exist_ok=True)
        base_argv = [paths.dsd(), "init", "--rom-config", g.extract_config, "--output-path", cfg_dir,
                     "--build-path", d / "build"]
        # hidden flag in dsd 0.12.x (used by khdays-decomp): tolerate calls into not-yet-known functions
        p = run([*base_argv, "--allow-unknown-function-calls"], check=False, capture=True)
        if p.returncode != 0 and "unexpected argument" in ((p.stderr or "") + (p.stdout or "")):
            p = run(base_argv, check=False, capture=True)
        if p.returncode != 0:
            print((p.stdout or "") + (p.stderr or ""))
            fail("dsd init falhou — veja a mensagem acima (ROMs não-NitroSDK/homebrew não são suportadas pelo dsd)")
    run([paths.dsd(), "sig", "apply", "--config-path", arm9_cfg, "--all"], check=False)
    run([paths.dsd(), "delink", "--config-path", arm9_cfg])
    run([paths.dsd(), "dis", "--config-path", arm9_cfg, "--asm-path", d / "asm", "--ual"], check=False)
    cfg = decomp_cfg(g)
    cfg.setdefault("compiler", mwcc.DEFAULT_VERSION)
    cfg.setdefault("flags", mwcc.DEFAULT_FLAGS)
    cfg.setdefault("note", "compilador/flags padrão (KH Days/ph). Confirme com `factory decomp sweep` numa função simples.")
    objdiff_argv = [paths.dsd(), "objdiff", "--config-path", arm9_cfg, "--output-path", d, "--c-flags",
                    " ".join(cfg["flags"]), "--custom-make", "ninja"]
    if mwcc.DECOMPME_IDS.get(cfg["compiler"]):
        objdiff_argv += ["--scratch", "--compiler", mwcc.DECOMPME_IDS[cfg["compiler"]]]
    run(objdiff_argv, check=False)
    dsprot = run([paths.dsd(), "fix", "dsprot", "--config-path", arm9_cfg, "--dry"], check=False, capture=True, quiet=True)
    cfg["dsprot_check"] = ((dsprot.stdout or "") + (dsprot.stderr or "")).strip().splitlines()[-3:]
    for sub in ("src", "include"):
        (d / sub).mkdir(exist_ok=True)
    types_h = d / "include" / "types.h"
    if not types_h.exists():
        types_h.write_text(TYPES_H, encoding="utf-8")
    cfg["initialized_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json(d / "decomp.json", cfg)
    proj = Project(arm9_cfg).load()
    nf = sum(1 for m in proj.modules.values() for s in m.symbols if s.kind == "function")
    named = sum(1 for m in proj.modules.values() for s in m.symbols if s.kind == "function" and not s.name.startswith("func_"))
    info(f"dsd pronto: {len(proj.modules)} módulos, {nf} funções ({named} com nome via assinaturas/SDK)")
    write_candidates(g, proj)
    return 0


TYPES_H = """#ifndef DSMODS_TYPES_H
#define DSMODS_TYPES_H
/* Tipos básicos do NitroSDK (equivalentes). Ajuste/expanda conforme o jogo. */
typedef unsigned char u8;   typedef signed char s8;
typedef unsigned short u16; typedef signed short s16;
typedef unsigned int u32;   typedef signed int s32;
typedef unsigned long long u64; typedef signed long long s64;
typedef volatile u8 vu8; typedef volatile u16 vu16; typedef volatile u32 vu32;
typedef int BOOL;
#define TRUE 1
#define FALSE 0
#ifndef NULL
#define NULL ((void*)0)
#endif
typedef s32 fx32;           /* ponto fixo 20.12: FX32_ONE = 0x1000 */
typedef s16 fx16;
#define FX32_ONE 0x1000
#define FX32_SHIFT 12
#endif
"""


# ── candidates (leaf vs calls) ────────────────────────────────────────────────

def _module_bytes(g: Game) -> dict[str, tuple[int, bytes]]:
    return {m.dsd_name: (m.base, m.data) for m in modules(g)}


def classify(g: Game, proj: Project) -> list[dict]:
    mods = _module_bytes(g)
    out = []
    for m in proj.load().modules.values():
        base_data = mods.get(m.name)
        calls_by_func: dict[int, int] = {}
        funcs = [s for s in m.symbols if s.kind == "function" and s.size]
        rel_srcs = sorted(r.src for r in m.relocs if r.kind in DSD_CALL_KINDS)
        import bisect
        for s in funcs:
            lo = bisect.bisect_left(rel_srcs, s.addr)
            hi = bisect.bisect_left(rel_srcs, s.addr + s.size)
            calls_by_func[s.addr] = hi - lo
        for s in funcs:
            out.append({"name": s.name, "module": m.name, "addr": f"0x{s.addr:08X}", "size": s.size,
                        "mode": s.mode or "arm", "calls": calls_by_func.get(s.addr, 0),
                        "kind": "leaf" if calls_by_func.get(s.addr, 0) == 0 else "calls",
                        "named": not s.name.startswith("func_"),
                        "has_bytes": base_data is not None})
    out.sort(key=lambda r: (r["kind"] != "leaf", r["size"]))
    return out


def write_candidates(g: Game, proj: Project) -> Path:
    rows = classify(g, proj)
    p = g.decomp / "candidates.tsv"
    lines = ["name\tmodule\taddr\tsize\tmode\tcalls\tkind\tnamed"]
    lines += [f"{r['name']}\t{r['module']}\t{r['addr']}\t{r['size']}\t{r['mode']}\t{r['calls']}\t{r['kind']}\t"
              f"{int(r['named'])}" for r in rows]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    leaf = sum(1 for r in rows if r["kind"] == "leaf")
    info(f"candidatos: {paths.rel(p)} ({leaf} folhas, {len(rows) - leaf} com chamadas)")
    return p


def cmd_candidates(args) -> int:
    g = load_game(args.game)
    rows = classify(g, project(g))
    if args.module:
        rows = [r for r in rows if r["module"] == args.module]
    if args.leaf:
        rows = [r for r in rows if r["kind"] == "leaf"]
    done = set(json.loads((g.decomp / "functions.json").read_text(encoding="utf-8")).get("matched", {})) \
        if (g.decomp / "functions.json").exists() else set()
    rows = [r for r in rows if r["name"] not in done and r["size"] <= args.max_size]
    print(table([(r["name"], r["module"], r["addr"], r["size"], r["mode"], r["kind"]) for r in rows[: args.limit]],
                ["função", "módulo", "endereço", "bytes", "modo", "tipo"]))
    return 0


# ── per-function work ─────────────────────────────────────────────────────────

def find_function(proj: Project, name: str) -> Symbol:
    syms = [s for s in proj.find(name) if s.kind == "function" and s.name == name] or \
           [s for s in proj.find(name) if s.kind == "function"]
    if not syms:
        fail(f"função '{name}' não está nos symbols.txt")
    if len(syms) > 1:
        warn("vários: " + ", ".join(f"{s.name}@{s.module}" for s in syms[:5]) + " — usando o 1º")
    return syms[0]


def src_path(g: Game, s: Symbol) -> Path:
    return g.decomp / "src" / s.module / f"{s.name}.c"


def original_function(g: Game, proj: Project, s: Symbol) -> mwcc.FuncBytes:
    mods = _module_bytes(g)
    if s.module not in mods:
        fail(f"binário do módulo {s.module} não encontrado na extração")
    base, data = mods[s.module]
    off = s.addr - base
    fb = mwcc.FuncBytes(bytearray(data[off:off + s.size]), {})
    for r in proj.xrefs_from(s.addr, s.addr + s.size, s.module):
        nm = proj.name_of(r.dst, r.target_module) or f"0x{r.dst:08X}"
        kind = 2 if r.kind == "load" else 28
        fb.relocs[r.src - s.addr] = (nm, kind)
    return fb


def compare_loose(mine: mwcc.FuncBytes, orig: mwcc.FuncBytes) -> tuple[bool, list[str]]:
    """Like mwcc.compare but reloc kinds are compared by category (dsd kinds vs ELF types)."""
    def cat(t: int) -> str:
        return "abs" if t in ELF_ABS_TYPES else "call"
    m2 = mwcc.FuncBytes(mine.data, {o: (n, cat(t)) for o, (n, t) in mine.relocs.items()})
    o2 = mwcc.FuncBytes(orig.data, {o: (n, cat(t)) for o, (n, t) in orig.relocs.items()})
    return mwcc.compare(m2, o2)


def cmd_scaffold(args) -> int:
    g = load_game(args.game)
    proj = project(g)
    s = find_function(proj, args.func)
    p = src_path(g, s)
    if p.exists() and not args.force:
        fail(f"{paths.rel(p)} já existe")
    from .disasm import format_listing, pick_module
    mods = modules(g)
    mod = pick_module(mods, s.addr, {"main": "arm9"}.get(s.module, s.module if not s.module.startswith("ov") else f"ov{int(s.module[2:])}"))
    listing = format_listing(g, mod, s.addr, s.mode or "arm", count=None, size=s.size, proj=proj)
    callees = sorted({r_name for r_name, _ in original_function(g, proj, s).relocs.values()})
    body = [f"/* {s.name} — {s.module} 0x{s.addr:08X}, {s.size} bytes, {s.mode or 'arm'} */",
            '#include "types.h"', ""]
    for c in callees:
        if not c.startswith("0x"):
            body.append(f"extern void {c}();  /* ajuste o protótipo */")
    body += ["", "/*", *[ln.rstrip() for ln in listing], "*/", ""]
    if (s.mode or "arm") == "thumb":
        body.append("#pragma thumb on")
    body += [f"void {s.name}(void) {{", "    /* TODO: traduzir o assembly acima */", "}"]
    if (s.mode or "arm") == "thumb":
        body.append("#pragma thumb reset")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    info(f"criado {paths.rel(p)} — edite e rode `factory decomp match {s.name}`")
    return 0


def _record(g: Game, name: str, status: str, **extra) -> None:
    fpath = g.decomp / "functions.json"
    data = read_json(fpath, {"matched": {}, "wip": {}}) or {"matched": {}, "wip": {}}
    data.setdefault("matched", {})
    data.setdefault("wip", {})
    entry = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), **extra}
    if status == "matched":
        data["matched"][name] = entry
        data["wip"].pop(name, None)
    else:
        data["wip"][name] = entry
    write_json(fpath, data)


def match_function(g: Game, name: str, *, src: Path | None = None, version: str | None = None,
                   thumb: bool | None = None, show: bool = True) -> bool:
    proj = project(g)
    s = find_function(proj, name)
    src = src or src_path(g, s)
    if not src.exists():
        fail(f"{paths.rel(src)} não existe — rode `factory decomp scaffold {name}`")
    cfg = decomp_cfg(g)
    version = version or cfg.get("compiler", mwcc.DEFAULT_VERSION)
    use_thumb = (s.mode == "thumb") if thumb is None else thumb
    obj = g.decomp / "build" / "match" / s.module / f"{s.name}.o"
    try:
        mwcc.compile_c(src, obj, version=version, flags=cfg.get("flags"), thumb=False,
                       includes=[g.decomp / "include", g.decomp / "src"])
    except RuntimeError as exc:
        fail(str(exc))
    mine = mwcc.function_from_compiled(obj, s.name)
    orig = original_function(g, proj, s)
    ok, problems = compare_loose(mine, orig)
    if show:
        if ok:
            print(">>> MATCH <<<")
        else:
            print(mwcc.side_by_side(mine, orig, use_thumb))
            for p in problems[:25]:
                print(f"  ✘ {p}")
            print(f"--- NÃO bate · {version} · {'thumb' if use_thumb else 'arm'} · {paths.rel(src)}")
    _record(g, s.name, "matched" if ok else "wip", src=paths.rel(src), compiler=version)
    return ok


def cmd_match(args) -> int:
    g = load_game(args.game)
    ok = match_function(g, args.func, src=Path(args.src) if args.src else None, version=args.version,
                        thumb=True if args.thumb else None)
    return 0 if ok else 1


def cmd_sweep(args) -> int:
    """Which mwccarm version/flags reproduce a function whose C is believed correct (ffc-decomp approach)."""
    g = load_game(args.game)
    proj = project(g)
    s = find_function(proj, args.func)
    src = Path(args.src) if args.src else src_path(g, s)
    orig = original_function(g, proj, s)
    wins = []
    versions = args.versions or paths.mwcc_versions()
    import itertools
    base = [f for f in mwcc.DEFAULT_FLAGS if f not in ("-O4,p", "-enum", "int", "-inline", "on,noauto")]
    for ver in versions:
        for opt, enum, inl in itertools.product(*mwcc.SWEEP_VARIANTS.values()):
            flags = opt + enum + inl + base
            obj = g.decomp / "build" / "sweep" / f"{ver.replace('/', '_')}__{'_'.join(x.strip('-,') for x in opt + enum + inl)}.o"
            try:
                mwcc.compile_c(src, obj, version=ver, flags=flags, includes=[g.decomp / "include"], quiet=True)
                ok, _ = compare_loose(mwcc.function_from_compiled(obj, s.name), orig)
            except Exception:
                continue
            if ok:
                wins.append((ver, " ".join(opt + enum + inl)))
                info(f"MATCH: {ver} {' '.join(opt + enum + inl)}")
    if wins:
        print(table(wins, ["versão", "flags"]))
        if args.apply:
            cfg = decomp_cfg(g)
            cfg["compiler"] = wins[0][0]
            cfg["flags"] = wins[0][1].split() + base
            cfg["note"] = f"definido por sweep em {s.name} ({time.strftime('%Y-%m-%d')})"
            write_json(g.decomp / "decomp.json", cfg)
            info(f"decomp.json atualizado: {wins[0][0]} {wins[0][1]}")
    else:
        warn("nenhuma combinação bateu — o C ainda não é equivalente, ou a função usa recursos não cobertos")
    return 0 if wins else 1


def cmd_status(args) -> int:
    g = load_game(args.game)
    proj = project(g).load()
    data = read_json(g.decomp / "functions.json", {"matched": {}, "wip": {}}) or {}
    rows = []
    for m in proj.modules.values():
        funcs = [s for s in m.symbols if s.kind == "function"]
        if not funcs:
            continue
        done = [s for s in funcs if s.name in data.get("matched", {})]
        b_all = sum(s.size or 0 for s in funcs)
        b_done = sum(s.size or 0 for s in done)
        rows.append((m.name, len(funcs), len(done), f"{100 * b_done / max(b_all, 1):.1f}%"))
    rows.sort(key=lambda r: r[0])
    print(table(rows[: args.limit], ["módulo", "funções", "batidas", "% bytes"]))
    tot_f = sum(r[1] for r in rows)
    tot_d = sum(r[2] for r in rows)
    info(f"total: {tot_d}/{tot_f} funções com MATCH; WIP: {len(data.get('wip', {}))}")
    return 0


# ── full rebuild (ph-style ninja graph) ───────────────────────────────────────

def _esc(p) -> str:
    return str(p).replace("$", "$$").replace(" ", "$ ").replace(":", "$:")


def cmd_configure(args) -> int:
    g = load_game(args.game)
    d = g.decomp
    arm9_cfg = d / "config" / "arm9" / "config.yaml"
    if not arm9_cfg.exists():
        fail("rode `factory decomp init` primeiro")
    p = subprocess.run([str(paths.dsd()), "json", "delinks", "--config-path", str(arm9_cfg)], capture_output=True,
                       text=True, cwd=str(d), env=paths.tool_env())
    if p.returncode != 0:
        fail(f"dsd json delinks falhou: {p.stderr.strip()[-800:]}")
    dj = json.loads(p.stdout)
    cfg = decomp_cfg(g)
    ver = cfg.get("compiler", mwcc.DEFAULT_VERSION)
    cc = paths.mwcc_dir(ver) / "mwccarm.exe"
    ld = paths.mwcc_dir(ver) / "mwldarm.exe"
    flags = " ".join(cfg.get("flags", mwcc.DEFAULT_FLAGS))
    ld_flags = "-proc arm946e -nostdlib -interworking -nodead -map closure,unused -msgstyle gcc -m Entry"
    dsd = paths.dsd()
    files = dj.get("files", [])
    delink_files = sorted({f["delink_file"] for f in files if f.get("delink_file")})
    objects = [f["object_to_link"] for f in files if f.get("object_to_link")]
    wrap = paths.sjiswrap()
    cc_cmd = (f"\"{wrap}\" " if wrap else "") + f"\"{cc}\""
    lines = [
        "# gerado por `factory decomp configure` — não editar à mão",
        f"rule mwcc\n  command = {cc_cmd} {flags} -i include -i src -MD -c $in -o $out_dir\n  depfile = $out_base.d\n"
        f"  description = mwcc $in",
        f"rule delink\n  command = \"{dsd}\" delink --config-path {_esc(arm9_cfg)}\n  description = dsd delink",
        f"rule lcf\n  command = \"{dsd}\" lcf -c {_esc(arm9_cfg)}\n  description = dsd lcf",
        f"rule mwld\n  command = \"{ld}\" {ld_flags} @$objects_file $lcf_file -o $out\n  description = mwldarm",
        f"rule rom_config\n  command = \"{dsd}\" rom config --elf $in --config {_esc(arm9_cfg)}",
        f"rule rom_build\n  command = \"{dsd}\" rom build --config $in --rom $out",
        f"rule check_modules\n  command = \"{dsd}\" check modules --config-path {_esc(arm9_cfg)} --fail",
        "",
    ]
    configs = [str(x) for x in (d / "config").rglob("*.txt")]
    if delink_files:
        lines.append(f"build {' '.join(_esc(x) for x in delink_files)}: delink {' '.join(_esc(x) for x in configs)}")
        lines.append(f"build delink: phony {' '.join(_esc(x) for x in delink_files)}")
    lcf_file, obj_file = dj.get("arm9_lcf_file"), dj.get("arm9_objects_file")
    lines.append(f"build {_esc(lcf_file)} {_esc(obj_file)}: lcf {' '.join(_esc(x) for x in configs)}")
    src_objs = []
    for f in files:
        name = f.get("name") or f.get("file") or ""
        if f.get("complete") and name and (d / name).exists():
            obj = f["object_to_link"]
            src_objs.append(obj)
            lines.append(f"build {_esc(obj)}: mwcc {_esc(name)}\n  out_dir = {_esc(Path(obj).parent)}\n"
                         f"  out_base = {_esc(Path(obj).with_suffix(''))}")
    elf = d / "build" / "arm9.o"
    lines.append(f"build {_esc(elf)}: mwld {' '.join(_esc(o) for o in objects)} | {_esc(lcf_file)} {_esc(obj_file)}\n"
                 f"  objects_file = {_esc(obj_file)}\n  lcf_file = {_esc(lcf_file)}")
    rom_cfg = d / "build" / "rom_config.yaml"
    rom = d / "build" / f"{g.id}_rebuilt.nds"
    lines += [f"build {_esc(rom_cfg)}: rom_config {_esc(elf)}", f"build {_esc(rom)}: rom_build {_esc(rom_cfg)}",
              f"build check: check_modules {_esc(elf)}", f"build arm9: phony {_esc(elf)}",
              f"build rom: phony {_esc(rom)}", "default check", ""]
    (d / "build.ninja").write_text("\n".join(lines), encoding="utf-8")
    write_json(d / "build" / "delinks.json", dj)
    info(f"build.ninja gerado ({len(files)} arquivos, {len(src_objs)} com fonte C completa)")
    return 0


def cmd_build(args) -> int:
    g = load_game(args.game)
    if not (g.decomp / "build.ninja").exists():
        cmd_configure(args)
    target = args.target or "check"
    return run([paths.ninja(), "-C", g.decomp, target], check=False).returncode


def register(sub) -> None:
    p = sub.add_parser("decomp", help="Via A — descompilação correspondente (dsd + mwccarm)")
    s = p.add_subparsers(dest="decomp_cmd", metavar="<ação>")

    q = s.add_parser("init", help="dsd init + sig apply + delink + dis + objdiff.json")
    q.add_argument("--game")
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_init)

    q = s.add_parser("candidates", help="funções a atacar (folhas primeiro, menores primeiro)")
    q.add_argument("--game")
    q.add_argument("--module")
    q.add_argument("--leaf", action="store_true")
    q.add_argument("--max-size", type=int, default=0x400)
    q.add_argument("--limit", type=int, default=40)
    q.set_defaults(func=cmd_candidates)

    q = s.add_parser("scaffold", help="criar src/<módulo>/<func>.c com o assembly comentado")
    q.add_argument("func")
    q.add_argument("--game")
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_scaffold)

    q = s.add_parser("match", help="compilar 1 função e comparar byte a byte (>>> MATCH <<<)")
    q.add_argument("func")
    q.add_argument("--src")
    q.add_argument("--game")
    q.add_argument("--version", help="versão mwccarm (ex.: 2.0/sp2p4)")
    q.add_argument("--thumb", action="store_true")
    q.set_defaults(func=cmd_match)

    q = s.add_parser("sweep", help="testar todas as versões/flags do mwccarm numa função (descobrir o compilador)")
    q.add_argument("func")
    q.add_argument("--src")
    q.add_argument("--game")
    q.add_argument("--versions", nargs="+")
    q.add_argument("--apply", action="store_true", help="gravar a 1ª combinação vencedora em decomp.json")
    q.set_defaults(func=cmd_sweep)

    q = s.add_parser("status", help="progresso por módulo")
    q.add_argument("--game")
    q.add_argument("--limit", type=int, default=400)
    q.set_defaults(func=cmd_status)

    q = s.add_parser("configure", help="gerar build.ninja (delink → mwcc → lcf → mwldarm → rom)")
    q.add_argument("--game")
    q.set_defaults(func=cmd_configure)

    q = s.add_parser("build", help="ninja: check (padrão) | arm9 | rom")
    q.add_argument("target", nargs="?")
    q.add_argument("--game")
    q.set_defaults(func=cmd_build)
