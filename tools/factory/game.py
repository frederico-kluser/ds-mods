"""Game workspaces: `factory new <rom>` creates games/<ID>/ and extracts the ROM; helpers to load them."""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths, rom
from .util import fail, hashes, info, read_json, run, table, warn, write_json

README_GAME = """# {title} ({gamecode})

Workspace criado por `factory new` em {date}. ROM base: `{rom}` (sha1 `{sha1}`).

| Pasta | O que é | Git |
|---|---|---|
| `game.json` | metadados da ROM (header, overlays, SDK, hashes) | sim |
| `notes/` | descobertas sobre o jogo: mapa de RAM, funções, formatos | sim |
| `scenarios/` | cenários de teste do ciclo visual (`factory run`) | sim |
| `inputs/` | logs de input gravados (`factory play`) para replays do boot | sim |
| `states/` | savestates do BizHawk (`*.State` ficam fora do git; `index.json` sim) | só o índice |
| `mods/<nome>/` | código/dados de cada mod (`factory mod ...`) | sim |
| `decomp/` | projeto de descompilação dsd (Via A) | config/src sim; asm/build não |
| `extract/` `build/` `runs/` `cache/` | derivados da ROM — regeneráveis | não |
"""


@dataclass
class Game:
    id: str
    dir: Path
    meta: dict

    @property
    def rom(self) -> Path:
        return paths.ROOT / self.meta["rom"]

    @property
    def extract(self) -> Path:
        return self.dir / "extract"

    @property
    def build(self) -> Path:
        return self.dir / "build"

    @property
    def runs(self) -> Path:
        return self.dir / "runs"

    @property
    def states(self) -> Path:
        return self.dir / "states"

    @property
    def scenarios(self) -> Path:
        return self.dir / "scenarios"

    @property
    def mods(self) -> Path:
        return self.dir / "mods"

    @property
    def cache(self) -> Path:
        return self.dir / "cache"

    @property
    def decomp(self) -> Path:
        return self.dir / "decomp"

    @property
    def extract_config(self) -> Path:
        return self.extract / "config.yaml"

    def save(self) -> None:
        write_json(self.dir / "game.json", self.meta)


def list_games() -> list[str]:
    if not paths.GAMES.exists():
        return []
    return sorted(d.name for d in paths.GAMES.iterdir() if (d / "game.json").exists())


def load_game(game_id: str | None) -> Game:
    games = list_games()
    if not game_id or game_id == ".":
        if len(games) == 1:
            game_id = games[0]
        elif not games:
            fail("nenhum jogo ainda — rode `factory new roms/<rom>.nds`")
        else:
            fail(f"vários jogos ({', '.join(games)}): informe qual com --game <ID>")
    d = paths.GAMES / game_id
    meta = read_json(d / "game.json")
    if meta is None:
        # allow prefix match (e.g. 'YKG' -> 'YKGP')
        cands = [g for g in games if g.lower().startswith(game_id.lower())]
        if len(cands) == 1:
            return load_game(cands[0])
        fail(f"jogo '{game_id}' não existe (jogos: {', '.join(games) or 'nenhum'})")
    return Game(id=game_id, dir=d, meta=meta)


def bios_paths() -> dict[str, Path]:
    found = {}
    for name in ("bios7.bin", "bios9.bin", "firmware.bin"):
        p = paths.BIOS / name
        if p.exists():
            found[name] = p
    return found


def extract_rom(g: Game, *, force: bool = False) -> bool:
    """dsd rom extract → games/<id>/extract (decompressed arm9/arm7/overlays + files + config.yaml)."""
    if g.extract_config.exists() and not force:
        info(f"extração já existe: {paths.rel(g.extract)}")
        return True
    if force and g.extract.exists():
        shutil.rmtree(g.extract)
    argv = [paths.dsd(), "rom", "extract", "--rom", g.rom, "--output-path", g.extract]
    bios = bios_paths()
    if "bios7.bin" in bios:
        argv += ["--arm7-bios", bios["bios7.bin"]]
    proc = run(argv, check=False, capture=True)
    if proc.returncode != 0:
        warn("dsd rom extract falhou:\n" + (proc.stderr or proc.stdout or "").strip()[-2000:])
        if g.meta.get("secure_area") == "encrypted" and "bios7.bin" not in bios:
            warn("ROM com secure area CRIPTOGRAFADA: coloque o dump do seu console em roms/bios/bios7.bin "
                 "(e bios9.bin, firmware.bin p/ o emulador) e rode `factory extract --force`.")
        return False
    info(f"extraído em {paths.rel(g.extract)}")
    rebuild_base(g)
    return True


def rebuild_base(g: Game) -> Path | None:
    """Rebuild the untouched extraction with dsd → the ROM used for every 'base' emulator run.

    BizHawk (waterbox melonDS) only loads a savestate into a ROM image of the SAME SIZE, and dsd lays out
    and pads rebuilt ROMs in its own deterministic way. Running the base game from this rebuild makes
    states recorded on it loadable on mod ROMs (also built by dsd), which is what RAM hotpatching needs.
    """
    out = g.build / "base" / f"{g.id}_base.nds"
    out.parent.mkdir(parents=True, exist_ok=True)
    p = run([paths.dsd(), "rom", "build", "--config", g.extract_config, "--rom", out], check=False, capture=True,
            quiet=True)
    if p.returncode != 0 or not out.exists():
        warn("dsd rom build da base falhou; runs 'base' usarão a ROM original (savestates base→mod podem não "
             "carregar):\n" + (p.stderr or "")[-800:])
        g.meta.pop("emu_base", None)
        g.save()
        return None
    import numpy as np

    a, b = g.rom.read_bytes(), out.read_bytes()
    n = min(len(a), len(b))
    diff = int(np.count_nonzero(np.frombuffer(a, np.uint8, n) != np.frombuffer(b, np.uint8, n)))
    g.meta["emu_base"] = paths.rel(out)
    g.meta["rebuild_check"] = {"original_size": len(a), "rebuilt_size": len(b), "bytes_differ": diff,
                               "note": "≤ ~4 bytes de CRC = round-trip fiel" if len(a) == len(b) else
                               "tamanhos diferem (ROM trimada?) — mods e base rodam ambos pela reconstrução"}
    g.save()
    info(f"ROM base reconstruída p/ emulação: {paths.rel(out)} (difere em {diff} byte(s), "
         f"{len(a)}→{len(b)} bytes)")
    return out


def write_listings(g: Game) -> None:
    """info/files.tsv and info/overlays.tsv: quick, grep-able maps of the ROM contents."""
    out = g.dir / "info"
    out.mkdir(parents=True, exist_ok=True)
    ri = rom.inspect(g.rom, scan_strings=False)
    lines = ["id\tram\tram_size\tbss\tfile_id\trom_size\tcompressed\tsigned"]
    for ov in ri.arm9_overlays:
        lines.append(f"{ov.id}\t0x{ov.ram_address:08X}\t0x{ov.ram_size:X}\t0x{ov.bss_size:X}\t{ov.file_id}"
                     f"\t0x{ov.rom_size:X}\t{int(ov.compressed)}\t{int(ov.signed)}")
    (out / "overlays9.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        files = rom.list_files(g.rom)
        flines = ["file_id\tpath\trom_offset\tsize"] + [f"{fid}\t{p}\t0x{off:X}\t{size}" for fid, p, off, size in files]
        (out / "files.tsv").write_text("\n".join(flines) + "\n", encoding="utf-8")
    except Exception as exc:  # ndspy may choke on odd homebrew FNTs
        warn(f"listagem NitroFS falhou: {exc}")


def cmd_new(args) -> int:
    src = Path(args.rom).expanduser().resolve()
    if not src.exists():
        fail(f"ROM não encontrada: {src}")
    ri = rom.inspect(src)
    hs = hashes(src)
    gid = args.id or ri.game_id
    gdir = paths.GAMES / gid
    if (gdir / "game.json").exists() and not args.force:
        old = read_json(gdir / "game.json")
        if old.get("sha1") != hs["sha1"]:
            fail(f"games/{gid} já existe com outra ROM (sha1 {old.get('sha1')}); use --id outro-nome")
        info(f"games/{gid} já existe (mesma ROM) — atualizando metadados")
    # keep the base ROM inside roms/ (gitignored) under a stable name
    paths.ROMS.mkdir(parents=True, exist_ok=True)
    try:
        src.relative_to(paths.ROMS)
        rom_path = src
    except ValueError:
        rom_path = paths.ROMS / f"{gid}.nds"
        if not rom_path.exists():
            shutil.copy2(src, rom_path)
            info(f"ROM copiada para {paths.rel(rom_path)}")
    meta = {
        "id": gid, "title": ri.title, "gamecode": ri.gamecode, "maker": ri.makercode, "region": ri.region,
        "revision": ri.revision, "unitcode": ri.unitcode, "rom": paths.rel(rom_path), "size": ri.size, **hs,
        "secure_area": ri.secure_area, "header_crc_ok": ri.header_crc_ok,
        "arm9": {"rom_offset": ri.arm9_rom_offset, "ram": ri.arm9_ram, "entry": ri.arm9_entry, "size": ri.arm9_size},
        "arm7": {"rom_offset": ri.arm7_rom_offset, "ram": ri.arm7_ram, "entry": ri.arm7_entry, "size": ri.arm7_size},
        "arm9_overlays": len(ri.arm9_overlays), "arm7_overlays": len(ri.arm7_overlays), "files": ri.file_count,
        "nitrosdk": ({"module_params_offset": ri.module_params.offset,
                      "sdk_version": f"0x{ri.module_params.sdk_version:08X}",
                      "arm9_compressed": bool(ri.module_params.compressed_static_end)}
                     if ri.module_params else None),
        "sdk_strings": ri.sdk_strings[:20], "known_decomp": ri.known_decomp,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    g = Game(id=gid, dir=gdir, meta=meta)
    for sub in ("notes", "scenarios", "inputs", "states", "mods"):
        (gdir / sub).mkdir(parents=True, exist_ok=True)
    g.save()
    if not (gdir / "README.md").exists():
        (gdir / "README.md").write_text(README_GAME.format(
            title=ri.title, gamecode=ri.gamecode, date=meta["created_at"], rom=meta["rom"], sha1=hs["sha1"]),
            encoding="utf-8")
    if not (gdir / "notes" / "README.md").exists():
        (gdir / "notes" / "README.md").write_text(
            f"# Notas — {ri.title}\n\nRegistre aqui o que for descoberto (endereços de RAM, funções, formatos de"
            " arquivo, efeitos de cada mod). Cite sempre a evidência (run, endereço, disassembly).\n\n"
            "## Mapa de RAM\n\n| endereço | tipo | significado | evidência |\n|---|---|---|---|\n",
            encoding="utf-8")
    print(rom.describe(ri))
    write_listings(g)
    ok = True
    if not args.no_extract:
        ok = extract_rom(g)
    if ri.secure_area == "encrypted" and len(bios_paths()) < 3:
        warn("secure area criptografada: o BizHawk exigirá BIOS reais (roms/bios/bios7.bin, bios9.bin, "
             "firmware.bin). Sem eles, o ciclo visual não roda esta ROM.")
    info(f"jogo pronto: games/{gid}  (próximo: `factory info --game {gid}`, `factory play --game {gid}`)")
    return 0 if ok else 1


def cmd_extract(args) -> int:
    g = load_game(args.game)
    return 0 if extract_rom(g, force=args.force) else 1


def cmd_info(args) -> int:
    g = load_game(args.game)
    ri = rom.inspect(g.rom)
    print(rom.describe(ri))
    print()
    rows = []
    for sub in ("extract", "decomp", "mods", "scenarios", "states", "inputs", "runs"):
        p = g.dir / sub
        n = sum(1 for _ in p.iterdir()) if p.exists() else 0
        rows.append((sub, "sim" if p.exists() else "não", n))
    print(table(rows, ["pasta", "existe", "itens"]))
    if args.overlays:
        print()
        print(table([(o.id, f"0x{o.ram_address:08X}", f"0x{o.ram_size:X}", f"0x{o.bss_size:X}", o.file_id,
                      "BLZ" if o.compressed else "-") for o in ri.arm9_overlays],
                    ["ov", "ram", "size", "bss", "file", "comp"]))
    return 0


def cmd_files(args) -> int:
    g = load_game(args.game)
    pat = (args.pattern or "").lower()
    rows = [(fid, p, f"0x{off:X}", size) for fid, p, off, size in rom.list_files(g.rom) if pat in p.lower()]
    print(table(rows[: args.limit], ["id", "path", "offset", "size"]))
    if len(rows) > args.limit:
        info(f"… {len(rows) - args.limit} a mais (use --limit)")
    return 0


def register(sub) -> None:
    p = sub.add_parser("new", help="registrar uma ROM: cria games/<ID>/, copia a ROM p/ roms/ e extrai")
    p.add_argument("rom", help="caminho da ROM .nds")
    p.add_argument("--id", help="nome do workspace (padrão: gamecode[-rREV])")
    p.add_argument("--no-extract", action="store_true", help="não rodar dsd rom extract")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("extract", help="(re)extrair a ROM base com dsd")
    p.add_argument("--game")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("info", help="resumo do jogo (header, SDK, overlays, pastas)")
    p.add_argument("--game")
    p.add_argument("--overlays", action="store_true", help="listar a tabela de overlays ARM9")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("files", help="listar arquivos do NitroFS")
    p.add_argument("pattern", nargs="?", help="filtro por substring do caminho")
    p.add_argument("--game")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_files)
