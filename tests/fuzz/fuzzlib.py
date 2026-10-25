"""Driver comum dos alvos de fuzzing (tests/fuzz/fuzz_*.py).

Com o atheris (Linux x86-64: `pip install --require-hashes -r requirements.txt -r requirements-fuzz.txt`) é o
libFuzzer guiado por cobertura e aceita os argumentos dele. O primeiro diretório recebe as entradas novas:
    python tests/fuzz/fuzz_rom.py -max_total_time=60 work/fuzz/rom tests/fuzz/corpus/rom
Sem o atheris (Windows/macOS) roda o corpus e mutações aleatórias; serve para reproduzir um crash do CI:
    python tests/fuzz/fuzz_rom.py crash-0123abcd
"""

from __future__ import annotations

import hashlib
import os
import random
import sys
from contextlib import nullcontext
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

try:
    import atheris
except ImportError:  # sem wheel fora do Linux x86-64
    atheris = None


def instrumented():
    """Contexto para importar o código sob teste (instrumentado para cobertura quando há atheris)."""
    return atheris.instrument_imports(include=["factory"]) if atheris else nullcontext()


def _mutate(rng: random.Random, data: bytes) -> bytes:
    buf = bytearray(data)
    for _ in range(rng.randint(1, 8)):
        op = rng.randrange(4)
        if op == 0 and buf:                              # troca um byte
            buf[rng.randrange(len(buf))] = rng.randrange(256)
        elif op == 1:                                    # insere bytes
            i = rng.randint(0, len(buf))
            buf[i:i] = rng.randbytes(rng.randint(1, 8))
        elif op == 2 and buf:                            # apaga um trecho
            i = rng.randrange(len(buf))
            del buf[i:i + rng.randint(1, 8)]
        elif buf:                                        # duplica um trecho
            i = rng.randrange(len(buf))
            buf[i:i] = buf[i:i + rng.randint(1, 16)]
    return bytes(buf[:65536])


def _run(test_one_input, data: bytes) -> None:
    try:
        test_one_input(data)
    except BaseException:
        out = Path(f"crash-{hashlib.sha1(data).hexdigest()}")
        out.write_bytes(data)
        print(f"entrada que quebrou o alvo salva em {out}", file=sys.stderr)
        raise


def main(test_one_input) -> None:
    if atheris is not None:
        atheris.Setup(sys.argv, test_one_input)
        atheris.Fuzz()
        return
    seeds = []
    for arg in sys.argv[1:]:
        if arg.startswith("-"):
            continue
        p = Path(arg)
        files = sorted(f for f in p.iterdir() if f.is_file()) if p.is_dir() else [p]
        seeds += [f.read_bytes() for f in files]
    for data in seeds:
        _run(test_one_input, data)
    runs = int(os.environ.get("FUZZ_RUNS", "2000"))
    rng = random.Random(int(os.environ.get("FUZZ_SEED", "0")))
    pool = seeds or [b""]
    for _ in range(runs):
        _run(test_one_input, _mutate(rng, rng.choice(pool)))
    print(f"ok: {len(seeds)} entradas do corpus + {runs} mutações aleatórias (modo sem atheris)")
