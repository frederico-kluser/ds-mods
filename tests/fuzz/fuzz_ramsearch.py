"""Fuzzing das condições do `factory ramsearch` (tools/factory/ramsearch.py): as expressões vêm do agente
ou do usuário e são avaliadas com eval() sobre os dumps de RAM, atrás de um filtro de caracteres e nomes.

Entrada: texto (latin-1); cada linha é uma condição (até 3) sobre os dumps f1, f2 e mark01 (64 posições).
Contrato: where_mask() devolve uma máscara booleana de 64 posições ou encerra com SystemExit (fail,
mensagem clara); nenhuma outra exceção e nenhum dump alterado.
"""

import contextlib
import io

import fuzzlib
import numpy as np

with fuzzlib.instrumented():
    from factory import ramsearch

N = 64
ARRAYS = {"f1": np.arange(N, dtype=np.int64), "f2": np.arange(N, dtype=np.int64)[::-1].copy(),
          "mark01": np.full(N, 7, dtype=np.int64)}


def TestOneInput(data: bytes) -> None:
    wheres = data.decode("latin-1").splitlines()[:3]
    arrays = {k: v.copy() for k, v in ARRAYS.items()}
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            mask = ramsearch.where_mask(wheres, arrays, N)
    except SystemExit:
        return
    assert mask.shape == (N,) and mask.dtype == bool
    assert all(np.array_equal(arrays[k], ARRAYS[k]) for k in ARRAYS)


if __name__ == "__main__":
    fuzzlib.main(TestOneInput)
