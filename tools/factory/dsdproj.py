"""Read a dsd project (config/arm9/config.yaml + symbols.txt/relocs.txt/delinks.txt per module).

Formats (dsd 0.12):
  symbols.txt  `Name kind:function(thumb,size=0x6) addr:0x02000066 [local] [ambiguous]`
               `data_02057b28 kind:data(any) addr:0x02057b28` / `kind:bss(size=0x8)` / `kind:label(arm)`
  relocs.txt   `from:0x02000814 kind:arm_call to:0x02000a78 module:main`   (module: main|itcm|dtcm|overlay(N)|
               overlays(N,M,...)|none)
  delinks.txt  section ranges, then `path/to/file.c:` blocks with `.text start:0x.. end:0x..` lines
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml  # type: ignore

SYM_RE = re.compile(r"^(?P<name>\S+)\s+kind:(?P<kind>\w+)(?:\((?P<params>[^)]*)\))?\s+addr:(?P<addr>0x[0-9a-fA-F]+)(?P<rest>.*)$")
REL_RE = re.compile(r"from:(0x[0-9a-fA-F]+)\s+kind:(\w+)\s+to:(0x[0-9a-fA-F]+)(?:\s+add:(-?0x[0-9a-fA-F]+|-?\d+))?\s+module:(\S+)")


@dataclass
class Symbol:
    name: str
    kind: str                   # function | data | bss | label
    addr: int
    size: int | None
    mode: str | None            # arm | thumb (functions/labels)
    module: str
    flags: list[str] = field(default_factory=list)


@dataclass
class Reloc:
    src: int
    kind: str
    dst: int
    addend: int
    target_module: str
    module: str


@dataclass
class Module:
    name: str                   # main | itcm | dtcm | ovNNN
    id: int | None
    dir: Path
    symbols_path: Path
    relocs_path: Path
    delinks_path: Path
    symbols: list[Symbol] = field(default_factory=list)
    relocs: list[Reloc] = field(default_factory=list)


def parse_symbols(path: Path, module: str) -> list[Symbol]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("//")[0].strip()
        if not line:
            continue
        m = SYM_RE.match(line)
        if not m:
            continue
        params = [p.strip() for p in (m.group("params") or "").split(",") if p.strip()]
        size = None
        mode = None
        for p in params:
            if p.startswith("size="):
                size = int(p[5:], 0)
            elif p in ("arm", "thumb"):
                mode = p
        out.append(Symbol(name=m.group("name"), kind=m.group("kind"), addr=int(m.group("addr"), 16), size=size,
                          mode=mode, module=module, flags=m.group("rest").split()))
    out.sort(key=lambda s: s.addr)
    return out


def parse_relocs(path: Path, module: str) -> list[Reloc]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = REL_RE.search(line)
        if m:
            add = int(m.group(4), 0) if m.group(4) else 0
            out.append(Reloc(src=int(m.group(1), 16), kind=m.group(2), dst=int(m.group(3), 16), addend=add,
                             target_module=m.group(5), module=module))
    return out


class Project:
    def __init__(self, config_yaml: Path):
        self.config_path = config_yaml
        self.root = config_yaml.parent
        self.cfg = yaml.safe_load(config_yaml.read_text(encoding="utf-8"))
        self.modules: dict[str, Module] = {}
        mm = self.cfg["main_module"]
        self._add("main", None, mm)
        for a in self.cfg.get("autoloads", []):
            self._add(a.get("name", a.get("kind", "").lower()), None, a)
        for ov in self.cfg.get("overlays", []):
            self._add(ov.get("name", f"ov{ov['id']:03d}"), ov["id"], ov)
        self._loaded = False

    def _add(self, name: str, oid: int | None, entry: dict) -> None:
        d = self.root
        self.modules[name] = Module(name=name, id=oid, dir=d, symbols_path=d / entry["symbols"],
                                    relocs_path=d / entry["relocations"], delinks_path=d / entry["delinks"])

    def load(self) -> "Project":
        if not self._loaded:
            for m in self.modules.values():
                m.symbols = parse_symbols(m.symbols_path, m.name)
                m.relocs = parse_relocs(m.relocs_path, m.name)
            self._loaded = True
        return self

    # lookups -------------------------------------------------------------------------------------
    def find(self, query: str) -> list[Symbol]:
        self.load()
        q = query.lower()
        exact, partial = [], []
        for m in self.modules.values():
            for s in m.symbols:
                n = s.name.lower()
                if n == q:
                    exact.append(s)
                elif q in n:
                    partial.append(s)
        return exact or partial

    def at(self, addr: int, module: str | None = None) -> list[Symbol]:
        """Symbols containing addr (several when overlays overlap)."""
        self.load()
        out = []
        for m in self.modules.values():
            if module and m.name != module:
                continue
            for s in m.symbols:
                end = s.addr + (s.size or 1)
                if s.addr <= addr < end:
                    out.append(s)
        return out

    def xrefs_to(self, addr: int, module: str | None = None) -> list[Reloc]:
        self.load()
        out = []
        for m in self.modules.values():
            for r in m.relocs:
                if r.dst == addr or (r.dst & ~1) == (addr & ~1):
                    if module is None or _module_matches(r.target_module, module):
                        out.append(r)
        return out

    def xrefs_from(self, start: int, end: int, module: str) -> list[Reloc]:
        self.load()
        m = self.modules.get(module)
        return [r for r in (m.relocs if m else []) if start <= r.src < end]

    def name_of(self, addr: int, target_module: str | None = None) -> str | None:
        self.load()
        cands = []
        for m in self.modules.values():
            if target_module and not _module_matches(target_module, m.name):
                continue
            for s in m.symbols:
                if s.addr == (addr & ~1) or s.addr == addr:
                    cands.append(s)
        if not cands:
            return None
        return cands[0].name if len(cands) == 1 else "|".join(sorted({c.name for c in cands}))


def _module_matches(target: str, module: str) -> bool:
    if target == module or target == "main" and module == "main":
        return True
    m = re.match(r"overlays?\(([\d,\s]+)\)", target)
    if m and module.startswith("ov"):
        ids = {int(x) for x in m.group(1).split(",") if x.strip()}
        return int(module[2:]) in ids
    return False


def find_project(game_dir: Path) -> Project | None:
    for cand in (game_dir / "decomp" / "config" / "arm9" / "config.yaml", game_dir / "decomp" / "config" / "config.yaml"):
        if cand.exists():
            return Project(cand)
    hits = list((game_dir / "decomp").glob("**/arm9/config.yaml")) if (game_dir / "decomp").exists() else []
    return Project(hits[0]) if hits else None
