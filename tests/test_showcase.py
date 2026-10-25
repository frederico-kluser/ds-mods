"""README media drawing (factory selftest-media) on synthetic runs — no emulator needed."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from factory import showcase


def fake_run(root: Path, color, scores) -> Path:
    (root / "frames").mkdir(parents=True)
    for n in range(len(scores)):
        img = np.zeros((384, 256, 3), dtype=np.uint8)
        img[:192] = color
        img[180:184, : 8 * int(scores[n])] = 255
        Image.fromarray(img).save(root / "frames" / f"f{n:06d}.png")
    rows = "\n".join(f"{n},{s}" for n, s in enumerate(scores))
    (root / "ram.csv").write_text("frame,placar\n" + rows + "\n", encoding="utf-8")
    return root


class ShowcaseTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.base = fake_run(self.root / "base", (0, 0, 255), [3, 3, 4, 4])
        self.mod = fake_run(self.root / "mod", (0, 255, 0), [3, 3, 5, 5])

    def test_gif_has_one_frame_per_sample(self):
        gif = showcase.demo_gif(self.base, self.mod, self.root / "out" / "demo.gif", step=1)
        with Image.open(gif) as im:
            self.assertEqual(im.n_frames, 4)
            self.assertEqual(im.size, (3 * showcase.SCREEN_W + 4 * 12, 30 + showcase.SCREEN_H + 28))

    def test_chart_renders(self):
        entry = {"verdict": "SUCESSO", "score": 3, "total": 3, "first_divergence": 1}
        png = showcase.ram_chart(self.base, self.mod, entry, self.root / "out" / "chart.png", scale=1)
        with Image.open(png) as im:
            self.assertEqual(im.size, (900, 420))

    def test_value_at_uses_previous_frame(self):
        self.assertEqual(showcase._value_at({4: 1.0, 5: 2.0}, 5), 1.0)
        self.assertEqual(showcase._value_at({5: 2.0}, 5), 2.0)
        self.assertIsNone(showcase._value_at({}, 5))


if __name__ == "__main__":
    unittest.main()
