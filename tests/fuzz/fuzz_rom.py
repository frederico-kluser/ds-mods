"""Fuzzing do leitor de ROM (tools/factory/rom.py): a ROM de DS é sempre arquivo de fora.

Entrada: os primeiros 0x180 bytes viram o cabeçalho (offsets e tamanhos de ARM9/ARM7, FNT, FAT e das
tabelas de overlay); o resto é copiado a partir de 0x4000 (área do ARM9) numa imagem de 64 KiB.
Contrato: inspect_bytes() devolve RomInfo (ou levanta ValueError); overlays nunca têm tamanho
negativo; describe() e to_dict() funcionam com qualquer RomInfo.
"""

import fuzzlib

with fuzzlib.instrumented():
    from factory import rom

SIZE = 0x10000


def TestOneInput(data: bytes) -> None:
    raw = bytearray(SIZE)
    head, body = data[:0x180], data[0x180:0x180 + SIZE - 0x4000]
    raw[:len(head)] = head
    raw[0x4000:0x4000 + len(body)] = body
    try:
        info = rom.inspect_bytes(bytes(raw))
    except ValueError:
        return
    assert info.size == SIZE
    for ov in info.arm9_overlays + info.arm7_overlays:
        assert ov.rom_size >= 0, ov
    rom.describe(info)
    info.to_dict()


if __name__ == "__main__":
    fuzzlib.main(TestOneInput)
