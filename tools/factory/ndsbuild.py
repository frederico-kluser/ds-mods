"""Minimal from-scratch NDS ROM writer (used by the selftest ROM; retail ROMs are rebuilt with dsd)."""
from __future__ import annotations

import struct

from .rom import NITROCODE, crc16


def _align(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def make_banner(title: str) -> bytes:
    """Version-1 banner (0x840 bytes): blank 32x32 icon, same title in the 6 languages."""
    b = bytearray(0x840)
    struct.pack_into("<H", b, 0x00, 1)
    # icon: 4bpp tiles, palette index 1 everywhere except a simple frame -> visible but generic
    for i in range(0x200):
        b[0x20 + i] = 0x11
    pal = [0x0000, 0x7C00, 0x7FFF] + [0] * 13
    struct.pack_into("<16H", b, 0x220, *pal)
    text = title.encode("utf-16-le")[:0xFE]
    for lang in range(6):
        off = 0x240 + lang * 0x100
        b[off:off + len(text)] = text
    struct.pack_into("<H", b, 0x02, crc16(bytes(b[0x20:0x840])))
    return bytes(b)


def build_fnt(files: dict[str, bytes], first_id: int = 0) -> tuple[bytes, list[str]]:
    """File Name Table for the given 'dir/sub/name' paths → (fnt bytes, paths in file-id order).
    File ids start at first_id (overlays take ids 0..n-1 on retail ROMs)."""
    tree: dict = {}
    for path in files:
        node = tree
        parts = path.split("/")
        for d in parts[:-1]:
            node = node.setdefault(d + "/", {})
        node[parts[-1]] = None
    dirs: list[tuple[dict, int]] = []          # (node, parent index)

    def collect(node: dict, parent: int) -> None:
        idx = len(dirs)
        dirs.append((node, parent))
        for name in sorted(k for k in node if k.endswith("/")):
            collect(node[name], idx)

    collect(tree, 0)
    order: list[str] = []
    first_ids: list[int] = []
    prefixes: dict[int, str] = {0: ""}
    sub_tables: list[bytes] = []
    for i, (node, _) in enumerate(dirs):
        first_ids.append(first_id + len(order))
        st = bytearray()
        for name in sorted(k for k in node if not k.endswith("/")):
            enc = name.encode("ascii")
            st += bytes([len(enc)]) + enc
            order.append(prefixes[i] + name)
        for name in sorted(k for k in node if k.endswith("/")):
            child = next(j for j, (n, _) in enumerate(dirs) if n is node[name])
            enc = name[:-1].encode("ascii")
            st += bytes([0x80 | len(enc)]) + enc + struct.pack("<H", 0xF000 + child)
            prefixes[child] = prefixes[i] + name
        st += b"\x00"
        sub_tables.append(bytes(st))
    main = bytearray()
    offset = 8 * len(dirs)
    for i, (_, parent) in enumerate(dirs):
        third = len(dirs) if i == 0 else 0xF000 + parent
        main += struct.pack("<IHH", offset, first_ids[i], third)
        offset += len(sub_tables[i])
    return bytes(main) + b"".join(sub_tables), order


def build_rom(*, arm9: bytes, arm7: bytes, title: str, gamecode: str, maker: str = "DM",
              arm9_entry: int, arm9_ram: int = 0x02000000, arm7_entry: int, arm7_ram: int,
              module_params_offset: int | None, arm9_autoload_hook: int = 0, logo: bytes | None = None,
              files: dict[str, bytes] | None = None, overlays: list[dict] | None = None,
              banner_title: str = "DS-MODS SELFTEST\nfactory\nds-mods") -> bytes:
    """logo: the 0x9C-byte header logo. Tools such as dsd validate it, so callers pass the copy that
    ships with the installed ndspy package (ndspy.rom.NintendoDSRom().nintendoLogo); it is never stored
    in this repository."""
    rom = bytearray(0x4000)
    # ARM9 at 0x4000 (retail position, inside the "secure area" window)
    arm9_off = 0x4000
    rom += arm9
    if module_params_offset is not None:
        rom += struct.pack("<3I", NITROCODE, module_params_offset, 0)   # NitroSDK ARM9 footer
    rom += b"\x00" * (_align(len(rom), 0x200) - len(rom))
    overlays = overlays or []
    ovt_off = len(rom) if overlays else 0
    ovt_size = 32 * len(overlays)
    rom += b"\x00" * ovt_size
    rom += b"\x00" * (_align(len(rom), 0x200) - len(rom))
    arm7_off = len(rom)
    rom += arm7
    rom += b"\x00" * (_align(len(rom), 0x200) - len(rom))
    files = files or {}
    fnt, order = build_fnt(files, first_id=len(overlays))
    fnt_off = len(rom)
    rom += fnt
    rom += b"\x00" * (_align(len(rom), 0x200) - len(rom))
    fat_off = len(rom)
    fat_size = 8 * (len(overlays) + len(order))
    rom += b"\x00" * fat_size
    rom += b"\x00" * (_align(len(rom), 0x200) - len(rom))
    banner_off = len(rom)
    rom += make_banner(banner_title)
    fat = bytearray()
    ovt = bytearray()
    for fid, ov in enumerate(overlays):          # overlay files come first (file ids 0..n-1)
        rom += b"\xFF" * (_align(len(rom), 0x200) - len(rom))
        start = len(rom)
        rom += ov["data"]
        fat += struct.pack("<2I", start, len(rom))
        end_addr = ov["ram"] + len(ov["data"])
        ovt += struct.pack("<8I", ov["id"], ov["ram"], len(ov["data"]), ov.get("bss", 0),
                           ov.get("sinit_start", end_addr), ov.get("sinit_end", end_addr), fid, 0)
    for name in order:
        rom += b"\xFF" * (_align(len(rom), 4) - len(rom))
        start = len(rom)
        rom += files[name]
        fat += struct.pack("<2I", start, len(rom))
    rom[fat_off:fat_off + fat_size] = fat
    if overlays:
        rom[ovt_off:ovt_off + ovt_size] = ovt
    used = len(rom)
    rom += b"\xFF" * (_align(len(rom), 0x200) - len(rom))

    cap = 0
    while (128 * 1024 << cap) < len(rom):
        cap += 1

    h = bytearray(0x200)
    h[0:12] = title.encode("ascii")[:12].ljust(12, b"\x00")
    h[0x0C:0x10] = gamecode.encode("ascii")[:4].ljust(4, b"\x00")
    h[0x10:0x12] = maker.encode("ascii")[:2].ljust(2, b"\x00")
    h[0x14] = cap
    struct.pack_into("<4I", h, 0x20, arm9_off, arm9_entry, arm9_ram, len(arm9))
    struct.pack_into("<4I", h, 0x30, arm7_off, arm7_entry, arm7_ram, len(arm7))
    struct.pack_into("<4I", h, 0x40, fnt_off, len(fnt), fat_off, fat_size)
    struct.pack_into("<4I", h, 0x50, ovt_off, ovt_size, 0, 0)
    struct.pack_into("<3I", h, 0x60, 0x00586000, 0x001808F8, banner_off)
    struct.pack_into("<HH", h, 0x6C, crc16(bytes(rom[0x4000:0x8000].ljust(0x4000, b"\x00"))), 0x051E)
    struct.pack_into("<2I", h, 0x70, arm9_autoload_hook, 0)
    struct.pack_into("<2I", h, 0x80, used, 0x4000)
    if logo:
        if len(logo) != 0x9C:
            raise ValueError("logo deve ter 0x9C bytes")
        h[0xC0:0x15C] = logo
    struct.pack_into("<H", h, 0x15C, crc16(bytes(h[0xC0:0x15C])))
    struct.pack_into("<H", h, 0x15E, crc16(bytes(h[0:0x15E])))
    rom[0:0x200] = h
    return bytes(rom)
