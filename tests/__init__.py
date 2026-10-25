"""Unit tests that run anywhere, without the Windows toolchain: `python -m unittest discover -s tests -t .`.

The end-to-end check (emulators, compilers, Ghidra) is `factory selftest`, which needs `factory setup`.
"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
