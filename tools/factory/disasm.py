"""Static RE helpers on the extracted (decompressed) binaries: dis, hookcheck, xrefs, find, sym.

Works with or without a dsd project: when games/<ID>/decomp/config exists, symbols/modes/relocations
from dsd are used to name things; otherwise addresses are resolved against the extracted modules and
xrefs come from a raw BL/BLX + literal-pointer scan.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, Cs  # type: ignore

from . import paths
from .dsdproj import Project, find_project
from .game import Game, load_game
from .util import fail, info, parse_int, table, warn


@dataclass
class Mod:
    name: str        # arm9 | itcm | dtcm | ovN | arm7
    dsd_name: str    # main | itcm | dtcm | ovNNN | arm7
    base: int
    data: bytes
    path: Path

    def contains(self, addr: int) -> bool:
        return self.base <= addr < self.base + len(self.data)


def modules(g: Game) -> list[Mod]:
    from .mods import WorkTree

    if not g.extract_config.exists():
        fail("ROM ainda não extraída — rode `factory extract`")
    out = []
    for name, m in WorkTree(g.extract).modules().items():
        if m.get("base") is None:
            continue
        dsd_name = {"arm9": "main"}.get(name, name)
        if m["kind"] == "overlay":
            dsd_name = f"ov{m['id']:03d}"
        out.append(Mod(name=name, dsd_name=dsd_name, base=int(m["base"]), data=m["bin"].read_bytes(), path=m["bin"]))
    return out


def pick_module(mods: list[Mod], addr: int, want: str | None) -> Mod:
    if want:
        w = want.lower()
        norm = {w, w.replace("ov0", "ov").lstrip("0")}
        for m in mods:
            if m.name.lower() in norm or m.dsd_name.lower() == w or (w.isdigit() and m.name == f"ov{int(w)}"):
                return m
        fail(f"módulo '{want}' não existe (ex.: arm9, itcm, dtcm, ov12)")
    hits = [m for m in mods if m.contains(addr) and m.name != "arm7"]
    if not hits:
        fail(f"0x{addr:08X} não pertence a nenhum módulo ARM9 extraído")
    if len(hits) > 1:
        names = ", ".join(m.name for m in hits)
        fail(f"0x{addr:08X} existe em vários overlays ({names}) — informe --module ovN")
    return hits[0]


def resolve_target(g: Game, target: str, module: str | None, proj: Project | None) -> tuple[int, str | None, str | None, int | None]:
    """→ (addr, module_hint, mode, size) from '0x..' or a symbol name."""
    if re.fullmatch(r"(0x)?[0-9a-fA-F]{6,8}", target):
        a = int(target, 16)
        mode = "thumb" if a & 1 else None
        return a & ~1, module, mode, None
    if proj is None:
        fail(f"'{target}' não é endereço e não há projeto dsd (rode `factory decomp init`) para resolver nomes")
    syms = proj.find(target)
    if not syms:
        fail(f"símbolo '{target}' não encontrado")
    if len(syms) > 1 and module is None:
        warn("vários símbolos: " + ", ".join(f"{s.name}@{s.module}:0x{s.addr:08X}" for s in syms[:8]) + " (usando o 1º)")
    s = syms[0]
    mod = module or ({"main": "arm9"}.get(s.module, s.module))
    if mod.startswith("ov") and mod[2:].isdigit():
        mod = f"ov{int(mod[2:])}"
    return s.addr, mod, s.mode, s.size


def disasm(data: bytes, addr: int, mode: str, count: int | None = None, size: int | None = None):
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB if mode == "thumb" else CS_MODE_ARM)
    md.detail = False
    return list(md.disasm(data[: size if size else len(data)], addr, count=count or 0))


def _literal(mod: Mod, insn, mode: str) -> int | None:
    m = re.match(r"(\w+), \[pc, #(-?0x[0-9a-f]+|-?\d+)\]", insn.op_str)
    if not insn.mnemonic.startswith("ldr") or not m:
        return None
    off = int(m.group(2), 0)
    pc = (insn.address + 4) & ~3 if mode == "thumb" else insn.address + 8
    target = pc + off
    i = target - mod.base
    if 0 <= i <= len(mod.data) - 4:
        return struct.unpack_from("<I", mod.data, i)[0]
    return None


def format_listing(g: Game, mod: Mod, addr: int, mode: str, *, count: int | None, size: int | None,
                   proj: Project | None, mark: tuple[int, int] | None = None) -> list[str]:
    off = addr - mod.base
    insns = disasm(mod.data[off:], addr, mode, count=count, size=size)
    lines = []
    for ins in insns:
        note = []
        lit = _literal(mod, ins, mode)
        if lit is not None:
            nm = proj.name_of(lit) if proj else None
            note.append(f"=0x{lit:08X}" + (f" <{nm}>" if nm else ""))
        if ins.mnemonic in ("bl", "blx", "b") or ins.mnemonic.startswith("b") and ins.op_str.startswith("#0x"):
            mt = re.match(r"#(0x[0-9a-f]+)", ins.op_str)
            if mt and proj:
                nm = proj.name_of(int(mt.group(1), 16))
                if nm:
                    note.append(f"<{nm}>")
        if proj:
            for r in proj.xrefs_from(ins.address, ins.address + ins.size, mod.dsd_name):
                nm = proj.name_of(r.dst, r.target_module)
                note.append(f"reloc {r.kind}→0x{r.dst:08X}{(' <' + nm + '>') if nm else ''} [{r.target_module}]")
        flag = ">>" if mark and mark[0] <= ins.address < mark[1] else "  "
        lines.append(f"{flag} {ins.address:08X}  {ins.bytes.hex():<9} {ins.mnemonic:<7} {ins.op_str:<28}"
                     + (" ; " + " ".join(note) if note else ""))
    return lines


def cmd_dis(args) -> int:
    g = load_game(args.game)
    proj = find_project(g.dir)
    mods = modules(g)
    addr, mhint, mode, size = resolve_target(g, args.target, args.module, proj)
    mod = pick_module(mods, addr, mhint)
    mode = "thumb" if args.thumb else ("arm" if args.arm else (mode or "arm"))
    if size is None and proj:
        syms = [s for s in proj.at(addr) if s.kind == "function"]
        if syms:
            size = syms[0].size
            mode = args.thumb and "thumb" or syms[0].mode or mode
    count = None if size else args.count
    head = f"{mod.name} ({mod.dsd_name}) @0x{addr:08X} modo={mode}" + (f" tamanho=0x{size:X}" if size else "")
    if proj:
        own = proj.at(addr, mod.dsd_name)
        if own:
            head += f"  [{own[0].name}]"
    print(head)
    print("\n".join(format_listing(g, mod, addr, mode, count=count, size=size, proj=proj)))
    return 0


HOOK_BYTES = {("arm", "jump"): 4, ("arm", "call"): 4, ("arm", "hook"): 4, ("thumb", "jump"): 8, ("thumb", "call"): 4}


def hookcheck(g: Game, addr: int, mode: str, kind: str, module: str | None) -> dict:
    proj = find_project(g.dir)
    mods = modules(g)
    mod = pick_module(mods, addr, module)
    if (mode, kind) not in HOOK_BYTES:
        return {"verdict": "PROIBIDO", "module": mod.name, "addr": f"0x{addr:08X}", "mode": mode, "kind": kind,
                "bytes_overwritten": 0, "reasons": ["ncp_hook só pode partir de código ARM (NCPatcher)"],
                "advice": ["use ncp_tcall (4 bytes) / ncp_tjump (8 bytes) ou escolha um endereço ARM"], "listing": []}
    n = HOOK_BYTES[(mode, kind)]
    reasons, advice = [], []
    align = 2 if mode == "thumb" else 4
    if addr % align:
        reasons.append(f"endereço desalinhado para {mode}")
    # function window: from the containing symbol, else ±64 bytes
    start, end = addr - 64, addr + 64
    if proj:
        own = [s for s in proj.at(addr, mod.dsd_name) if s.kind == "function" and s.size]
        if own:
            start, end = own[0].addr, own[0].addr + own[0].size
            if own[0].mode and own[0].mode != mode:
                reasons.append(f"o símbolo {own[0].name} é {own[0].mode}, não {mode}")
    start = max(start, mod.base)
    end = min(end, mod.base + len(mod.data))
    insns = disasm(mod.data[start - mod.base:end - mod.base], start, mode)
    window = [i for i in insns if addr <= i.address < addr + n or (i.address < addr + n <= i.address + i.size - 1)]
    covered_end = max((i.address + i.size for i in window), default=addr + n)
    if covered_end > addr + n:
        reasons.append(f"a janela de {n} bytes corta uma instrução ao meio (termina em 0x{covered_end:08X}); "
                       "ex.: BL Thumb de 32 bits")
    for i in window:
        if "pc" in i.op_str and i.mnemonic.startswith(("ldr", "add", "adr")):
            reasons.append(f"0x{i.address:08X} {i.mnemonic} {i.op_str} é relativo ao PC — refaça o cálculo no hook")
        if i.mnemonic.startswith(("b", "cb")) and i.mnemonic not in ("bic", "bics"):
            reasons.append(f"0x{i.address:08X} {i.mnemonic} {i.op_str} é desvio — reexecute com cuidado no hook")
    for i in insns:
        mt = re.match(r"#(0x[0-9a-f]+)", i.op_str)
        if i.mnemonic.startswith("b") and mt and i.mnemonic not in ("bl", "blx"):
            tgt = int(mt.group(1), 16)
            if addr < tgt < addr + n:
                reasons.append(f"0x{i.address:08X} desvia para 0x{tgt:08X}, DENTRO dos bytes sobrescritos")
    if proj:
        for r in proj.xrefs_to(addr, mod.dsd_name):
            if r.kind not in ("arm_call", "thumb_call", "arm_call_thumb", "thumb_call_arm", "load"):
                reasons.append(f"referência {r.kind} de 0x{r.src:08X} aponta exatamente para o hook")
    verdict = "SEGURO" if not reasons else ("ATENÇÃO" if all("PC" in x or "desvio" in x for x in reasons) else "PERIGOSO")
    if mode == "arm" and not reasons:
        advice.append("hook a partir de ARM sobrescreve 1 instrução: ncp_hook/ncp_call/ncp_jump são seguros aqui")
    if window:
        advice.append("instruções sobrescritas (reexecute-as no seu código se usar jump/call): " +
                      "; ".join(f"{i.mnemonic} {i.op_str}" for i in window))
    listing = format_listing(g, mod, start, mode, count=None, size=end - start, proj=proj, mark=(addr, addr + n))
    return {"verdict": verdict, "module": mod.name, "addr": f"0x{addr:08X}", "mode": mode, "kind": kind,
            "bytes_overwritten": n, "reasons": reasons, "advice": advice, "listing": listing}


def cmd_hookcheck(args) -> int:
    g = load_game(args.game)
    proj = find_project(g.dir)
    addr, mhint, mode, _ = resolve_target(g, args.target, args.module, proj)
    mode = "thumb" if args.thumb else ("arm" if args.arm else (mode or "arm"))
    res = hookcheck(g, addr, mode, args.kind, mhint)
    print("\n".join(res.get("listing", [])))
    print()
    print(f"VEREDITO: {res['verdict']} — {res['kind']} a partir de {res['mode'].upper()} em {res['addr']} "
          f"({res.get('module')}) sobrescreve {res.get('bytes_overwritten')} bytes")
    for r in res["reasons"]:
        print(f"  ! {r}")
    for a in res["advice"]:
        print(f"  → {a}")
    return 0 if res["verdict"] == "SEGURO" else 2


def _scan_bl(mod: Mod, target: int) -> list[tuple[int, str]]:
    """Raw xref scan: ARM BL/BLX, Thumb BL/BLX pairs and literal words equal to target (±1)."""
    hits = []
    d = mod.data
    for off in range(0, len(d) - 3, 4):
        w = struct.unpack_from("<I", d, off)[0]
        pc = mod.base + off
        if (w & 0x0F000000) == 0x0B000000 and (w >> 28) != 0xF:      # BL<cond>
            imm = w & 0xFFFFFF
            imm = imm - (1 << 24) if imm & 0x800000 else imm
            if pc + 8 + imm * 4 == target:
                hits.append((pc, "arm bl"))
        elif (w >> 25) == 0x7D:                                       # BLX imm (to thumb)
            imm = w & 0xFFFFFF
            imm = imm - (1 << 24) if imm & 0x800000 else imm
            h = (w >> 24) & 1
            if pc + 8 + imm * 4 + h * 2 == target:
                hits.append((pc, "arm blx→thumb"))
        if w in (target, target | 1):
            hits.append((pc, "ponteiro (literal/tabela)"))
    for off in range(0, len(d) - 3, 2):
        hi, lo = struct.unpack_from("<HH", d, off)
        if (hi & 0xF800) == 0xF000 and (lo & 0xE800) in (0xF800, 0xE800):
            offset = ((hi & 0x7FF) << 12) | ((lo & 0x7FF) << 1)
            if offset & 0x400000:
                offset -= 1 << 23
            pc = mod.base + off + 4
            dest = pc + offset
            if (lo & 0xF800) == 0xE800:   # BLX → ARM
                dest &= ~3
            if dest == target:
                hits.append((mod.base + off, "thumb bl" if (lo & 0xF800) == 0xF800 else "thumb blx→arm"))
    return hits


def cmd_xrefs(args) -> int:
    g = load_game(args.game)
    proj = find_project(g.dir)
    addr, mhint, _, _ = resolve_target(g, args.target, args.module, proj)
    rows = []
    if proj and not args.raw:
        for r in proj.xrefs_to(addr):
            owner = proj.at(r.src, r.module)
            rows.append((r.module, f"0x{r.src:08X}", r.kind, owner[0].name if owner else "?"))
    else:
        for m in modules(g):
            if m.name == "arm7":
                continue
            for pc, kind in _scan_bl(m, addr):
                rows.append((m.name, f"0x{pc:08X}", kind, ""))
    print(table(rows, ["módulo", "de", "tipo", "função"]) if rows else "nenhuma referência encontrada")
    if not proj:
        info("(scan bruto: sem projeto dsd; chamadas via registrador/tabelas calculadas não aparecem)")
    return 0


def cmd_find(args) -> int:
    g = load_game(args.game)
    needles: list[tuple[str, bytes]] = []
    if args.hex:
        needles.append(("hex", bytes.fromhex(args.hex.replace(" ", ""))))
    if args.string:
        needles.append(("ascii", args.string.encode("ascii", "ignore")))
        needles.append(("utf16", args.string.encode("utf-16-le")))
        try:
            needles.append(("sjis", args.string.encode("shift_jis")))
        except UnicodeEncodeError:
            pass
    if args.u32 is not None:
        needles.append(("u32", struct.pack("<I", parse_int(args.u32) & 0xFFFFFFFF)))
    if not needles:
        fail("use --hex, --string ou --u32")
    rows = []
    for m in modules(g):
        for kind, nd in needles:
            start = 0
            while (i := m.data.find(nd, start)) >= 0 and len(rows) < args.limit:
                rows.append((m.name, f"0x{m.base + i:08X}", kind, ""))
                start = i + 1
    if args.files:
        from .mods import WorkTree
        fdir = WorkTree(g.extract).files_dir
        for f in fdir.rglob("*"):
            if not f.is_file() or len(rows) >= args.limit:
                continue
            data = f.read_bytes()
            for kind, nd in needles:
                i = data.find(nd)
                if i >= 0:
                    rows.append(("files", f"+0x{i:X}", kind, f.relative_to(fdir).as_posix()))
    print(table(rows, ["onde", "endereço/offset", "como", "arquivo"]) if rows else "nada encontrado")
    return 0


def cmd_sym(args) -> int:
    g = load_game(args.game)
    proj = find_project(g.dir)
    if not proj:
        fail("sem projeto dsd — rode `factory decomp init` (símbolos vêm do dsd)")
    if re.fullmatch(r"(0x)?[0-9a-fA-F]{6,8}", args.query):
        syms = proj.at(int(args.query, 16))
    else:
        syms = proj.find(args.query)
    rows = [(s.name, s.kind, s.mode or "", f"0x{s.addr:08X}", f"0x{s.size:X}" if s.size else "", s.module)
            for s in syms[: args.limit]]
    print(table(rows, ["nome", "tipo", "modo", "endereço", "tamanho", "módulo"]) if rows else "nada")
    return 0


def register(sub) -> None:
    p = sub.add_parser("dis", help="desmontar função/endereço (ARM/THUMB) com símbolos e literais")
    p.add_argument("target", help="endereço 0x0201.... ou nome de símbolo (com projeto dsd)")
    p.add_argument("--game")
    p.add_argument("--module", help="arm9 | itcm | dtcm | ovN (obrigatório se overlays se sobrepõem)")
    p.add_argument("--thumb", action="store_true")
    p.add_argument("--arm", action="store_true")
    p.add_argument("--count", type=int, default=48, help="instruções quando o tamanho é desconhecido")
    p.set_defaults(func=cmd_dis)

    p = sub.add_parser("hookcheck", help="auditar um ponto de hook (bytes sobrescritos, BL partido, PC-relativo)")
    p.add_argument("target")
    p.add_argument("--kind", choices=["jump", "call", "hook"], default="call")
    p.add_argument("--game")
    p.add_argument("--module")
    p.add_argument("--thumb", action="store_true")
    p.add_argument("--arm", action="store_true")
    p.set_defaults(func=cmd_hookcheck)

    p = sub.add_parser("xrefs", help="quem referencia um endereço/símbolo (relocs do dsd ou scan bruto)")
    p.add_argument("target")
    p.add_argument("--game")
    p.add_argument("--module")
    p.add_argument("--raw", action="store_true", help="forçar scan bruto mesmo com projeto dsd")
    p.set_defaults(func=cmd_xrefs)

    p = sub.add_parser("find", help="procurar bytes/strings/valores nos módulos (e arquivos com --files)")
    p.add_argument("--game")
    p.add_argument("--hex")
    p.add_argument("--string")
    p.add_argument("--u32")
    p.add_argument("--files", action="store_true", help="procurar também no NitroFS")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("sym", help="procurar símbolos do dsd por nome ou endereço")
    p.add_argument("query")
    p.add_argument("--game")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_sym)
