"""Hotpatch hunks: what the Lua runner re-applies in RAM after loading a savestate (mods.compute_hotpatch)."""
import tempfile
import unittest
from pathlib import Path

import yaml

from factory.mods import WorkTree, compute_hotpatch


def make_tree(root: Path, arm9: bytes, ov0: bytes, arm7: bytes = bytes(16)) -> WorkTree:
    for d in ("arm9", "arm7", "arm9_overlays"):
        (root / d).mkdir(parents=True)
    config = {"arm9_bin": "arm9/arm9.bin", "arm9_config": "arm9/arm9.yaml", "arm7_bin": "arm7/arm7.bin",
              "arm7_config": "arm7/arm7.yaml", "arm9_overlays": "arm9_overlays/overlays.yaml", "files_dir": "files/"}
    (root / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (root / "arm9" / "arm9.yaml").write_text("base_address: 0x02000000\n", encoding="utf-8")
    (root / "arm7" / "arm7.yaml").write_text("base_address: 0x02380000\n", encoding="utf-8")
    overlays = {"overlays": [{"id": 0, "base_address": 0x02080000, "file_name": "ov000.bin"}]}
    (root / "arm9_overlays" / "overlays.yaml").write_text(yaml.safe_dump(overlays), encoding="utf-8")
    (root / "arm9" / "arm9.bin").write_bytes(arm9)
    (root / "arm7" / "arm7.bin").write_bytes(arm7)
    (root / "arm9_overlays" / "ov000.bin").write_bytes(ov0)
    return WorkTree(root)


class HotpatchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        base_arm9, base_ov0 = bytes(64), bytes(range(32))
        arm9, ov0 = bytearray(base_arm9), bytearray(base_ov0)
        arm9[8], arm9[12], arm9[40] = 0xAA, 0xBB, 0xCC    # 8 and 12 merge (gap ≤ 8); 40 is separate
        arm9 += b"\x01\x02\x03\x04"                        # appended code: never hotpatched
        ov0[2], ov0[20] = 0xFF, 0xEE                       # inside / outside the overlay's guard bytes
        base = make_tree(root / "base", base_arm9, base_ov0)
        mod = make_tree(root / "mod", bytes(arm9), bytes(ov0), arm7=b"\x01" * 16)   # arm7 is not RAM-patched
        self.hunks = compute_hotpatch(base, mod)

    def test_arm9_hunks_are_merged_by_gap(self):
        arm9 = [h for h in self.hunks if h["module"] == "arm9"]
        self.assertEqual([(h["addr"], h["orig"], h["new"]) for h in arm9],
                         [("0x02000008", "0000000000", "AA000000BB"), ("0x02000028", "00", "CC")])
        self.assertFalse(any(h.get("appended") for h in self.hunks))

    def test_overlay_hunks_are_optional_and_guarded(self):
        ov = {h["addr"]: h for h in self.hunks if h["module"] == "ov0"}
        self.assertEqual(set(ov), {"0x02080002", "0x02080014"})
        self.assertTrue(all(h["optional"] for h in ov.values()))
        self.assertNotIn("guard", ov["0x02080002"])                 # would overlap the guard itself
        self.assertEqual(ov["0x02080014"]["guard"], {"addr": "0x02080000", "bytes": bytes(range(16)).hex().upper()})

    def test_arm7_is_ignored(self):
        self.assertNotIn("arm7", {h["module"] for h in self.hunks})


if __name__ == "__main__":
    unittest.main()
