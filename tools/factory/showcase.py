"""`factory selftest-media`: regenerate the README media (docs/img) from a real factory cycle.

Runs `factory cycle readme_demo --mod green` on the homebrew test ROM of the selftest (same savestate and
same A presses on the original and on the mod, applied in RAM by the hotpatch), then draws:
  • ciclo-selftest.gif — original | mod | difference (top screen), with frame number and RAM score;
  • ram-placar.png     — the score watch of both runs over time, the presses and the cycle's verdict.
Nothing here comes from a commercial game: the ROM is the selftest's own homebrew (tools/selftest/).
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import analyze, paths
from .util import info, write_json

SCENARIO = "readme_demo"
MOD = "green"
PRESSES = (10, 30, 50, 70)
SCORE_ADDR = "0x02100004"
DEMO = {
    "description": "README: mesmo savestate e mesmos toques em A no original e no mod green",
    "start": {"type": "state", "state": "after_three"},
    "frames": 100,
    "inputs": [{"frame": f, "hold": 4, "buttons": ["A"]} for f in PRESSES],
    "capture": {"all": True},
    "watches": [{"name": "placar", "addr": SCORE_ADDR, "type": "u32"}],
    "watch_every": 1,
    "expect": {"no_freeze": True, "no_black": True,
               "ram": [{"watch": "placar", "at": "end", "op": "==", "value": 3 + 2 * len(PRESSES)}],
               "notes": "fundo verde e barra de pontos amarela; o placar sobe 2 por toque"},
}

BG, PANEL_TXT, MUTED = (13, 17, 23), (230, 237, 243), (139, 148, 158)
SCREEN_W, SCREEN_H = 256, 192


def _font(size: int, *, bold: bool = False, mono: bool = False):
    if mono:
        names = ("consolab.ttf", "DejaVuSansMono-Bold.ttf") if bold else ("consola.ttf", "DejaVuSansMono.ttf")
    else:
        names = ("segoeuib.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf") if bold else \
            ("segoeui.ttf", "DejaVuSans.ttf", "arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _top_screen(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)[:SCREEN_H]


def _value_at(series: dict[int, float], frame: int) -> float | None:
    """RAM shown on screen at frame n was sampled at n-1 (one frame of display latency)."""
    for f in (frame - 1, frame):
        if f in series:
            return series[f]
    return None


def demo_gif(base_dir: Path, run_dir: Path, dest: Path, *, step: int = 2) -> Path:
    ram_b = dict(analyze.read_ram_csv(base_dir).get("placar", []))
    ram_m = dict(analyze.read_ram_csv(run_dir).get("placar", []))
    names = sorted(p.name for p in (run_dir / "frames").glob("f*.png") if (base_dir / "frames" / p.name).exists())
    pad, head, foot = 12, 30, 28
    width, height = 3 * SCREEN_W + 4 * pad, head + SCREEN_H + foot
    labels = ("ROM original", f"mod “{MOD}” (via hotpatch)", "diferença medida")
    f_label, f_foot = _font(15, bold=True), _font(14, mono=True)
    frames: list[Image.Image] = []
    for name in names[::step]:
        n = int(name[1:7])
        a, b = _top_screen(base_dir / "frames" / name), _top_screen(run_dir / "frames" / name)
        pct = 100.0 * float(analyze.changed_mask(a, b).mean())
        canvas = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(canvas)
        for i, (panel, label) in enumerate(zip((a, b, analyze.diff_image(a, b)), labels)):
            x = pad + i * (SCREEN_W + pad)
            canvas.paste(Image.fromarray(panel), (x, head))
            draw.text((x, 7), label, font=f_label, fill=PANEL_TXT)
        vb, vm = _value_at(ram_b, n), _value_at(ram_m, n)
        texts = (f"frame {n:3d} · placar {vb:.0f}" if vb is not None else f"frame {n:3d}",
                 f"frame {n:3d} · placar {vm:.0f}" if vm is not None else f"frame {n:3d}",
                 f"{pct:5.1f}% dos pixels mudaram")
        for i, text in enumerate(texts):
            draw.text((pad + i * (SCREEN_W + pad), head + SCREEN_H + 6), text, font=f_foot, fill=MUTED)
        frames.append(canvas)
    if not frames:
        raise RuntimeError(f"sem frames em comum entre {base_dir} e {run_dir}")
    # one shared palette: no colour flicker between frames
    sample = Image.new("RGB", (width, height * min(4, len(frames))))
    for i, fr in enumerate(frames[:: max(1, len(frames) // 4)][:4]):
        sample.paste(fr, (0, i * height))
    palette = sample.quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    quant = [fr.quantize(palette=palette, dither=Image.Dither.NONE) for fr in frames]
    durations = [33] * (len(quant) - 1) + [2200]
    dest.parent.mkdir(parents=True, exist_ok=True)
    quant[0].save(dest, save_all=True, append_images=quant[1:], duration=durations, loop=0, optimize=True)
    return dest


def _dashed_vline(draw: ImageDraw.ImageDraw, x: int, y0: int, y1: int, *, fill, width: int, dash: int) -> None:
    y = y0
    while y < y1:
        draw.line([(x, y), (x, min(y + dash, y1))], fill=fill, width=width)
        y += 2 * dash


def ram_chart(base_dir: Path, run_dir: Path, entry: dict, dest: Path, *, scale: int = 2) -> Path:
    s = scale
    series = {"ROM original": (analyze.read_ram_csv(base_dir).get("placar", []), (140, 149, 159)),
              f"mod “{MOD}”": (analyze.read_ram_csv(run_dir).get("placar", []), (207, 34, 46))}
    width, height = 900 * s, 420 * s
    left, right, top, bottom = 70 * s, 30 * s, 92 * s, 96 * s
    pw, ph = width - left - right, height - top - bottom
    max_f = max((pts[-1][0] for pts, _ in series.values() if pts), default=100)
    max_v = max((v for pts, _ in series.values() for _, v in pts), default=1)
    y_top = max(2, int(math.ceil((max_v + 1) / 2.0)) * 2)
    fx = lambda f: left + int(pw * f / max_f)          # noqa: E731
    fy = lambda v: top + ph - int(ph * v / y_top)      # noqa: E731
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    text, muted, grid, blue = (31, 35, 40), (89, 99, 110), (216, 222, 228), (9, 105, 218)
    f_title, f_sub, f_tick, f_foot = _font(19 * s, bold=True), _font(14 * s), _font(12 * s), _font(13 * s, mono=True)
    draw.text((left, 16 * s), f"Placar na RAM (u32 @ {SCORE_ADDR}) — original × mod", font=f_title, fill=text)
    draw.text((left, 44 * s), "mesmo savestate e mesmos toques em A nas duas ROMs; o mod entra na RAM pelo hotpatch",
              font=f_sub, fill=muted)
    for v in range(0, y_top + 1, 2):
        y = fy(v)
        draw.line([(left, y), (left + pw, y)], fill=grid, width=s)
        draw.text((left - 12 * s, y), str(v), font=f_tick, fill=muted, anchor="rm")
    for f in range(0, max_f + 1, 10):
        draw.text((fx(f), top + ph + 8 * s), str(f), font=f_tick, fill=muted, anchor="mt")
    draw.text((left + pw // 2, top + ph + 28 * s), "frame (depois de carregar o savestate)", font=f_tick, fill=muted,
              anchor="mt")
    for p in PRESSES:
        _dashed_vline(draw, fx(p), top, top + ph, fill=blue, width=s, dash=5 * s)
        draw.text((fx(p), top - 4 * s), "A", font=_font(13 * s, bold=True), fill=blue, anchor="mb")
    for _, (pts, color) in series.items():
        if not pts:
            continue
        line = [(fx(pts[0][0]), fy(pts[0][1]))]
        for (_, v0), (f1, v1) in zip(pts, pts[1:]):
            line += [(fx(f1), fy(v0)), (fx(f1), fy(v1))]
        draw.line(line, fill=color, width=3 * s, joint="curve")
        draw.text((left + pw + 4 * s, fy(pts[-1][1])), f"{pts[-1][1]:.0f}", font=f_tick, fill=color, anchor="lm")
    lx = left + 16 * s                                  # upper left: the score starts low, the area is empty
    box_w = 34 * s + max(int(draw.textlength(label, font=f_sub)) for label in series) + 14 * s
    draw.rounded_rectangle([lx - 10 * s, top + 4 * s, lx + box_w, top + 28 * s + 24 * s * (len(series) - 1)],
                           radius=6 * s, fill=(255, 255, 255), outline=grid, width=s)
    for i, (label, (_, color)) in enumerate(series.items()):
        y = top + 16 * s + i * 24 * s
        draw.line([(lx, y), (lx + 26 * s, y)], fill=color, width=4 * s)
        draw.text((lx + 34 * s, y), label, font=f_sub, fill=text, anchor="lm")
    draw.rectangle([left, top, left + pw, top + ph], outline=grid, width=s)
    verdict = (f"factory cycle {SCENARIO} --mod {MOD}  →  {entry['verdict']} · {entry['score']}/{entry['total']} "
               f"critérios · 1ª divergência visual no frame {entry.get('first_divergence')}")
    draw.text((left, height - 26 * s), verdict, font=f_foot, fill=text, anchor="lm")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, optimize=True)
    return dest


def cmd_selftest_media(args) -> int:
    from . import cycle as cycle_mod
    from . import game as game_mod
    from . import selftest

    work = selftest.OUT / "work"
    paths.GAMES, paths.ROMS = work / "games", work / "roms"
    g = game_mod.load_game(selftest.GAME_ID) if (paths.GAMES / selftest.GAME_ID / "game.json").exists() else None
    if g is None or not (g.states / "after_three.State").exists() or not (g.mods / MOD / "mod.json").exists():
        info("workspace do selftest ausente — rodando `factory selftest --quick` antes")
        selftest.cmd_selftest(argparse.Namespace(quick=True, ghidra=False, keep=False))
        g = game_mod.load_game(selftest.GAME_ID)
    write_json(g.scenarios / f"{SCENARIO}.json", DEMO)
    (g.mods / MOD / "iterations.jsonl").unlink(missing_ok=True)      # the README shows iteration #1
    entry = cycle_mod.cycle(g, SCENARIO, MOD, note="factory selftest-media", refresh_baseline=True)
    base_dir, run_dir = paths.ROOT / entry["baseline"], paths.ROOT / entry["run"]
    out = Path(args.out)
    gif = demo_gif(base_dir, run_dir, out / "ciclo-selftest.gif")
    png = ram_chart(base_dir, run_dir, entry, out / "ram-placar.png")
    info(f"{paths.rel(gif)} ({gif.stat().st_size // 1024} KB) · {paths.rel(png)} ({png.stat().st_size // 1024} KB)")
    return 0 if entry["verdict"] == "SUCESSO" else 1


def register(sub) -> None:
    p = sub.add_parser("selftest-media", help="gerar a mídia do README (GIF + gráfico) com um ciclo real na ROM de teste")
    p.add_argument("--out", default=str(paths.ROOT / "docs" / "img"))
    p.set_defaults(func=cmd_selftest_media)
