"""Offline RAM search over dumps (factory ramsearch)."""
import contextlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from factory import ramsearch
from factory.ramsearch import MAIN_RAM_BASE


class RamSearchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name)
        (self.run_dir / "ram").mkdir()
        a, b = bytearray(0x1000), bytearray(0x1000)
        struct.pack_into("<I", b, 0x104, 3)              # score 0 → 3
        struct.pack_into("<H", a, 0x200, 99)             # lives 99 → 98
        struct.pack_into("<H", b, 0x200, 98)
        struct.pack_into("<H", b, 0x301, 4660)           # unaligned field
        for name, data in (("f000001", a), ("f000150", b), ("mark01_f000077", b)):
            (self.run_dir / "ram" / f"Main_RAM_{name}.bin").write_bytes(bytes(data))

    def search(self, *wheres, **kw):
        idx, _ = ramsearch.search(self.run_dir, list(wheres), **kw)
        step = int(ramsearch.TYPES[kw.get("dtype", "u16")][-1]) if kw.get("aligned", True) else 1
        return [MAIN_RAM_BASE + step * int(i) for i in idx]

    def test_dump_tags(self):
        self.assertEqual(set(ramsearch.load_dumps(self.run_dir)), {"f1", "f150", "mark01"})

    def test_exact_values(self):
        self.assertEqual(self.search("f1 == 0", "f150 == 3", dtype="u32"), [0x02000104])

    def test_relative_change(self):
        self.assertEqual(self.search("f150 - f1 == -1", dtype="u16"), [0x02000200])

    def test_unaligned(self):
        self.assertEqual(self.search("f150 == 4660", "f1 == 0", dtype="u16", aligned=False), [0x02000301])

    def test_narrow_from_previous_candidates(self):
        cand = self.run_dir / "cand.json"
        cand.write_text(json.dumps({"addresses": ["0x02000104"]}), encoding="utf-8")
        self.assertEqual(self.search("mark01 >= 0", dtype="u32", start=cand), [0x02000104])

    def test_rejects_unsafe_or_unknown(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for where in ("__import__('os')", "f999 == 1"):
                with self.subTest(where=where), self.assertRaises(SystemExit):
                    ramsearch.search(self.run_dir, [where])


if __name__ == "__main__":
    unittest.main()
