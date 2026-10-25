"""Frame metrics, freeze/black detection, A/B comparison and expectation grading (factory analyze/cycle)."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from factory import analyze
from factory.analyze import Frame


def solid(color, h: int = 192, w: int = 256) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color
    return img


def frame(i: int, img: np.ndarray) -> Frame:
    return Frame(index=i, time=i / 60.0, path=None, img=img)


BLUE, GREEN = (0, 0, 255), (0, 255, 0)


class MetricsTest(unittest.TestCase):
    def test_mean_abs_diff_extremes(self):
        self.assertEqual(analyze.mean_abs_diff(solid(0), solid(0)), 0.0)
        self.assertAlmostEqual(analyze.mean_abs_diff(solid(0), solid(255)), 1.0)

    def test_changed_mask_threshold(self):
        a = solid(100)
        b = a.copy()
        b[0, 0] = 100 + analyze.CHANGE_THRESH           # exactly the threshold: not a change
        b[1, 1] = 100 + analyze.CHANGE_THRESH + 1
        m = analyze.changed_mask(a, b)
        self.assertEqual((bool(m[0, 0]), bool(m[1, 1]), int(m.sum())), (False, True, 1))

    def test_ssim(self):
        rng = np.random.default_rng(7)
        a = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
        b = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
        self.assertAlmostEqual(analyze.ssim(a, a), 1.0, places=5)
        self.assertLess(analyze.ssim(a, b), 0.2)

    def test_dhash_and_bbox(self):
        ramp = np.tile(np.arange(256, dtype=np.uint8), (64, 1))
        img = np.stack([ramp] * 3, axis=2)
        self.assertEqual(analyze.dhash(img), analyze.dhash(img.copy()))
        self.assertNotEqual(analyze.dhash(img), analyze.dhash(img[:, ::-1].copy()))
        mask = np.zeros((10, 10), dtype=bool)
        mask[2:5, 3:7] = True
        self.assertEqual(analyze.bbox(mask), [3, 2, 7, 5])
        self.assertIsNone(analyze.bbox(np.zeros((4, 4), dtype=bool)))

    def test_ds_screen_regions(self):
        self.assertEqual(analyze.screen_regions(solid(0, 384, 256))["bottom"], (0, 192, 256, 384))
        self.assertIn("full", analyze.screen_regions(solid(0, 100, 100)))


class FrameStatsTest(unittest.TestCase):
    def test_freeze_and_black_frames(self):
        frames = []
        for i in range(10):                              # a bar moving every frame
            img = solid(BLUE)
            img[i * 4:i * 4 + 4] = 255
            frames.append(frame(i, img))
        frozen = frames[-1].img
        frames += [frame(i, frozen.copy()) for i in range(10, 200)]   # ~3 s without any change
        frames.append(frame(200, solid(0)))
        st = analyze.frame_stats(frames)
        self.assertEqual(st["black_frames"], [200])
        self.assertEqual(len(st["freezes"]), 1)
        fr = st["freezes"][0]
        self.assertEqual((fr["from"], fr["to"]), (9, 199))
        self.assertGreaterEqual(fr["seconds"], 3.0)


class CompareTest(unittest.TestCase):
    def test_first_divergence(self):
        ref = [frame(i, solid(BLUE)) for i in range(5)]
        cur = [frame(i, solid(BLUE) if i < 3 else solid(GREEN)) for i in range(5)]
        summary, pairs = analyze.compare_frames(cur, ref)
        self.assertEqual(len(pairs), 5)
        self.assertEqual((summary["identical_pairs"], summary["max_changed_pct"]), (3, 100.0))
        self.assertEqual(summary["first_divergence"]["frame"], 3)

    def test_ram_diff(self):
        ref = {"score": [(1, 3.0), (2, 3.0), (3, 4.0)]}
        cur = {"score": [(1, 3.0), (2, 5.0), (3, 7.0)]}
        d = analyze.ram_diff(cur, ref)["score"]
        self.assertEqual(d["first_diff"], {"frame": 2, "ref": 3.0, "cur": 5.0})
        self.assertEqual((d["frames_different"], d["ref_end"], d["cur_end"]), (2, 4.0, 7.0))

    def test_read_ram_csv_skips_non_numeric(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ram.csv").write_text("frame,score,label\n1,3,x\n2,4,y\n", encoding="utf-8")
            ram = analyze.read_ram_csv(Path(d))
        self.assertEqual(ram, {"score": [(1, 3.0), (2, 4.0)]})


class ExpectTest(unittest.TestCase):
    def test_ram_checks_and_score(self):
        frames = [frame(i, solid(BLUE)) for i in range(3)]
        st = analyze.frame_stats(frames)
        ram = {"score": [(1, 3.0), (50, 5.0), (100, 7.0)]}
        exp = {"no_freeze": True, "no_black": True,
               "ram": [{"watch": "score", "at": "end", "op": "==", "value": 7},
                       {"watch": "score", "at": 50, "op": ">", "value": 5},
                       {"watch": "score", "at": "max", "op": ">=", "value": 7}]}
        res = analyze.evaluate_expect(exp, stats=st, ram=ram, comparison=None, frames=frames, ref_frames=None)
        self.assertEqual((res["score"], res["total"], res["passed"]), (4, 5, False))

    def test_visual_region(self):
        ref = [frame(10, solid(BLUE))]
        img = solid(BLUE)
        img[:96] = GREEN                                  # only the top half changes
        cur = [frame(10, img)]
        exp = {"visual": [{"frame": 10, "region": [0, 0, 256, 96], "min_changed_pct": 90},
                          {"frame": 10, "region": [0, 96, 256, 192], "max_changed_pct": 1}]}
        res = analyze.evaluate_expect(exp, stats=analyze.frame_stats(cur), ram={}, comparison=None,
                                      frames=cur, ref_frames=ref)
        self.assertEqual((res["score"], res["passed"]), (2, True))

    def test_no_expectations(self):
        self.assertIsNone(analyze.evaluate_expect(None, stats={}, ram={}, comparison=None, frames=[], ref_frames=None))


if __name__ == "__main__":
    unittest.main()
