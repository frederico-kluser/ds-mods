"""dsd config parsing (symbols.txt / relocs.txt) and the byte-match oracle used by `factory decomp match`."""
import tempfile
import unittest
from pathlib import Path

from factory.dsdproj import parse_relocs, parse_symbols
from factory.mwcc import FuncBytes, compare, side_by_side

SYMBOLS = """\
score_step kind:function(arm,size=0x8) addr:0x02000a00   // comment
func_02000800 kind:function(arm,size=0x110) addr:0x02000800
helper kind:function(thumb,size=0x6) addr:0x02000c00
data_02100000 kind:data(any) addr:0x02100000 local
not a symbol line
"""

RELOCS = """\
from:0x020008cc kind:arm_call to:0x02000a00 module:main
from:0x02000b14 kind:load to:0x02100000 add:0x4 module:main
from:0x02000b18 kind:load to:0x02080000 add:-8 module:overlays(0,1)
"""


class DsdConfigTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "symbols.txt").write_text(SYMBOLS, encoding="utf-8")
        (self.dir / "relocs.txt").write_text(RELOCS, encoding="utf-8")

    def test_symbols_sorted_with_attributes(self):
        syms = parse_symbols(self.dir / "symbols.txt", "main")
        self.assertEqual([s.name for s in syms], ["func_02000800", "score_step", "helper", "data_02100000"])
        helper = syms[2]
        self.assertEqual((helper.kind, helper.mode, helper.size, helper.addr), ("function", "thumb", 6, 0x02000C00))
        self.assertEqual(syms[3].flags, ["local"])
        self.assertEqual(parse_symbols(self.dir / "missing.txt", "main"), [])

    def test_relocs(self):
        rel = parse_relocs(self.dir / "relocs.txt", "main")
        self.assertEqual([(r.src, r.kind, r.dst, r.addend, r.target_module) for r in rel],
                         [(0x020008CC, "arm_call", 0x02000A00, 0, "main"),
                          (0x02000B14, "load", 0x02100000, 4, "main"),
                          (0x02000B18, "load", 0x02080000, -8, "overlays(0,1)")])


def fb(hexstr: str, relocs: dict | None = None) -> FuncBytes:
    return FuncBytes(bytearray.fromhex(hexstr), relocs or {})


ADD = "010080e0"            # add r0, r0, r1
ADD_LSL = "810080e0"        # add r0, r0, r1, lsl #1
BX_LR = "1eff2fe1"          # bx lr
BL_A, BL_B = "000000eb", "fe0000eb"   # bl with different (unrelocated) offsets


class MatchOracleTest(unittest.TestCase):
    def test_identical(self):
        self.assertEqual(compare(fb(ADD + BX_LR), fb(ADD + BX_LR)), (True, []))

    def test_byte_and_size_differences(self):
        ok, problems = compare(fb(ADD_LSL + BX_LR), fb(ADD + BX_LR))
        self.assertFalse(ok)
        self.assertEqual(problems, ["bytes diferem a partir de +0x0"])
        ok, problems = compare(fb(ADD + BX_LR + ADD), fb(ADD + BX_LR))
        self.assertTrue(any(p.startswith("tamanho difere") for p in problems))

    def test_relocations_mask_their_bytes(self):
        mine = fb(BL_A + BX_LR, {0: ("score_step", "R_ARM_PC24")})
        orig = fb(BL_B + BX_LR, {0: ("score_step", "R_ARM_PC24")})
        self.assertEqual(compare(mine, orig), (True, []))
        anon = fb(BL_B + BX_LR, {0: ("@1234", "R_ARM_PC24")})            # anonymous target is tolerated
        self.assertTrue(compare(mine, anon)[0])

    def test_relocation_mismatches(self):
        mine = fb(BL_A + BX_LR, {0: ("score_step", "R_ARM_PC24")})
        self.assertIn("+0x0: alvo score_step ≠ original other", compare(mine, fb(BL_A + BX_LR, {0: ("other", "R_ARM_PC24")}))[1])
        self.assertIn("+0x0: tipo de relocação R_ARM_PC24 ≠ R_ARM_CALL",
                      compare(mine, fb(BL_A + BX_LR, {0: ("score_step", "R_ARM_CALL")}))[1])
        self.assertIn("+0x0: relocação só em seu código", compare(mine, fb(BL_A + BX_LR))[1])

    def test_side_by_side_marks_differences(self):
        text = side_by_side(fb(ADD + BX_LR), fb(ADD_LSL + BX_LR), thumb=False)
        lines = text.splitlines()
        self.assertIn("SEU CÓDIGO", lines[0])
        self.assertIn("!!", lines[1])                     # add r0, r0, r1  vs  add r0, r0, r1, lsl #1
        self.assertNotIn("!!", lines[2])                  # bx lr on both sides


if __name__ == "__main__":
    unittest.main()
