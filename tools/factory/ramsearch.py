"""`factory ramsearch`: offline RAM search over Main RAM dumps (like a cheat search, but scriptable).

Dumps come from scenarios (`"ram_dumps": [1, 300]` → runs/<run>/ram/Main_RAM_f000001.bin) or from
`factory play` (F10 → ram/Main_RAM_mark01_f001234.bin). Conditions are numpy expressions over the dump
tags: `f1`, `f300`, `mark01` (any dump name after the domain prefix, without the _fNNNNNN suffix for marks):

  factory ramsearch runs/<run> --type u16 --where "f300 == 99" --where "f1 > f300"
  factory ramsearch runs/<run> --type s32 --where "f300 - f1 == -5"
  factory ramsearch runs/<run> --where "mark01 != mark02" --from cand.json --save cand.json

Addresses are reported as ARM9 bus addresses (Main RAM base 0x02000000) ready for scenario "watches".
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from . import paths
from .util import fail, info, table

TYPES = {"u8": "<u1", "s8": "<i1", "u16": "<u2", "s16": "<i2", "u32": "<u4", "s32": "<i4"}
SAFE = re.compile(r"^[\sA-Za-z0-9_+\-*/%()<>=!&|~.]+$")
MAIN_RAM_BASE = 0x02000000


def load_dumps(run_dir: Path, domain: str = "Main_RAM") -> dict[str, Path]:
    d = run_dir / "ram"
    out = {}
    for p in sorted(d.glob(f"{domain}_*.bin")):
        tag = p.stem[len(domain) + 1:]
        m = re.match(r"f0*(\d+)$", tag)
        if m:
            out[f"f{m.group(1)}"] = p
        else:
            m = re.match(r"(mark\d+)_f\d+$", tag)
            out[m.group(1) if m else tag] = p
    return out


def search(run_dir: Path, wheres: list[str], *, dtype: str = "u16", aligned: bool = True,
           start: Path | None = None, limit: int = 50) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    dumps = load_dumps(run_dir)
    if not dumps:
        fail(f"nenhum dump de RAM em {paths.rel(run_dir / 'ram')} (use \"ram_dumps\" no cenário ou F10 no play)")
    width = int(TYPES[dtype][-1])
    arrays: dict[str, np.ndarray] = {}
    for tag, p in dumps.items():
        raw = np.fromfile(p, dtype=np.uint8)
        if aligned:
            n = len(raw) // width * width
            arrays[tag] = raw[:n].view(TYPES[dtype]).astype(np.int64)
        else:  # every byte offset (slower, catches unaligned fields)
            n = len(raw) - width + 1
            stacked = np.lib.stride_tricks.sliding_window_view(raw, width)[:n]
            arrays[tag] = stacked.copy().view(TYPES[dtype]).reshape(-1).astype(np.int64)
    n = min(len(a) for a in arrays.values())
    arrays = {k: v[:n] for k, v in arrays.items()}
    mask = np.ones(n, dtype=bool)
    if start is not None:
        prev = json.loads(Path(start).read_text(encoding="utf-8"))
        keep = np.zeros(n, dtype=bool)
        step = width if aligned else 1
        idx = [((int(a, 16) - MAIN_RAM_BASE) // step) for a in prev.get("addresses", [])]
        idx = [i for i in idx if 0 <= i < n]
        keep[idx] = True
        mask &= keep
    for w in wheres:
        if not SAFE.match(w):
            fail(f"expressão não permitida: {w!r}")
        names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", w))
        unknown = names - set(arrays)
        if unknown:
            fail(f"tags desconhecidas {sorted(unknown)} (disponíveis: {', '.join(sorted(arrays))})")
        res = eval(w, {"__builtins__": {}}, arrays)  # noqa: S307 — restricted charset + names
        mask &= np.asarray(res, dtype=bool)
    idx = np.nonzero(mask)[0]
    return idx, arrays


def cmd_ramsearch(args) -> int:
    run_dir = Path(args.run)
    if not run_dir.exists():
        from .game import load_game
        g = load_game(args.game)
        run_dir = g.runs / args.run
    idx, arrays = search(run_dir, args.where or [], dtype=args.type, aligned=not args.unaligned,
                         start=Path(args.from_) if args.from_ else None)
    width = int(TYPES[args.type][-1])
    step = width if not args.unaligned else 1
    tags = sorted(arrays, key=lambda t: (not t.startswith("f"), int(t[1:]) if t[1:].isdigit() else 0, t))
    info(f"{len(idx)} candidato(s) ({args.type}, {'alinhado' if not args.unaligned else 'qualquer offset'})")
    rows = []
    for i in idx[: args.limit]:
        addr = MAIN_RAM_BASE + int(i) * step
        rows.append([f"0x{addr:08X}"] + [str(int(arrays[t][i])) for t in tags])
    if rows:
        print(table(rows, ["endereço", *tags]))
    if args.save:
        Path(args.save).write_text(json.dumps({"type": args.type, "where": args.where,
                                               "addresses": [f"0x{MAIN_RAM_BASE + int(i) * step:08X}" for i in idx]},
                                              indent=1), encoding="utf-8")
        info(f"candidatos salvos em {args.save} (refine com --from)")
    return 0 if len(idx) else 1


def register(sub) -> None:
    p = sub.add_parser("ramsearch", help="busca na RAM entre dumps (achar HP/pontos/flags p/ watches e patches)")
    p.add_argument("run", help="diretório do run (ou nome dentro de games/<ID>/runs)")
    p.add_argument("--game")
    p.add_argument("--type", choices=sorted(TYPES), default="u16")
    p.add_argument("--where", action="append", help='ex.: "f300 == 99"  "f1 > f300"  "mark02 - mark01 == 1"')
    p.add_argument("--unaligned", action="store_true")
    p.add_argument("--from", dest="from_", help="refinar a partir de candidatos salvos")
    p.add_argument("--save", help="salvar candidatos em JSON")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_ramsearch)
