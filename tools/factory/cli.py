"""Command-line interface of the factory: `factory <command> ...`."""
from __future__ import annotations

import argparse
import importlib
import sys

from . import __version__
from .util import setup_console

# Each module exposes register(subparsers); missing optional modules are skipped so the
# CLI keeps working while a part of the factory is being repaired.
MODULES = [
    "setup_tools",
    "doctor",
    "game",
    "disasm",
    "decomp",
    "mods",
    "emu",
    "analyze",
    "ramsearch",
    "cycle",
    "debug",
    "ghidra",
    "selftest",
    "memory",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Fábrica de mods/recompilação de Nintendo DS — ROM → extração → mod/decomp → "
                    "emulador → captura → análise (ciclo autônomo).",
    )
    parser.add_argument("--version", action="version", version=f"factory {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<comando>")
    for name in MODULES:
        try:
            mod = importlib.import_module(f"factory.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"factory.{name}":
                continue
            raise
        mod.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("interrompido", file=sys.stderr)
        return 130
