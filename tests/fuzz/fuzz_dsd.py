"""Fuzzing dos parsers de configuração do dsd (tools/factory/dsdproj.py): symbols.txt e relocs.txt vêm de
projetos de decomp de terceiros e de edições manuais.

Entrada: o 1.º byte escolhe o parser (par → symbols.txt, ímpar → relocs.txt); o resto é o texto (latin-1).
Contrato: nunca levanta exceção; símbolos ordenados por endereço, com tamanho None ou ≥ 0 e modo
None/arm/thumb; relocações com origem e destino ≥ 0.
"""

import fuzzlib

with fuzzlib.instrumented():
    from factory import dsdproj


def TestOneInput(data: bytes) -> None:
    if not data:
        return
    text = data[1:].decode("latin-1")
    if data[0] % 2 == 0:
        syms = dsdproj.parse_symbols_text(text, "main")
        assert [s.addr for s in syms] == sorted(s.addr for s in syms)
        for s in syms:
            assert s.size is None or s.size >= 0, s
            assert s.mode in (None, "arm", "thumb"), s
    else:
        for r in dsdproj.parse_relocs_text(text, "main"):
            assert r.src >= 0 and r.dst >= 0, r


if __name__ == "__main__":
    fuzzlib.main(TestOneInput)
