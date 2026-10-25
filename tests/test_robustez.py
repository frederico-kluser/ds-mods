"""Regressões do fuzzing (tests/fuzz/): entrada malformada vira erro claro (SystemExit via fail) ou é
ignorada — nunca um traceback."""
import contextlib
import io
import struct
import unittest

import numpy as np

from factory import analyze, dsdproj, emu, ndsbuild, ramsearch, rom


def quiet_exit(test: unittest.TestCase, fn, *args, **kwargs):
    with contextlib.redirect_stderr(io.StringIO()), test.assertRaises(SystemExit):
        fn(*args, **kwargs)


class RomTest(unittest.TestCase):
    def test_fat_entry_ending_before_start_is_not_a_file(self):
        data = bytearray(ndsbuild.build_rom(
            arm9=bytes(0x200), arm7=bytes(0x80), title="T", gamecode="TEST", arm9_entry=0x02000000,
            arm7_entry=0x02380000, arm7_ram=0x02380000, module_params_offset=None,
            overlays=[{"id": 0, "ram": 0x02080000, "data": bytes(32)}]))
        info = rom.inspect_bytes(bytes(data))
        fid = info.arm9_overlays[0].file_id
        struct.pack_into("<2I", data, info.fat_offset + 8 * fid, 0x7000, 0x10)
        ov = rom.inspect_bytes(bytes(data)).arm9_overlays[0]
        self.assertEqual((ov.rom_offset, ov.rom_size), (0, 0))


class DsdTest(unittest.TestCase):
    def test_bad_sizes_are_unknown(self):
        syms = dsdproj.parse_symbols_text("a kind:function(arm,size=0xZZ) addr:0x02000000\n"
                                          "b kind:function(arm,size=-1) addr:0x02000010\n", "main")
        self.assertEqual([s.size for s in syms], [None, None])

    def test_addends_in_any_base(self):
        rel = dsdproj.parse_relocs_text("from:0x1 kind:load to:0x2 add:007 module:main\n"
                                        "from:0x3 kind:load to:0x4 add:-0x10 module:main\n", "main")
        self.assertEqual([r.addend for r in rel], [7, -16])


class RamSearchTest(unittest.TestCase):
    ARRAYS = {"f1": np.arange(8, dtype=np.int64), "mark01": np.zeros(8, dtype=np.int64)}

    def test_broken_conditions_fail_cleanly(self):
        for where in ("mark01 != ", "f1 ** 2 == 4", "f1.f1 == 1", "(f1 == 1", "f1 == 1" * 80):
            with self.subTest(where=where[:20]):
                quiet_exit(self, ramsearch.where_mask, [where], dict(self.ARRAYS), 8)

    def test_valid_condition(self):
        self.assertEqual(ramsearch.where_mask(["(f1 > 2) & (mark01 == 0)"], dict(self.ARRAYS), 8).sum(), 5)


class ScenarioTest(unittest.TestCase):
    def test_bad_inputs(self):
        for items in ({"frame": 1}, [{"frame": "x"}], [{"hold": 2}], [{"frame": 1, "buttons": [["A"]]}],
                      [{"frame": 1, "buttons": "A"}], [{"frame": 1, "touch": [1]}], [{"frame": float("inf")}], ["A"]):
            with self.subTest(items=items):
                quiet_exit(self, emu._normalize_inputs, items)

    def test_bad_watches(self):
        for items in ([{"name": "a"}], [{"name": "a", "addr": 1.5}], [{"name": "a", "addr": "0x10", "type": "f32"}],
                      [{"name": "a", "addr": -4}], [{"name": 3, "addr": 4}], "watch"):
            with self.subTest(items=items):
                quiet_exit(self, emu._normalize_watches, items)

    def test_none_means_empty(self):
        self.assertEqual((emu._normalize_inputs(None), emu._normalize_watches(None)), ([], []))


class ExpectTest(unittest.TestCase):
    FRAMES = [analyze.Frame(index=i, time=i / 60, path=None, img=np.zeros((8, 8, 3), dtype=np.uint8)) for i in range(3)]

    def evaluate(self, expect):
        return analyze.evaluate_expect(expect, stats=analyze.frame_stats(self.FRAMES), ram={"p": [(0, 1.0)]},
                                       comparison=None, frames=self.FRAMES, ref_frames=self.FRAMES)

    def test_bad_criteria_fail_cleanly(self):
        for expect in ([1], {"no_freeze": True, "freeze_seconds": "x"}, {"ram": {"watch": "p"}},
                       {"ram": [{"watch": "p", "op": "~=", "value": 1}]}, {"ram": [{"watch": ["p"], "value": 1}]},
                       {"ram": [{"watch": "p", "value": "muito"}]}, {"ram": [{"watch": "p", "value": 1, "at": "meio"}]},
                       {"visual": [{"region": [0, 0, 1]}]}, {"visual": [{"min_changed_pct": float("nan")}]}):
            with self.subTest(expect=expect):
                quiet_exit(self, self.evaluate, expect)

    def test_region_outside_the_image_is_not_graded(self):
        res = self.evaluate({"visual": [{"frame": 1, "region": [50, 50, 60, 60]}]})
        self.assertIsNone(res["results"][0]["passed"])
        self.assertIn("fora da imagem", res["results"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
