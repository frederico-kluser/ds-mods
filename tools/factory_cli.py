"""Entry point of the DS mod/decomp factory CLI (use ./factory, factory.cmd or factory.ps1)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from factory.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
