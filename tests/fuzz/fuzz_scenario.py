"""Fuzzing da validação de cenários (tools/factory/emu.py e analyze.py): cenários e critérios são JSON
escrito pelo agente ou pelo usuário.

Entrada: JSON (UTF-8) com as chaves "inputs", "watches" e "expect" de um objeto.
Contrato: _normalize_inputs()/_normalize_watches() devolvem listas e evaluate_expect() devolve um
resultado (ou None) — ou encerram com SystemExit (fail, mensagem clara); nenhuma outra exceção.
"""

import contextlib
import io
import json

import fuzzlib
import numpy as np

with fuzzlib.instrumented():
    from factory import analyze, emu

FRAMES = [analyze.Frame(index=i, time=i / 60.0, path=None, img=np.zeros((8, 8, 3), dtype=np.uint8))
          for i in range(3)]
STATS = analyze.frame_stats(FRAMES)
RAM = {"placar": [(0, 3.0), (1, 4.0), (2, 5.0)]}


def TestOneInput(data: bytes) -> None:
    try:
        scen = json.loads(data.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        return
    if not isinstance(scen, dict):
        return
    with contextlib.redirect_stderr(io.StringIO()):
        for normalize, key in ((emu._normalize_inputs, "inputs"), (emu._normalize_watches, "watches")):
            try:
                assert isinstance(normalize(scen.get(key)), list)
            except SystemExit:
                pass
        try:
            res = analyze.evaluate_expect(scen.get("expect"), stats=STATS, ram=RAM, comparison=None,
                                          frames=FRAMES, ref_frames=FRAMES)
        except SystemExit:
            return
    assert res is None or isinstance(res, dict)


if __name__ == "__main__":
    fuzzlib.main(TestOneInput)
