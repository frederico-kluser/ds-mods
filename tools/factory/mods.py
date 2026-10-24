"""Via B — quick mods: build a modded ROM from games/<ID>/mods/<name>/mod.json.

mod.json
{
  "name": "infinite-hp",
  "description": "o que o mod faz e como verificar",
  "base": "base",                       # or "mod:<other>" to stack on another mod's work tree
  "steps": [
    {"type": "armips", "file": "patch.asm"},                       # micro-patches in ARM/THUMB asm
    {"type": "bytes", "patches": [{"module": "arm9", "addr": "0x02012345", "hex": "0000A0E3"}]},
    {"type": "files", "dir": "files"},                             # replace/add NitroFS files (mirrors files/)
    {"type": "python", "file": "patch.py"},                        # free-form: def apply(ctx) (see ModContext)
    {"type": "ncpatcher", "dir": "ncp"}                            # C/C++ hooks (NCPatcher project dir)
  ]
}

Modules are addressed as "arm9", "arm7", "itcm", "dtcm" or "ov<N>" (ARM9 overlay N). All binaries in the
work tree are the DECOMPRESSED ones produced by `dsd rom extract`; `dsd rom build` recompresses as the
original was. armips sources receive the paths as -strequ defines: ARM9_BIN, ARM7_BIN, ITCM_BIN, DTCM_BIN,
OV<N>_BIN (e.g. `.open ARM9_BIN, 0x02000000`) plus FILES_DIR.

Outputs (games/<ID>/build/mods/<name>/): <ID>_<name>.nds, build.json, hotpatch.json, build.log, work/.
hotpatch.json lists every byte range whose RAM-resident code/data differs from the base ROM, so a run
that starts from a savestate made on the base ROM can re-apply the mod in RAM (see emu.py / runner).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import time
from pathlib import Path

import yaml  # type: ignore

from . import paths
from .game import Game, load_game
from .util import fail, info, parse_int, read_json, run, sha256_file, table, warn, write_json


# ── work tree (a dsd extract directory) ──────────────────────────────────────

class WorkTree:
    """A writable copy of games/<ID>/extract used to build one mod."""

    def __init__(self, root: Path):
        self.root = root
        self.config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))

    def _yaml(self, rel_path: str) -> dict:
        return yaml.safe_load((self.root / rel_path).read_text(encoding="utf-8")) or {}

    def modules(self) -> dict[str, dict]:
        """name -> {bin: Path, base: int, kind}"""
        out: dict[str, dict] = {}
        c = self.config
        out["arm9"] = {"bin": self.root / c["arm9_bin"], "base": self._yaml(c["arm9_config"])["base_address"], "kind": "arm9"}
        out["arm7"] = {"bin": self.root / c["arm7_bin"], "base": self._yaml(c["arm7_config"])["base_address"], "kind": "arm7"}
        for name in ("itcm", "dtcm"):
            entry = c.get(name)
            if entry and (self.root / entry["bin"]).exists():
                out[name] = {"bin": self.root / entry["bin"], "base": self._yaml(entry["config"])["base_address"],
                             "kind": name}
        ovs = c.get("arm9_overlays")
        if ovs:
            table_yaml = self._yaml(ovs) if isinstance(ovs, str) else ovs
            ov_dir = (self.root / ovs).parent if isinstance(ovs, str) else self.root
            for ov in _overlay_entries(table_yaml):
                oid = int(ov.get("id", ov.get("overlay_id", -1)))
                file_name = ov.get("file_name") or ov.get("bin") or f"ov{oid:03d}.bin"
                bin_path = ov_dir / file_name
                if not bin_path.exists():
                    cands = list(ov_dir.glob(f"*{oid:03d}*.bin")) + list(ov_dir.glob(f"ov{oid}.bin"))
                    bin_path = cands[0] if cands else bin_path
                base = ov.get("base_address", ov.get("ram_address", ov.get("address")))
                if base is None and (bin_path.with_suffix(".yaml")).exists():
                    base = self._yaml(str(bin_path.with_suffix(".yaml").relative_to(self.root))).get("base_address")
                out[f"ov{oid}"] = {"bin": bin_path, "base": int(base) if base is not None else None, "kind": "overlay",
                                   "id": oid}
        return out

    @property
    def files_dir(self) -> Path:
        return self.root / self.config.get("files_dir", "files/")


def _overlay_entries(table_yaml) -> list[dict]:
    if isinstance(table_yaml, list):
        return table_yaml
    for key in ("overlays", "table", "entries"):
        if isinstance(table_yaml.get(key), list):
            return table_yaml[key]
    return []


class ModContext:
    """Passed to python steps: def apply(ctx): ..."""

    def __init__(self, game: Game, work: WorkTree, mod_dir: Path):
        self.game, self.work, self.mod_dir = game, work, mod_dir
        self._mods = work.modules()

    def module_path(self, module: str) -> Path:
        if module not in self._mods:
            fail(f"módulo '{module}' não existe (disponíveis: {', '.join(self._mods)})")
        return self._mods[module]["bin"]

    def _locate(self, module: str, addr: int) -> tuple[Path, int]:
        m = self._mods.get(module) or fail(f"módulo '{module}' desconhecido")
        off = addr - m["base"]
        size = m["bin"].stat().st_size
        if not 0 <= off < size:
            fail(f"endereço 0x{addr:08X} fora de {module} (0x{m['base']:08X}..0x{m['base'] + size:08X})")
        return m["bin"], off

    def read(self, module: str, addr: int | str, size: int) -> bytes:
        p, off = self._locate(module, parse_int(addr))
        with open(p, "rb") as f:
            f.seek(off)
            return f.read(size)

    def write(self, module: str, addr: int | str, data: bytes, *, expect: bytes | None = None) -> None:
        p, off = self._locate(module, parse_int(addr))
        with open(p, "r+b") as f:
            f.seek(off)
            if expect is not None:
                cur = f.read(len(expect))
                if cur != expect:
                    fail(f"{module}@0x{parse_int(addr):08X}: esperava {expect.hex()} e achou {cur.hex()}")
                f.seek(off)
            f.write(data)

    def file(self, nitro_path: str) -> Path:
        return self.work.files_dir / nitro_path.lstrip("/")

    def narc(self, nitro_path: str):
        import ndspy.narc  # type: ignore
        return ndspy.narc.NARC(self.file(nitro_path).read_bytes())

    def save_narc(self, nitro_path: str, narc) -> None:
        self.file(nitro_path).write_bytes(narc.save())


# ── build ─────────────────────────────────────────────────────────────────────

def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _defines(work: WorkTree) -> list[str]:
    args = []
    for name, m in work.modules().items():
        args += ["-strequ", f"{name.upper()}_BIN", str(m["bin"]).replace("\\", "/")]
    args += ["-strequ", "FILES_DIR", str(work.files_dir).replace("\\", "/")]
    return args


def step_armips(g: Game, work: WorkTree, mod_dir: Path, step: dict, log) -> None:
    src = (mod_dir / step["file"]).resolve()
    sym = work.root.parent / f"{src.stem}.sym"
    run([paths.armips(), src, *_defines(work), "-sym", sym, "-root", mod_dir], quiet=True)
    log(f"armips {step['file']} ok (símbolos em {paths.rel(sym)})")


def step_bytes(g: Game, work: WorkTree, mod_dir: Path, step: dict, log) -> None:
    ctx = ModContext(g, work, mod_dir)
    for p in step.get("patches", []):
        data = bytes.fromhex(p["hex"].replace(" ", ""))
        expect = bytes.fromhex(p["expect"].replace(" ", "")) if p.get("expect") else None
        ctx.write(p.get("module", "arm9"), p["addr"], data, expect=expect)
        log(f"bytes {p.get('module', 'arm9')}@{p['addr']} ← {data.hex()}")


def step_files(g: Game, work: WorkTree, mod_dir: Path, step: dict, log) -> None:
    src = mod_dir / step.get("dir", "files")
    n = 0
    for f in src.rglob("*"):
        if f.is_file():
            rel_path = f.relative_to(src)
            dst = work.files_dir / rel_path
            if not dst.exists() and not step.get("allow_new", False):
                fail(f"{rel_path} não existe no NitroFS original (use \"allow_new\": true para adicionar)")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            n += 1
    log(f"files: {n} arquivo(s) de {paths.rel(src)}")


def step_python(g: Game, work: WorkTree, mod_dir: Path, step: dict, log) -> None:
    src = mod_dir / step["file"]
    spec = importlib.util.spec_from_file_location(f"mod_{mod_dir.name}_{src.stem}", src)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    if not hasattr(module, "apply"):
        fail(f"{paths.rel(src)} precisa definir apply(ctx)")
    module.apply(ModContext(g, work, mod_dir))
    log(f"python {step['file']} ok")


def step_ncpatcher(g: Game, work: WorkTree, mod_dir: Path, step: dict, log) -> None:
    """NCPatcher works on an nds-extract (Fireflower) tree; bridge dsd tree ⇄ that layout."""
    proj = mod_dir / step.get("dir", "ncp")
    if not (proj / "ncpatcher.json").exists():
        fail(f"{paths.rel(proj)}/ncpatcher.json não existe (crie com `factory mod new <nome> --template ncpatcher`)")
    rom_in = work.root.parent / "pre_ncp.nds"
    run([paths.dsd(), "rom", "build", "--config", work.root / "config.yaml", "--rom", rom_in], quiet=True)
    fs = proj / "fs-data"
    if fs.exists():
        shutil.rmtree(fs)
    run([paths.fireflower_tool("nds-extract"), rom_in, fs], cwd=proj, quiet=True)
    run([paths.ncpatcher()], cwd=proj)
    rom_out = work.root.parent / "post_ncp.nds"
    rules = next(iter(fs.glob("buildrules.txt")), None) or next(iter(proj.glob("buildrules.txt")), None)
    if rules is None:
        fail("buildrules.txt não encontrado após nds-extract")
    run([paths.fireflower_tool("nds-build"), rules, rom_out], cwd=rules.parent, quiet=True)
    # re-extract into the work tree so later steps and the final dsd build see NCPatcher's result
    fresh = work.root.parent / "work_ncp"
    if fresh.exists():
        shutil.rmtree(fresh)
    run([paths.dsd(), "rom", "extract", "--rom", rom_out, "--output-path", fresh], quiet=True)
    shutil.rmtree(work.root)
    fresh.rename(work.root)
    work.__init__(work.root)
    log(f"ncpatcher {paths.rel(proj)} ok")


STEPS = {"armips": step_armips, "bytes": step_bytes, "files": step_files, "python": step_python,
         "ncpatcher": step_ncpatcher}


def compute_hotpatch(base_tree: WorkTree, mod_tree: WorkTree, *, gap: int = 8) -> list[dict]:
    """Byte ranges that differ in RAM-resident modules (arm9/itcm/dtcm/overlays) between base and mod."""
    base_mods, mod_mods = base_tree.modules(), mod_tree.modules()
    hunks: list[dict] = []
    for name, mm in mod_mods.items():
        if name == "arm7" or name not in base_mods or mm.get("base") is None:
            continue
        a = base_mods[name]["bin"].read_bytes()
        b = mm["bin"].read_bytes()
        n = min(len(a), len(b))
        i = 0
        while i < n:
            if a[i] == b[i]:
                i += 1
                continue
            j = i
            last = i
            while j < n and (j - last) <= gap:
                if a[j] != b[j]:
                    last = j
                j += 1
            end = last + 1
            hunk = {"module": name, "addr": f"0x{mm['base'] + i:08X}", "orig": a[i:end].hex().upper(),
                    "new": b[i:end].hex().upper()}
            if mm["kind"] == "overlay":
                # overlays share RAM: only patch when THIS overlay is resident (guard on its first bytes)
                hunk["guard"] = {"addr": f"0x{mm['base']:08X}", "bytes": a[:16].hex().upper()} if i >= 16 else None
                if hunk["guard"] is None:
                    hunk.pop("guard")
                hunk["optional"] = True
            hunks.append(hunk)
            i = end
        if len(b) > len(a):
            hunks.append({"module": name, "addr": f"0x{mm['base'] + len(a):08X}", "orig": "",
                          "new": b[len(a):].hex().upper(), "appended": True, "optional": True})
    return [h for h in hunks if h["orig"]]  # appended code cannot be hotpatched safely (heap may live there)


def build_mod(g: Game, name: str) -> Path:
    mod_dir = g.mods / name
    spec = read_json(mod_dir / "mod.json")
    if not spec:
        fail(f"{paths.rel(mod_dir)}/mod.json não existe")
    if not g.extract_config.exists():
        fail("ROM base ainda não extraída — rode `factory extract`")
    out = g.build / "mods" / name
    out.mkdir(parents=True, exist_ok=True)
    log_lines: list[str] = []

    def log(msg: str) -> None:
        log_lines.append(msg)
        info(f"[{name}] {msg}")

    base = spec.get("base", "base")
    if base.startswith("mod:"):
        parent_out = g.build / "mods" / base[4:] / "work"
        if not parent_out.exists():
            fail(f"mod base '{base}' ainda não construído")
        _copy_tree(parent_out, out / "work")
    else:
        _copy_tree(g.extract, out / "work")
    work = WorkTree(out / "work")
    for i, step in enumerate(spec.get("steps", []), 1):
        fn = STEPS.get(step.get("type"))
        if not fn:
            fail(f"passo {i}: tipo desconhecido '{step.get('type')}' (tipos: {', '.join(STEPS)})")
        fn(g, work, mod_dir, step, log)
    rom_out = out / f"{g.id}_{name}.nds"
    run([paths.dsd(), "rom", "build", "--config", work.root / "config.yaml", "--rom", rom_out], quiet=True)
    hot = compute_hotpatch(WorkTree(g.extract), work)
    write_json(out / "hotpatch.json", {"mod": name, "patches": hot,
                                       "note": "aplicado pelo runner após carregar savestates do jogo base"})
    manifest = {"mod": name, "game": g.id, "rom": paths.rel(rom_out), "sha1_hint": sha256_file(rom_out)[:16],
                "built_at": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": len(spec.get("steps", [])),
                "hotpatch_hunks": len(hot), "description": spec.get("description", "")}
    write_json(out / "build.json", manifest)
    (out / "build.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    log(f"ROM: {paths.rel(rom_out)}  ({len(hot)} hunk(s) de hotpatch)")
    return rom_out


TEMPLATES = {
    "armips": {
        "mod.json": {"name": "{name}", "description": "descreva o efeito esperado e como verificar",
                     "steps": [{"type": "armips", "file": "patch.asm"}]},
        "patch.asm": ("; {name} — patch armips. Endereços são de RAM (veja config/ do dsd ou `factory dis`).\n"
                      "; ARM9_BIN/ITCM_BIN/DTCM_BIN/OV<N>_BIN chegam via -strequ (binários DESCOMPRIMIDOS).\n"
                      ".nds\n.open ARM9_BIN, 0x02000000\n\n; .org 0x02012345\n;     .arm   ; ou .thumb\n"
                      ";     mov r0, #1\n\n.close\n"),
    },
    "data": {
        "mod.json": {"name": "{name}", "description": "troca de arquivos/dados",
                     "steps": [{"type": "files", "dir": "files"}, {"type": "python", "file": "patch.py"}]},
        "patch.py": ('"""Patch de dados. ctx: read/write(module, addr, bytes), file(path), narc(path)."""\n\n\n'
                     "def apply(ctx):\n    # ex.: ctx.write('arm9', 0x02012345, bytes.fromhex('0000A0E3'))\n    pass\n"),
        "files/.keep": "",
    },
    "ncpatcher": {
        "mod.json": {"name": "{name}", "description": "hooks C/C++ via NCPatcher",
                     "steps": [{"type": "ncpatcher", "dir": "ncp"}]},
        "ncp/ncpatcher.json": {
            "$arm_flags": "-masm-syntax-unified -mno-unaligned-access -mfloat-abi=soft -mabi=aapcs",
            "$c_flags": "-Os -fomit-frame-pointer -ffast-math -fno-builtin -nostdlib -nodefaultlibs -nostartfiles"
                        " -DSDK_GCC -DSDK_FINALROM",
            "$cpp_flags": "-fno-rtti -fno-exceptions -std=c++20",
            "$asm_flags": "-Os -x assembler-with-cpp -fomit-frame-pointer",
            "$ld_flags": "-lgcc -lc -lstdc++ --use-blx",
            "backup": "backup", "filesystem": "fs-data", "toolchain": "{toolchain}",
            "arm7": {}, "arm9": {"target": "arm9.json", "build": "build"},
            "pre-build": [], "post-build": [], "thread-count": 0},
        "ncp/arm9.json": {
            "$arm_flags": "-march=armv5te -mtune=arm946e-s $${arm_flags}",
            "$c_flags": "${arm_flags} $${c_flags} -DSDK_ARM9",
            "$cpp_flags": "${c_flags} $${cpp_flags}", "$asm_flags": "${arm_flags} $${asm_flags}",
            "$ld_flags": "$${ld_flags}", "c_flags": "${c_flags}", "cpp_flags": "${cpp_flags}",
            "asm_flags": "${asm_flags}", "ld_flags": "${ld_flags}",
            "includes": [["include", False], ["source", False], ["{ncp_include}", False]],
            "regions": [{"dest": "main", "compress": False, "sources": [["source", True]]}],
            "arenaLo": "0x00000000", "symbols": "symbols9.x"},
        "ncp/symbols9.x": "/* símbolos do jogo para linkar: Nome = 0x02xxxxxx; */\n",
        "ncp/source/main.cpp": ('#include "ncp.h"\n\n// Exemplo (ajuste o endereço; hook só a partir de código ARM):\n'
                                "// ncp_hook(0x02012345)\n// void MeuHook() { }\n"),
        "ncp/include/.keep": "",
        "README.md": ("# {name} (NCPatcher)\n\n1. Ache `arenaLo`: o endereço do valor carregado pelo primeiro LDR de "
                      "`OS_GetInitArenaLo` (use `factory dis`/Ghidra) e coloque em `ncp/arm9.json`.\n"
                      "2. Escreva hooks em `ncp/source/`. Hooks em THUMB sobrescrevem 4–8 bytes: audite com "
                      "`factory dis --addr` antes.\n3. `factory mod build {name}`.\n"),
    },
}


def cmd_mod(args) -> int:
    g = load_game(args.game)
    if args.action == "list":
        rows = []
        for d in sorted(g.mods.iterdir()) if g.mods.exists() else []:
            spec = read_json(d / "mod.json") or {}
            built = read_json(g.build / "mods" / d.name / "build.json") or {}
            rows.append((d.name, len(spec.get("steps", [])), built.get("built_at", "-"),
                         (spec.get("description") or "")[:60]))
        print(table(rows, ["mod", "passos", "construído", "descrição"]) if rows else "nenhum mod")
        return 0
    if not args.name:
        fail("informe o nome do mod")
    if args.action == "new":
        d = g.mods / args.name
        if d.exists() and any(d.iterdir()) and not args.force:
            fail(f"{paths.rel(d)} já existe")
        tpl = TEMPLATES[args.template]
        ncp_inc = ""
        try:
            ncp_inc = str(paths.ncpatcher().parent).replace("\\", "/")
            toolchain = str(paths.arm_gnu_bin() / "arm-none-eabi-").replace("\\", "/")
        except paths.ToolMissing:
            toolchain = "arm-none-eabi-"
        for rel_name, content in tpl.items():
            p = d / rel_name
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, dict):
                text = json.dumps(content, indent=2, ensure_ascii=False)
            else:
                text = content
            text = text.replace("{name}", args.name).replace("{toolchain}", toolchain).replace("{ncp_include}", ncp_inc)
            p.write_text(text + ("\n" if text and not text.endswith("\n") else ""), encoding="utf-8")
        info(f"mod criado: {paths.rel(d)} (template {args.template})")
        return 0
    if args.action == "build":
        build_mod(g, args.name)
        return 0
    if args.action == "hotpatch":
        print(json.dumps(read_json(g.build / "mods" / args.name / "hotpatch.json"), indent=2))
        return 0
    fail(f"ação desconhecida {args.action}")


def register(sub) -> None:
    p = sub.add_parser("mod", help="mods (Via B): list | new | build | hotpatch")
    p.add_argument("action", choices=["list", "new", "build", "hotpatch"])
    p.add_argument("name", nargs="?")
    p.add_argument("--game")
    p.add_argument("--template", choices=sorted(TEMPLATES), default="armips")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_mod)
