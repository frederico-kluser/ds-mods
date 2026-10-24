"""Project memory (CoALA): the SQLite base stays local; git tracks its canonical JSONL dump (`track-dump`).

`factory memory dump` refreshes the dump before a commit, `factory memory restore` rebuilds the base on a
fresh clone (import of the dump + sync of docs/code) and `factory doctor` warns when the dump is stale.
"""
from __future__ import annotations

import subprocess
import sys

from . import paths
from .util import fail, info

COALA_DIR = paths.ROOT / ".agents" / "ds-mods-coala-agent-skill"
ENGINE = COALA_DIR / "scripts" / "coala.py"
DB = COALA_DIR / "memory" / "coala.sqlite"
DUMP = COALA_DIR / "memory" / "coala.canonical.jsonl"


def _coala(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ENGINE), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(paths.ROOT))


def _last_line(p: subprocess.CompletedProcess) -> str:
    out = (p.stdout or p.stderr or "").strip().splitlines()
    return out[-1] if out else f"exit {p.returncode}"


def dump_state() -> tuple[str, str]:
    """('ok' | 'stale' | 'absent' | 'error', detail) — does the tracked dump match the local base?"""
    if not ENGINE.exists():
        return "error", f"motor ausente: {paths.rel(ENGINE)}"
    if not DB.exists():
        return "absent", "base local ausente — rode `factory memory restore`"
    tmp = paths.RUNTIME / "coala.check.jsonl"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    p = _coala("export", "--format", "jsonl", "--out", str(tmp))
    if p.returncode != 0:
        return "error", _last_line(p)
    same = DUMP.exists() and tmp.read_bytes() == DUMP.read_bytes()
    tmp.unlink(missing_ok=True)
    if same:
        return "ok", paths.rel(DUMP)
    return "stale", "dump desatualizado — rode `factory memory dump` antes de commitar"


def cmd_memory(args) -> int:
    if not ENGINE.exists():
        fail(f"motor CoALA ausente: {paths.rel(ENGINE)}")
    if args.memory_cmd == "dump":
        p = _coala("export", "--format", "jsonl", "--out", str(DUMP))
        if p.returncode != 0:
            fail(_last_line(p))
        info(f"{_last_line(p)}")
        return 0
    if args.memory_cmd == "restore":
        if DB.exists() and not args.force:
            fail(f"a base já existe ({paths.rel(DB)}) — use --force para importar o dump por cima")
        if not DUMP.exists():
            fail(f"dump ausente: {paths.rel(DUMP)}")
        sync = ("sync", "--config", str(COALA_DIR / "ingest.json"), "--root", str(paths.ROOT))
        for step in (("init",), ("import", "--jsonl", str(DUMP)), sync):
            p = _coala(*step)
            if p.returncode != 0:
                fail(f"coala {step[0]}: {_last_line(p)}")
            info(f"coala {step[0]}: {_last_line(p)}")
        return 0
    state, detail = dump_state()
    print(f"{state}: {detail}")
    return 0 if state == "ok" else 1


def register(sub) -> None:
    p = sub.add_parser("memory", help="memória CoALA no git: dump | restore | status")
    s = p.add_subparsers(dest="memory_cmd", metavar="<ação>", required=True)
    s.add_parser("dump", help="exportar a base local para o dump versionado (antes de commitar)")
    q = s.add_parser("restore", help="clone novo: recriar a base a partir do dump + sync de docs/código")
    q.add_argument("--force", action="store_true")
    s.add_parser("status", help="o dump versionado está em dia com a base local?")
    p.set_defaults(func=cmd_memory)
