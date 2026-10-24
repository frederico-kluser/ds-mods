"""Metrowerks CodeWarrior (mwccarm) driver: compile, byte-exact match against delinked objects, compiler sweep.

The matching approach follows the one proven in Yokimitsuro/khdays-decomp (CC0): compile ONE function,
take its own .text section (mwccarm emits one .text section per function), and compare it with the
original bytes from the dsd-delinked object, masking relocated words but requiring the relocation
targets (callee/global names) and kinds to agree.
"""
from __future__ import annotations

import itertools
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, Cs  # type: ignore
from elftools.elf.elffile import ELFFile  # type: ignore

from . import paths
from .util import fail, info, warn

# Codegen flags exactly as khdays-decomp's match.py/_run_mwcc.py (no -fp soft there); the last three
# only affect diagnostics. zeldaret/ph uses C++ variants (-lang=c++ -RTTI off -str noreuse ...).
DEFAULT_FLAGS = ["-O4,p", "-proc", "arm946e", "-interworking", "-lang", "c99", "-enum", "int", "-char", "signed",
                 "-inline", "on,noauto", "-Cpp_exceptions", "off", "-gccext,on", "-msgstyle", "gcc", "-w", "off"]
DEFAULT_VERSION = "2.0/sp2p4"   # = "Version 3.0 build 139"

# decomp.me compiler ids (frontend compilers.json): CW DS 1.2.x = "2.0 build N", 2.0.x = "3.0 build N"
DECOMPME_IDS = {
    "1.2/base": "mwcc_20_72", "1.2/sp2": "mwcc_20_79", "1.2/sp2p3": "mwcc_20_82", "1.2/sp3": "mwcc_20_84",
    "1.2/sp4": "mwcc_20_87", "2.0/base": "mwcc_30_114", "2.0/sp1": "mwcc_30_123", "2.0/sp1p2": "mwcc_30_126",
    "2.0/sp1p5": "mwcc_30_131", "2.0/sp1p6": "mwcc_30_133", "2.0/sp1p7": "mwcc_30_134", "2.0/sp2": "mwcc_30_136",
    "2.0/sp2p2": "mwcc_30_137", "2.0/sp2p3": "mwcc_30_138", "2.0/sp2p4": "mwcc_30_139",
}

# compiler sweep: flag variants tried for each mwccarm version
SWEEP_VARIANTS = {
    "opt": [["-O4,p"], ["-O4,s"], ["-O4"], ["-O2"]],
    "enum": [["-enum", "int"], ["-enum", "min"]],
    "inline": [["-inline", "on,noauto"], ["-inline", "auto"], ["-inline", "off"]],
}


def compile_c(src: Path, obj: Path, *, version: str = DEFAULT_VERSION, flags: list[str] | None = None,
              thumb: bool = False, includes: list[Path] | None = None, extra: list[str] | None = None,
              quiet: bool = False) -> Path:
    exe = paths.mwcc_dir(version) / "mwccarm.exe"
    argv = [str(exe), "-c", *(flags or DEFAULT_FLAGS)]
    if src.suffix.lower() in (".cpp", ".cp", ".cc"):
        argv = [a if a != "c99" else "c++" for a in argv]
    if thumb:
        argv.append("-thumb")
    for inc in includes or []:
        argv += ["-i", str(inc)]
    argv += list(extra or [])
    argv += ["-o", str(obj), str(src)]
    # non-ASCII sources (Japanese strings) go through sjiswrap, as zeldaret/ph does
    wrap = paths.sjiswrap()
    if wrap and any(b > 0x7F for b in src.read_bytes()):
        argv = [str(wrap), *argv]
    obj.parent.mkdir(parents=True, exist_ok=True)
    if obj.exists():
        obj.unlink()
    p = None
    # the FLEXlm licence check fails intermittently under parallel load (seen by khdays-decomp): retry
    for attempt in range(8):
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=paths.tool_env())
        if p.returncode == 0 and obj.exists():
            return obj
        if "licen" not in (p.stdout + p.stderr).lower():
            break
        time.sleep(0.25 * (attempt + 1))
    if not quiet and p is not None:
        print(p.stdout + p.stderr)
    raise RuntimeError(f"mwccarm {version} falhou em {src.name}")


@dataclass
class FuncBytes:
    data: bytearray
    relocs: dict[int, tuple[str, int]]     # offset -> (symbol, reloc type)


def _symtab(elf):
    return elf.get_section_by_name(".symtab")


def function_from_compiled(obj: Path, name: str | None) -> FuncBytes:
    """mwccarm puts every function in its own '.text' section; pick the one holding `name`."""
    with open(obj, "rb") as f:
        elf = ELFFile(f)
        st = _symtab(elf)
        idx = None
        if name:
            for sym in st.get_symbol_by_name(name) or []:
                if sym["st_info"]["type"] == "STT_FUNC" and isinstance(sym["st_shndx"], int):
                    idx = sym["st_shndx"]
                    break
            if idx is None:
                fail(f"função '{name}' não está no objeto compilado {obj.name}")
        else:
            texts = [i for i, s in enumerate(elf.iter_sections()) if s.name == ".text"]
            if len(texts) != 1:
                fail(f"{len(texts)} seções .text no objeto — informe o nome da função")
            idx = texts[0]
        data = bytearray(elf.get_section(idx).data())
        rel = {}
        for s in elf.iter_sections():
            if s.name in (".rel.text", ".rela.text") and s["sh_info"] == idx:
                for r in s.iter_relocations():
                    rel[r["r_offset"]] = (st.get_symbol(r["r_info_sym"]).name, r["r_info_type"])
        return FuncBytes(data, rel)


def function_from_delinked(obj: Path, name: str) -> tuple[FuncBytes, bool]:
    """Original bytes of `name` inside a dsd-delinked object (one .text per translation unit)."""
    with open(obj, "rb") as f:
        elf = ELFFile(f)
        st = _symtab(elf)
        target = None
        for sym in st.iter_symbols():
            if sym.name == name and sym["st_info"]["type"] == "STT_FUNC":
                target = sym
                break
        if target is None:
            fail(f"símbolo '{name}' não encontrado em {obj}")
        sec_idx = target["st_shndx"]
        sec = elf.get_section(sec_idx)
        val = target["st_value"]
        thumb = bool(val & 1)
        val &= ~1
        size = target["st_size"]
        text = sec.data()
        data = bytearray(text[val:val + size])
        rel = {}
        for s in elf.iter_sections():
            if s.name.startswith((".rel", ".rela")) and s["sh_info"] == sec_idx:
                for r in s.iter_relocations():
                    if val <= r["r_offset"] < val + size:
                        rel[r["r_offset"] - val] = (st.get_symbol(r["r_info_sym"]).name, r["r_info_type"])
        # mapping symbols ($t/$a) tell the mode when the function symbol has no Thumb bit
        for sym in st.iter_symbols():
            if sym["st_shndx"] == sec_idx and sym.name in ("$t", "$a") and sym["st_value"] == val:
                thumb = sym.name == "$t"
        return FuncBytes(data, rel), thumb


def compare(mine: FuncBytes, orig: FuncBytes) -> tuple[bool, list[str]]:
    problems = []
    if len(mine.data) != len(orig.data):
        problems.append(f"tamanho difere: seu código {len(mine.data)} bytes, original {len(orig.data)}")
    n = min(len(mine.data), len(orig.data))
    a, b = bytearray(mine.data[:n]), bytearray(orig.data[:n])
    for off in set(mine.relocs) | set(orig.relocs):
        if off in mine.relocs and off in orig.relocs:
            (sm, tm), (so, to) = mine.relocs[off], orig.relocs[off]
            if tm != to:
                problems.append(f"+0x{off:X}: tipo de relocação {tm} ≠ {to}")
            if sm != so and not (sm.startswith("@") or so.startswith("@")):
                problems.append(f"+0x{off:X}: alvo {sm} ≠ original {so}")
            for k in range(4):
                if off + k < n:
                    a[off + k] = b[off + k] = 0
        else:
            problems.append(f"+0x{off:X}: relocação só em {'seu código' if off in mine.relocs else 'original'}")
    if a != b:
        first = next(i for i in range(n) if a[i] != b[i])
        problems.append(f"bytes diferem a partir de +0x{first:X}")
    return not problems, problems


def side_by_side(mine: FuncBytes, orig: FuncBytes, thumb: bool, limit: int = 120) -> str:
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB if thumb else CS_MODE_ARM)
    A = [(i.address, i.bytes, f"{i.mnemonic} {i.op_str}") for i in md.disasm(bytes(mine.data), 0)]
    B = [(i.address, i.bytes, f"{i.mnemonic} {i.op_str}") for i in md.disasm(bytes(orig.data), 0)]
    lines = [f"{'off':>5}  {'SEU CÓDIGO':<36} {'ORIGINAL':<36}"]
    for k in range(min(max(len(A), len(B)), limit)):
        a = A[k] if k < len(A) else None
        b = B[k] if k < len(B) else None
        mark = "  " if a and b and (a[1] == b[1] or a[0] in mine.relocs) else "!!"
        sa = f"{a[2]}" + (f" [{mine.relocs[a[0]][0]}]" if a and a[0] in mine.relocs else "") if a else ""
        sb = f"{b[2]}" + (f" [{orig.relocs[b[0]][0]}]" if b and b[0] in orig.relocs else "") if b else ""
        off = a[0] if a else b[0]
        lines.append(f"{off:5X}{mark}{sa[:36]:<36} {sb[:36]:<36}")
    return "\n".join(lines)


def match(src: Path, target_obj: Path, func: str, *, version: str = DEFAULT_VERSION, flags: list[str] | None = None,
          thumb: bool | None = None, includes: list[Path] | None = None, show: bool = True) -> bool:
    orig, orig_thumb = function_from_delinked(target_obj, func)
    use_thumb = orig_thumb if thumb is None else thumb
    obj = src.with_suffix(src.suffix + ".o")
    compile_c(src, obj, version=version, flags=flags, thumb=use_thumb, includes=includes)
    mine = function_from_compiled(obj, func)
    ok, problems = compare(mine, orig)
    if show:
        if ok:
            print(">>> MATCH <<<")
        else:
            print(side_by_side(mine, orig, use_thumb))
            for p in problems[:20]:
                print(f"  ✘ {p}")
            print(f"--- NÃO bate ({len(problems)} problema(s)) · compilador {version} · thumb={use_thumb}")
    return ok


def sweep(src: Path, target_obj: Path, func: str, *, versions: list[str] | None = None,
          includes: list[Path] | None = None) -> list[tuple[str, list[str]]]:
    """Try every mwccarm version × flag variant; return the combos that reproduce the original bytes."""
    orig, thumb = function_from_delinked(target_obj, func)
    versions = versions or paths.mwcc_versions()
    base = [f for f in DEFAULT_FLAGS if f not in ("-O4,p",)]
    wins = []
    tmp = src.parent / ".sweep"
    for ver in versions:
        for opt, enum, inl in itertools.product(*SWEEP_VARIANTS.values()):
            flags = [x for x in base]
            for group in (enum, inl):
                key = group[0]
                if key in flags:
                    i = flags.index(key)
                    del flags[i:i + 2]
            flags = opt + enum + inl + flags
            obj = tmp / f"{ver.replace('/', '_')}_{'_'.join(x.strip('-,') for x in opt + enum + inl)}.o"
            try:
                compile_c(src, obj, version=ver, flags=flags, thumb=thumb, includes=includes, quiet=True)
                ok, _ = compare(function_from_compiled(obj, func), orig)
            except Exception:
                continue
            if ok:
                wins.append((ver, opt + enum + inl))
                info(f"MATCH com {ver} {' '.join(opt + enum + inl)}")
    if not wins:
        warn("nenhuma combinação reproduziu os bytes (o C candidato ainda não é equivalente?)")
    return wins
