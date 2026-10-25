"""Nintendo DS ROM inspection: header, overlay tables, NitroFS, secure area, SDK build info.

Pure Python on purpose (no decompression), so it is instant even for 512 MiB ROMs.
Offsets follow GBATEK "DS Cartridge Header".
"""
from __future__ import annotations

import re
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

NITROCODE = 0xDEC00621          # ARM9 footer / ModuleParams magic
NITROCODE_LE = 0x2106C0DE
DECRYPTED_SECURE_ID = 0xE7FFDEFF_E7FFDEFF

REGIONS = {
    "E": "USA", "P": "Europa", "J": "Japão", "K": "Coreia", "D": "Alemanha", "F": "França",
    "I": "Itália", "S": "Espanha", "U": "Austrália", "C": "China", "O": "Internacional",
    "H": "Holanda", "R": "Rússia", "W": "Europa (outros)", "X": "Europa (outros)", "Y": "Europa (outros)",
    "Z": "Europa (outros)", "V": "Europa (outros)", "N": "Nórdicos", "Q": "Dinamarca", "T": "USA+AUS",
}

# Public decompilation projects (verified to exist on GitHub, 2026-10). Gamecode prefix (3 chars) → repo.
KNOWN_DECOMPS = {
    "YKG": "Yokimitsuro/khdays-decomp (Kingdom Hearts 358/2 Days, alvo EU YKGP)",
    "BK9": "Yokimitsuro/khrecoded-decomp (Kingdom Hearts Re:coded)",
    "AZE": "zeldaret/ph (Zelda: Phantom Hourglass, EUR/USA)",
    "BKI": "zeldaret/st (Zelda: Spirit Tracks)",
    "ADA": "pret/pokediamond (Pokémon Diamond)",
    "APA": "pret/pokediamond (Pokémon Pearl)",
    "CPU": "pret/pokeplatinum (Pokémon Platinum)",
    "IPK": "pret/pokeheartgold (Pokémon HeartGold)",
    "IPG": "pret/pokeheartgold (Pokémon SoulSilver)",
}


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/MODBUS as used by the DS header (poly 0xA001 reflected, init 0xFFFF)."""
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


@dataclass
class Overlay:
    id: int
    ram_address: int
    ram_size: int
    bss_size: int
    sinit_start: int
    sinit_end: int
    file_id: int
    compressed_size: int
    compressed: bool
    signed: bool
    rom_offset: int = 0
    rom_size: int = 0


@dataclass
class ModuleParams:
    offset: int                 # offset inside arm9.bin
    autoload_list: int
    autoload_list_end: int
    autoload_start: int
    static_bss_start: int
    static_bss_end: int
    compressed_static_end: int
    sdk_version: int

    @property
    def sdk_version_str(self) -> str:
        v = self.sdk_version
        return f"{v >> 24}.{(v >> 16) & 0xFF}.{v & 0xFFFF}"


@dataclass
class RomInfo:
    path: str
    size: int
    title: str
    gamecode: str
    makercode: str
    unitcode: int
    region: str
    revision: int
    device_capacity_mib: float
    arm9_rom_offset: int
    arm9_entry: int
    arm9_ram: int
    arm9_size: int
    arm7_rom_offset: int
    arm7_entry: int
    arm7_ram: int
    arm7_size: int
    fnt_offset: int
    fnt_size: int
    fat_offset: int
    fat_size: int
    arm9_ovt_offset: int
    arm9_ovt_size: int
    arm7_ovt_offset: int
    arm7_ovt_size: int
    banner_offset: int
    header_crc_ok: bool
    logo_crc_ok: bool
    secure_area: str            # "decrypted" | "encrypted" | "none" (homebrew, arm9 not in 0x4000..0x7FFF)
    arm9_footer: bool           # NitroSDK footer present (nitrocode)
    module_params: ModuleParams | None
    sdk_strings: list[str] = field(default_factory=list)
    arm9_overlays: list[Overlay] = field(default_factory=list)
    arm7_overlays: list[Overlay] = field(default_factory=list)
    file_count: int = 0
    known_decomp: str | None = None

    @property
    def is_dsi(self) -> bool:
        return bool(self.unitcode & 0x02)

    @property
    def is_homebrew(self) -> bool:
        return self.gamecode in ("", "####") or self.gamecode.startswith("\x00")

    @property
    def game_id(self) -> str:
        code = re.sub(r"[^A-Za-z0-9]", "", self.gamecode) or "HOMEBREW"
        return code + (f"-r{self.revision}" if self.revision else "")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["is_dsi"] = self.is_dsi
        d["game_id"] = self.game_id
        if self.module_params:
            d["module_params"]["sdk_version_str"] = self.module_params.sdk_version_str
        return d


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def parse_overlay_table(table: bytes, fat: bytes) -> list[Overlay]:
    out = []
    for i in range(len(table) // 32):
        (ovid, ram, size, bss, s0, s1, fid, comp) = struct.unpack_from("<8I", table, i * 32)
        start = end = 0
        if fid * 8 + 8 <= len(fat):
            start, end = struct.unpack_from("<2I", fat, fid * 8)
            if end < start:                      # broken FAT entry
                start = end = 0
        out.append(Overlay(id=ovid, ram_address=ram, ram_size=size, bss_size=bss, sinit_start=s0,
                           sinit_end=s1, file_id=fid, compressed_size=comp & 0xFFFFFF,
                           compressed=bool(comp & 0x01000000), signed=bool(comp & 0x02000000),
                           rom_offset=start, rom_size=end - start))
    return out


def find_module_params(arm9: bytes, footer: bytes) -> ModuleParams | None:
    """Locate NitroSDK _start_ModuleParams via the 12-byte ARM9 footer (or by scanning)."""
    offset = None
    if len(footer) >= 8 and _u32(footer, 0) == NITROCODE:
        offset = _u32(footer, 4)
    if offset is None or offset + 36 > len(arm9):
        # fallback: scan for the two nitrocodes that terminate ModuleParams
        idx = arm9.find(struct.pack("<II", NITROCODE, NITROCODE_LE))
        if idx < 28:
            return None
        offset = idx - 28
    vals = struct.unpack_from("<9I", arm9, offset)
    if vals[7] != NITROCODE or vals[8] != NITROCODE_LE:
        return None
    return ModuleParams(offset=offset, autoload_list=vals[0], autoload_list_end=vals[1],
                        autoload_start=vals[2], static_bss_start=vals[3], static_bss_end=vals[4],
                        compressed_static_end=vals[5], sdk_version=vals[6])


SDK_RE = re.compile(rb"\[SDK\+[A-Z0-9_]+:[ -~]{1,60}\]")


def inspect(path: str | Path, *, scan_strings: bool = True) -> RomInfo:
    path = Path(path)
    return inspect_bytes(path.read_bytes(), path=str(path), scan_strings=scan_strings)


def inspect_bytes(data: bytes, *, path: str = "<memória>", scan_strings: bool = True) -> RomInfo:
    """Parse a ROM image already in memory; raises ValueError when it cannot be a DS ROM."""
    if len(data) < 0x200:
        raise ValueError(f"{path}: pequeno demais para ser uma ROM de DS")
    h = data[:0x200]
    title = h[0:12].split(b"\x00")[0].decode("ascii", "replace").strip()
    gamecode = h[0x0C:0x10].split(b"\x00")[0].decode("ascii", "replace")
    maker = h[0x10:0x12].split(b"\x00")[0].decode("ascii", "replace")
    unit = h[0x12]
    revision = h[0x1E]
    cap = (128 * 1024 << h[0x14]) / (1024 * 1024) if h[0x14] < 16 else 0
    g = lambda o: _u32(h, o)  # noqa: E731
    arm9_off, arm9_entry, arm9_ram, arm9_size = g(0x20), g(0x24), g(0x28), g(0x2C)
    arm7_off, arm7_entry, arm7_ram, arm7_size = g(0x30), g(0x34), g(0x38), g(0x3C)
    fnt_off, fnt_size, fat_off, fat_size = g(0x40), g(0x44), g(0x48), g(0x4C)
    ovt9_off, ovt9_size, ovt7_off, ovt7_size = g(0x50), g(0x54), g(0x58), g(0x5C)
    banner = g(0x68)
    header_crc_ok = crc16(h[0:0x15E]) == _u16(h, 0x15E)
    logo_crc_ok = crc16(h[0xC0:0x15C]) == _u16(h, 0x15C) == 0xCF56

    if 0x4000 <= arm9_off < 0x8000 and arm9_off + 8 <= len(data):
        sid = struct.unpack_from("<Q", data, arm9_off)[0]
        secure = "decrypted" if sid == DECRYPTED_SECURE_ID else "encrypted"
    else:
        secure = "none"

    arm9 = data[arm9_off:arm9_off + arm9_size]
    footer = data[arm9_off + arm9_size:arm9_off + arm9_size + 12]
    mp = find_module_params(arm9, footer) if arm9 else None
    fat = data[fat_off:fat_off + fat_size]
    ov9 = parse_overlay_table(data[ovt9_off:ovt9_off + ovt9_size], fat) if ovt9_size else []
    ov7 = parse_overlay_table(data[ovt7_off:ovt7_off + ovt7_size], fat) if ovt7_size else []
    sdk = []
    if scan_strings:
        seen = set()
        for m in SDK_RE.finditer(data):
            s = m.group(0).decode("ascii", "replace")
            if s not in seen:
                seen.add(s)
                sdk.append(s)
            if len(sdk) >= 40:
                break
    known = KNOWN_DECOMPS.get(gamecode[:3]) if gamecode else None
    return RomInfo(
        path=path, size=len(data), title=title, gamecode=gamecode, makercode=maker, unitcode=unit,
        region=REGIONS.get(gamecode[3:4], "?") if len(gamecode) == 4 else "?", revision=revision,
        device_capacity_mib=cap, arm9_rom_offset=arm9_off, arm9_entry=arm9_entry, arm9_ram=arm9_ram,
        arm9_size=arm9_size, arm7_rom_offset=arm7_off, arm7_entry=arm7_entry, arm7_ram=arm7_ram,
        arm7_size=arm7_size, fnt_offset=fnt_off, fnt_size=fnt_size, fat_offset=fat_off, fat_size=fat_size,
        arm9_ovt_offset=ovt9_off, arm9_ovt_size=ovt9_size, arm7_ovt_offset=ovt7_off,
        arm7_ovt_size=ovt7_size, banner_offset=banner, header_crc_ok=header_crc_ok,
        logo_crc_ok=logo_crc_ok, secure_area=secure, arm9_footer=len(footer) >= 4 and _u32(footer, 0) == NITROCODE,
        module_params=mp, sdk_strings=sdk, arm9_overlays=ov9, arm7_overlays=ov7,
        file_count=fat_size // 8, known_decomp=known,
    )


def list_files(path: str | Path) -> list[tuple[int, str, int, int]]:
    """NitroFS listing: (file_id, path, rom_offset, size). Uses ndspy's FNT parser."""
    import ndspy.rom  # type: ignore

    rom = ndspy.rom.NintendoDSRom.fromFile(str(path))
    fat = []
    data = Path(path).read_bytes()
    info = inspect(path, scan_strings=False)
    raw_fat = data[info.fat_offset:info.fat_offset + info.fat_size]
    for i in range(len(raw_fat) // 8):
        s, e = struct.unpack_from("<2I", raw_fat, i * 8)
        fat.append((s, e - s))
    out = []
    for fid, name in _walk_fnt(rom.filenames, ""):
        off, size = fat[fid] if fid < len(fat) else (0, 0)
        out.append((fid, name, off, size))
    out.sort()
    return out


def _walk_fnt(folder, prefix: str):
    for i, fname in enumerate(folder.files):
        yield folder.firstID + i, prefix + fname
    for sub_name, sub in folder.folders:
        yield from _walk_fnt(sub, f"{prefix}{sub_name}/")


def describe(info: RomInfo) -> str:
    lines = [
        f"Título      : {info.title}   Gamecode: {info.gamecode}   Maker: {info.makercode}   "
        f"Região: {info.region}   Revisão: {info.revision}",
        f"Tamanho     : {info.size / 1048576:.1f} MiB   Unidade: {'DSi-enhanced/DSi' if info.is_dsi else 'NDS'}"
        f"   Header CRC: {'ok' if info.header_crc_ok else 'INVÁLIDO'}",
        f"ARM9        : rom 0x{info.arm9_rom_offset:X}  ram 0x{info.arm9_ram:08X}  entry 0x{info.arm9_entry:08X}"
        f"  size 0x{info.arm9_size:X}",
        f"ARM7        : rom 0x{info.arm7_rom_offset:X}  ram 0x{info.arm7_ram:08X}  entry 0x{info.arm7_entry:08X}"
        f"  size 0x{info.arm7_size:X}",
        f"Overlays    : ARM9 {len(info.arm9_overlays)}  ARM7 {len(info.arm7_overlays)}   Arquivos NitroFS: {info.file_count}",
        f"Secure area : {info.secure_area}",
    ]
    if info.module_params:
        mp = info.module_params
        lines.append(f"NitroSDK    : ModuleParams @arm9+0x{mp.offset:X}  sdk_version=0x{mp.sdk_version:08X}"
                     f" ({mp.sdk_version_str})  arm9 comprimido={'sim' if mp.compressed_static_end else 'não'}")
    else:
        lines.append("NitroSDK    : ModuleParams não encontrado (homebrew/libnds?)")
    if info.sdk_strings:
        lines.append("SDK strings : " + "; ".join(info.sdk_strings[:6]) + (" …" if len(info.sdk_strings) > 6 else ""))
    if info.known_decomp:
        lines.append(f"Decomp público conhecido: {info.known_decomp}")
    return "\n".join(lines)
