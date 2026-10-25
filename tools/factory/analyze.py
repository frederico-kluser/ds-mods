"""Frame / video analysis — the "eyes" of the autonomous loop.

Inputs
  * a run directory produced by `factory run` (frames/*.png + ram.csv + run.json), or
  * any video (mp4/mkv/avi/webm/gif — e.g. an OBS or phone recording), or a folder of images.

Outputs (written next to the input, under analysis/ or <video>.analysis/)
  * report.json   — metrics, freezes, black frames, A/B comparison, expectation verdict
  * report.md     — short human/agent summary (what to look at, PASS/FAIL per criterion)
  * contact.png   — grid of sampled frames with frame/time labels (the agent reads THIS image)
  * compare.png   — rows of [referência | atual | diff] for matching frames (when --ref is given)
  * timeline.png  — motion (frame-to-frame change) and diff-vs-ref over time
Images are kept small on purpose: DS frames are 256x384, so a 4x3 grid is ~1024x1152 px.
"""
from __future__ import annotations

import csv
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import paths
from .util import fail, info, read_json, write_json

FREEZE_EPS = 0.002          # mean abs diff (0..1) below which two frames are "the same"
CHANGE_THRESH = 24          # per-pixel max channel delta (0..255) that counts as "changed"


# ── frame loading ─────────────────────────────────────────────────────────────

@dataclass
class Frame:
    index: int              # emulator frame (runs) or sample index (videos)
    time: float             # seconds
    path: Path | None
    img: np.ndarray         # HxWx3 uint8


def _load_png(p: Path) -> np.ndarray:
    with Image.open(p) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)


def frames_from_run(run_dir: Path) -> list[Frame]:
    out = []
    for p in sorted((run_dir / "frames").glob("f*.png")):
        try:
            idx = int(p.stem[1:])
        except ValueError:
            continue
        out.append(Frame(index=idx, time=idx / 60.0, path=p, img=_load_png(p)))
    return out


def frames_from_dir(d: Path) -> list[Frame]:
    out = []
    files = sorted(p for p in d.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".webp"))
    for i, p in enumerate(files):
        out.append(Frame(index=i, time=float(i), path=p, img=_load_png(p)))
    return out


def probe_video(path: Path) -> dict:
    """Duration/fps/size via ffmpeg -i (ffprobe is not bundled with imageio-ffmpeg)."""
    p = subprocess.run([str(paths.ffmpeg()), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    txt = p.stderr
    info_ = {"duration": None, "fps": None, "width": None, "height": None}
    import re
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", txt)
    if m:
        info_["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"(\d{2,5})x(\d{2,5})[^\n]*?(\d+(?:\.\d+)?) fps", txt)
    if m:
        info_["width"], info_["height"], info_["fps"] = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return info_


def frames_from_video(path: Path, out_dir: Path, *, fps: float | None = None, max_frames: int = 600,
                      scene: float | None = None) -> list[Frame]:
    """Sample a video with ffmpeg into out_dir/frames (uniform fps, or scene-change keyframes)."""
    meta = probe_video(path)
    dur = meta.get("duration") or 0
    if fps is None:
        fps = 4.0 if not dur else max(0.5, min(10.0, max_frames / max(dur, 1)))
    fdir = out_dir / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    for old in fdir.glob("v*.png"):
        old.unlink()
    if scene:
        vf = f"select='gt(scene,{scene})+eq(n,0)',showinfo"
        args = ["-vf", vf, "-vsync", "vfr"]
    else:
        args = ["-vf", f"fps={fps}"]
    cmd = [str(paths.ffmpeg()), "-hide_banner", "-loglevel", "info", "-y", "-i", str(path), *args,
           "-frames:v", str(max_frames), str(fdir / "v%06d.png")]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        fail(f"ffmpeg não conseguiu ler {path}: {p.stderr.strip()[-400:]}")
    times: list[float] = []
    if scene:
        import re
        times = [float(t) for t in re.findall(r"pts_time:([\d.]+)", p.stderr)]
    frames = []
    for i, f in enumerate(sorted(fdir.glob("v*.png"))):
        t = times[i] if i < len(times) else i / fps
        frames.append(Frame(index=i, time=t, path=f, img=_load_png(f)))
    return frames


# ── metrics ───────────────────────────────────────────────────────────────────

def _gray(img: np.ndarray) -> np.ndarray:
    return (img[..., 0] * 0.299 + img[..., 1] * 0.587 + img[..., 2] * 0.114).astype(np.float32)


def mean_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        b = np.asarray(Image.fromarray(b).resize((a.shape[1], a.shape[0])))
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean() / 255.0)


def changed_mask(a: np.ndarray, b: np.ndarray, thresh: int = CHANGE_THRESH) -> np.ndarray:
    if a.shape != b.shape:
        b = np.asarray(Image.fromarray(b).resize((a.shape[1], a.shape[0])))
    return np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > thresh


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Global-window SSIM on luminance (8x8 blocks averaged) — good enough to rank similarity."""
    x, y = _gray(a), _gray(b)
    if x.shape != y.shape:
        y = np.asarray(Image.fromarray(y.astype(np.uint8)).resize((x.shape[1], x.shape[0]))).astype(np.float32)
    h, w = (x.shape[0] // 8) * 8, (x.shape[1] // 8) * 8
    if h == 0 or w == 0:
        return 1.0
    x = x[:h, :w].reshape(h // 8, 8, w // 8, 8).swapaxes(1, 2).reshape(-1, 64)
    y = y[:h, :w].reshape(h // 8, 8, w // 8, 8).swapaxes(1, 2).reshape(-1, 64)
    mx, my = x.mean(1), y.mean(1)
    vx, vy = x.var(1), y.var(1)
    cov = ((x - mx[:, None]) * (y - my[:, None])).mean(1)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    s = ((2 * mx * my + c1) * (2 * cov + c2)) / ((mx ** 2 + my ** 2 + c1) * (vx + vy + c2))
    return float(s.mean())


def dhash(img: np.ndarray, size: int = 8) -> int:
    g = Image.fromarray(img).convert("L").resize((size + 1, size), Image.BILINEAR)
    a = np.asarray(g, dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def bbox(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def screen_regions(img: np.ndarray) -> dict[str, tuple[int, int, int, int]]:
    """DS layout helpers: emulator captures are 256x384 (top screen above the bottom one)."""
    h, w = img.shape[:2]
    if (w, h) == (256, 384):
        return {"top": (0, 0, 256, 192), "bottom": (0, 192, 256, 384)}
    return {"full": (0, 0, w, h)}


def frame_stats(frames: list[Frame]) -> dict:
    lum = [float(_gray(f.img).mean() / 255.0) for f in frames]
    motion = [0.0] + [mean_abs_diff(frames[i - 1].img, frames[i].img) for i in range(1, len(frames))]
    black = [f.index for f, y in zip(frames, lum) if y < 0.02]
    white = [f.index for f, y in zip(frames, lum) if y > 0.98]
    # freeze segments: consecutive samples with ~no change
    freezes = []
    start = None
    for i in range(1, len(frames)):
        if motion[i] < FREEZE_EPS:
            start = i - 1 if start is None else start
        else:
            if start is not None:
                freezes.append((start, i - 1))
            start = None
    if start is not None:
        freezes.append((start, len(frames) - 1))
    fr_out = []
    for s, e in freezes:
        span_frames = frames[e].index - frames[s].index
        span_time = frames[e].time - frames[s].time
        if e - s >= 2:
            fr_out.append({"from": frames[s].index, "to": frames[e].index, "frames": span_frames,
                           "seconds": round(span_time, 2), "samples": e - s + 1})
    uniq = len({dhash(f.img) for f in frames})
    return {"luminance": lum, "motion": motion, "black_frames": black, "white_frames": white,
            "freezes": fr_out, "unique_frames": uniq}


# ── images for the agent ──────────────────────────────────────────────────────

def _font(size: int = 12):
    for name in ("consola.ttf", "DejaVuSansMono.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def contact_sheet(frames: list[Frame], dest: Path, *, cols: int = 4, max_tiles: int = 12, title: str = "",
                  scale: float = 1.0, is_video: bool = False) -> Path | None:
    if not frames:
        return None
    if len(frames) > max_tiles:
        step = (len(frames) - 1) / (max_tiles - 1)
        picks = [frames[round(i * step)] for i in range(max_tiles)]
    else:
        picks = frames
    h, w = picks[0].img.shape[:2]
    tw, th = int(w * scale), int(h * scale)
    if tw > 512:  # keep sheets readable but light
        k = 512 / tw
        tw, th = int(tw * k), int(th * k)
    rows = math.ceil(len(picks) / cols)
    label_h = 16
    head = 22 if title else 0
    sheet = Image.new("RGB", (cols * tw, head + rows * (th + label_h)), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    font = _font(12)
    if title:
        d.text((6, 4), title, fill=(240, 240, 240), font=font)
    for i, f in enumerate(picks):
        x, y = (i % cols) * tw, head + (i // cols) * (th + label_h)
        tile = Image.fromarray(f.img).resize((tw, th), Image.NEAREST)
        sheet.paste(tile, (x, y + label_h))
        lab = f"t={f.time:.2f}s #{f.index}" if is_video else f"frame {f.index} ({f.time:.2f}s)"
        d.text((x + 4, y + 2), lab, fill=(255, 220, 0), font=font)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return dest


def diff_image(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    m = changed_mask(a, b)
    base = (_gray(b) * 0.35).astype(np.uint8)
    out = np.stack([base, base, base], axis=2)
    out[m] = [255, 40, 40]
    return out


def compare_sheet(pairs: list[tuple[Frame, Frame]], dest: Path, *, max_rows: int = 6, title: str = "") -> Path | None:
    if not pairs:
        return None
    if len(pairs) > max_rows:
        step = (len(pairs) - 1) / (max_rows - 1)
        pairs = [pairs[round(i * step)] for i in range(max_rows)]
    h, w = pairs[0][1].img.shape[:2]
    label_h = 16
    head = 22
    sheet = Image.new("RGB", (3 * w, head + len(pairs) * (h + label_h)), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    font = _font(12)
    d.text((6, 4), title or "referência | atual | diferença (vermelho)", fill=(240, 240, 240), font=font)
    for r, (ref, cur) in enumerate(pairs):
        y = head + r * (h + label_h)
        pct = 100.0 * changed_mask(ref.img, cur.img).mean()
        d.text((4, y + 2), f"frame {cur.index}: {pct:.1f}% pixels mudaram", fill=(255, 220, 0), font=font)
        for c, img in enumerate((ref.img, cur.img, diff_image(ref.img, cur.img))):
            tile = Image.fromarray(img)
            if tile.size != (w, h):
                tile = tile.resize((w, h), Image.NEAREST)
            sheet.paste(tile, (c * w, y + label_h))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return dest


def timeline_plot(series: dict[str, list[tuple[float, float]]], dest: Path, *, title: str = "") -> Path:
    W, H, pad = 900, 260, 40
    img = Image.new("RGB", (W, H), (250, 250, 250))
    d = ImageDraw.Draw(img)
    font = _font(11)
    d.text((pad, 6), title, fill=(20, 20, 20), font=font)
    xs = [x for s in series.values() for x, _ in s]
    if not xs:
        img.save(dest)
        return dest
    x0, x1 = min(xs), max(xs) or 1
    d.rectangle([pad, pad - 10, W - 10, H - pad], outline=(160, 160, 160))
    colors = [(37, 99, 235), (220, 38, 38), (22, 163, 74), (217, 119, 6)]
    for k, (name, pts) in enumerate(series.items()):
        ys = [y for _, y in pts] or [0]
        ymax = max(max(ys), 1e-9)
        col = colors[k % len(colors)]
        prev = None
        for x, y in pts:
            px = pad + (x - x0) / max(x1 - x0, 1e-9) * (W - 10 - pad)
            py = (H - pad) - (y / ymax) * (H - 2 * pad + 10)
            if prev:
                d.line([prev, (px, py)], fill=col, width=2)
            prev = (px, py)
        d.text((W - 260, 18 + 14 * k), f"{name} (máx {ymax:.3g})", fill=col, font=font)
    d.text((pad, H - pad + 6), f"{x0:.1f}s", fill=(80, 80, 80), font=font)
    d.text((W - 60, H - pad + 6), f"{x1:.1f}s", fill=(80, 80, 80), font=font)
    img.save(dest)
    return dest


# ── RAM and expectations ──────────────────────────────────────────────────────

def read_ram_csv(run_dir: Path) -> dict[str, list[tuple[int, float]]]:
    p = run_dir / "ram.csv"
    if not p.exists():
        return {}
    out: dict[str, list[tuple[int, float]]] = {}
    with open(p, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            fr = int(row["frame"])
            for k, v in row.items():
                if k == "frame":
                    continue
                try:
                    val = float(v)
                except (TypeError, ValueError):
                    continue
                out.setdefault(k, []).append((fr, val))
    return out


OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
       "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}


def _ram_value(ram: dict, watch: str, at) -> float | None:
    series = ram.get(watch) or []
    if not series:
        return None
    if at in (None, "end"):
        return series[-1][1]
    if at == "start":
        return series[0][1]
    if at in ("max", "min"):
        vals = [v for _, v in series]
        return max(vals) if at == "max" else min(vals)
    at = int(at)
    best = min(series, key=lambda fv: abs(fv[0] - at))
    return best[1]


def evaluate_expect(expect: dict | None, *, stats: dict, ram: dict, comparison: dict | None,
                    frames: list[Frame], ref_frames: list[Frame] | None) -> dict | None:
    if not expect:
        return None
    results = []
    if expect.get("no_freeze"):
        limit = float(expect.get("freeze_seconds", 2.0))
        long_fr = [f for f in stats["freezes"] if f["seconds"] >= limit]
        results.append({"check": "no_freeze", "passed": not long_fr,
                        "detail": f"{len(long_fr)} congelamento(s) ≥ {limit:g}s"
                                  + (f": {long_fr[:3]}" if long_fr else "")})
    if expect.get("no_black"):
        bl = stats["black_frames"]
        results.append({"check": "no_black", "passed": len(bl) <= 1,
                        "detail": f"{len(bl)} frame(s) pretos" + (f": {bl[:6]}" if bl else "")})
    for r in expect.get("ram", []):
        v = _ram_value(ram, r["watch"], r.get("at", "end"))
        ok = v is not None and OPS[r.get("op", "==")](v, float(r["value"]))
        results.append({"check": f"ram {r['watch']} {r.get('op', '==')} {r['value']} @ {r.get('at', 'end')}",
                        "passed": bool(ok), "detail": f"valor={v}"})
    for vexp in expect.get("visual", []):
        fr = int(vexp.get("frame", frames[-1].index if frames else 0))
        cur = min(frames, key=lambda f: abs(f.index - fr)) if frames else None
        ref = (min(ref_frames, key=lambda f: abs(f.index - fr)) if ref_frames else None) if vexp.get("vs", "baseline") \
            else None
        if cur is None or ref is None:
            results.append({"check": f"visual @ {fr}", "passed": None, "detail": "sem referência para comparar"})
            continue
        x0, y0, x1, y1 = vexp.get("region", [0, 0, cur.img.shape[1], cur.img.shape[0]])
        m = changed_mask(ref.img[y0:y1, x0:x1], cur.img[y0:y1, x0:x1])
        pct = 100.0 * float(m.mean())
        lo, hi = vexp.get("min_changed_pct"), vexp.get("max_changed_pct")
        ok = (lo is None or pct >= lo) and (hi is None or pct <= hi)
        results.append({"check": f"visual região {[x0, y0, x1, y1]} @ {fr}", "passed": ok,
                        "detail": f"{pct:.1f}% mudou (min {lo}, max {hi})"})
    graded = [r for r in results if r["passed"] is not None]
    score = sum(1 for r in graded if r["passed"])
    return {"passed": all(r["passed"] for r in graded) if graded else None, "score": score, "total": len(graded),
            "results": results, "notes": expect.get("notes")}


# ── comparison ────────────────────────────────────────────────────────────────

def compare_frames(cur: list[Frame], ref: list[Frame], *, by: str = "index") -> tuple[dict, list[tuple[Frame, Frame]]]:
    pairs: list[tuple[Frame, Frame]] = []
    if by == "index":
        ref_by = {f.index: f for f in ref}
        pairs = [(ref_by[f.index], f) for f in cur if f.index in ref_by]
    else:  # by time (videos of different lengths)
        for f in cur:
            r = min(ref, key=lambda x: abs(x.time - f.time))
            if abs(r.time - f.time) <= 0.5:
                pairs.append((r, f))
    rows = []
    for r, c in pairs:
        m = changed_mask(r.img, c.img)
        rows.append({"frame": c.index, "time": round(c.time, 3), "changed_pct": round(100.0 * float(m.mean()), 3),
                     "mad": round(mean_abs_diff(r.img, c.img), 5), "ssim": round(ssim(r.img, c.img), 4),
                     "bbox": bbox(m)})
    if not rows:
        return {"pairs": 0}, pairs
    first_div = next((r for r in rows if r["changed_pct"] > 0.5), None)
    summary = {"pairs": len(rows), "identical_pairs": sum(1 for r in rows if r["changed_pct"] == 0),
               "max_changed_pct": max(r["changed_pct"] for r in rows),
               "mean_changed_pct": round(sum(r["changed_pct"] for r in rows) / len(rows), 3),
               "min_ssim": min(r["ssim"] for r in rows),
               "first_divergence": first_div, "per_frame": rows}
    return summary, pairs


def ram_diff(cur: dict, ref: dict) -> dict:
    out = {}
    for k in sorted(set(cur) | set(ref)):
        a, b = dict(ref.get(k, [])), dict(cur.get(k, []))
        common = sorted(set(a) & set(b))
        diffs = [(f, a[f], b[f]) for f in common if a[f] != b[f]]
        out[k] = {"ref_end": ref[k][-1][1] if ref.get(k) else None, "cur_end": cur[k][-1][1] if cur.get(k) else None,
                  "first_diff": {"frame": diffs[0][0], "ref": diffs[0][1], "cur": diffs[0][2]} if diffs else None,
                  "frames_different": len(diffs)}
    return out


# ── drivers ───────────────────────────────────────────────────────────────────

def _write_md(rep: dict, dest: Path) -> None:
    L = [f"# Análise — {rep.get('subject')}", ""]
    if rep.get("run"):
        r = rep["run"]
        L.append(f"- cenário **{r.get('scenario')}**, ROM **{r.get('variant')}**, {r.get('frames')} frames, "
                 f"resultado do emulador: {'ok' if r.get('ok') else 'FALHOU'}")
    L.append(f"- amostras: {rep.get('sampled')} · frames únicos: {rep.get('unique_frames')} · "
             f"pretos: {len(rep.get('black_frames', []))} · congelamentos: {len(rep.get('freezes', []))}")
    for f in rep.get("freezes", [])[:5]:
        L.append(f"  - congelado de {f['from']} a {f['to']} ({f['frames']} frames / {f['seconds']} s)")
    c = rep.get("comparison")
    if c and c.get("pairs"):
        L.append(f"- vs referência: {c['pairs']} pares · idênticos {c['identical_pairs']} · "
                 f"mudança máx {c['max_changed_pct']}% · média {c['mean_changed_pct']}% · SSIM mín {c['min_ssim']}")
        if c.get("first_divergence"):
            fd = c["first_divergence"]
            L.append(f"  - primeira divergência no frame {fd['frame']} ({fd['changed_pct']}%, bbox {fd['bbox']})")
    if rep.get("ram_diff"):
        for k, v in rep["ram_diff"].items():
            L.append(f"- RAM `{k}`: ref={v['ref_end']} atual={v['cur_end']} (frames diferentes: {v['frames_different']})")
    elif rep.get("ram_end"):
        L.append("- RAM no fim: " + ", ".join(f"`{k}`={v}" for k, v in rep["ram_end"].items()))
    e = rep.get("expect")
    if e:
        verdict = {True: "PASSOU", False: "REPROVOU", None: "sem critérios automáticos"}[e["passed"]]
        L.append(f"- **expectativas: {verdict}** ({e['score']}/{e['total']})")
        for r in e["results"]:
            mark = {True: "✔", False: "✘", None: "?"}[r["passed"]]
            L.append(f"  - {mark} {r['check']} — {r['detail']}")
        if e.get("notes"):
            L.append(f"- critério visual (julgar olhando as imagens): {e['notes']}")
    L.append("")
    L.append("Imagens para inspecionar (use a ferramenta Read nelas):")
    for k in ("contact_sheet", "compare_sheet", "timeline"):
        if rep.get(k):
            L.append(f"- {k}: `{rep[k]}`")
    dest.write_text("\n".join(L) + "\n", encoding="utf-8")


def analyze_frames(frames: list[Frame], out_dir: Path, *, subject: str, ref: list[Frame] | None = None,
                   by: str = "index", is_video: bool = False, ram: dict | None = None, ref_ram: dict | None = None,
                   expect: dict | None = None, run_meta: dict | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not frames:
        fail(f"nenhum frame para analisar em {subject}")
    stats = frame_stats(frames)
    rep: dict = {"subject": subject, "sampled": len(frames), "frame_size": list(frames[0].img.shape[:2][::-1]),
                 "unique_frames": stats["unique_frames"], "black_frames": stats["black_frames"],
                 "white_frames": stats["white_frames"], "freezes": stats["freezes"]}
    if run_meta:
        res = run_meta.get("result", {})
        rep["run"] = {"scenario": run_meta.get("scenario"), "variant": run_meta.get("variant"),
                      "frames": run_meta.get("frames"), "ok": res.get("ok"), "error": res.get("error"),
                      "hotpatch": res.get("hotpatch")}
    sheet = contact_sheet(frames, out_dir / "contact.png", title=subject, is_video=is_video)
    rep["contact_sheet"] = paths.rel(sheet) if sheet else None
    series = {"movimento (Δ entre amostras)": [(f.time, m) for f, m in zip(frames, stats["motion"])]}
    comparison = None
    if ref:
        comparison, pairs = compare_frames(frames, ref, by=by)
        rep["comparison"] = {k: v for k, v in comparison.items() if k != "per_frame"}
        rep["comparison_per_frame"] = comparison.get("per_frame", [])[:400]
        cs = compare_sheet(pairs, out_dir / "compare.png")
        rep["compare_sheet"] = paths.rel(cs) if cs else None
        series["% pixels ≠ referência"] = [(c.time, 100.0 * float(changed_mask(r.img, c.img).mean()))
                                           for r, c in pairs]
    rep["timeline"] = paths.rel(timeline_plot(series, out_dir / "timeline.png", title=subject))
    if ram:
        rep["ram_end"] = {k: v[-1][1] for k, v in ram.items() if v}
    if ram and ref_ram:
        rep["ram_diff"] = ram_diff(ram, ref_ram)
    rep["expect"] = evaluate_expect(expect, stats=stats, ram=ram or {}, comparison=comparison, frames=frames,
                                    ref_frames=ref)
    write_json(out_dir / "report.json", rep)
    _write_md(rep, out_dir / "report.md")
    return rep


def analyze_run(run_dir: Path, ref: Path | None = None) -> dict:
    run_dir = Path(run_dir)
    meta = read_json(run_dir / "run.json") or {}
    frames = frames_from_run(run_dir)
    ref_frames = frames_from_run(Path(ref)) if ref else None
    ram = read_ram_csv(run_dir)
    ref_ram = read_ram_csv(Path(ref)) if ref else None
    tag = "vs_" + Path(ref).name if ref else "solo"
    out = run_dir / "analysis" / tag
    rep = analyze_frames(frames, out, subject=f"{meta.get('scenario', run_dir.name)} [{meta.get('variant', '?')}]",
                         ref=ref_frames, ram=ram, ref_ram=ref_ram, expect=meta.get("expect"), run_meta=meta)
    rep["report_md"] = paths.rel(out / "report.md")
    write_json(out / "report.json", rep)
    info(f"análise: {paths.rel(out / 'report.md')}")
    return rep


def analyze_video(video: Path, *, ref: Path | None = None, fps: float | None = None, scene: float | None = None,
                  max_frames: int = 600, expect: dict | None = None) -> dict:
    video = Path(video)
    out = video.parent / f"{video.stem}.analysis"
    frames = frames_from_video(video, out, fps=fps, scene=scene, max_frames=max_frames)
    ref_frames = None
    if ref:
        ref = Path(ref)
        if ref.is_dir() and (ref / "frames").exists():
            ref_frames = frames_from_run(ref)
        elif ref.is_dir():
            ref_frames = frames_from_dir(ref)
        else:
            ref_frames = frames_from_video(ref, ref.parent / f"{ref.stem}.analysis", fps=fps, max_frames=max_frames)
    meta = probe_video(video)
    rep = analyze_frames(frames, out, subject=f"vídeo {video.name}", ref=ref_frames, by="time", is_video=True,
                         expect=expect)
    rep["video"] = meta
    write_json(out / "report.json", rep)
    info(f"análise: {paths.rel(out / 'report.md')}")
    return rep


def cmd_analyze(args) -> int:
    target = Path(args.target)
    if not target.exists():
        # allow run names relative to a game's runs/ dir
        from .game import load_game
        g = load_game(args.game)
        cand = g.runs / args.target
        if not cand.exists():
            fail(f"alvo não encontrado: {args.target}")
        target = cand
    ref = Path(args.ref) if args.ref else None
    if ref and not ref.exists():
        from .game import load_game
        g = load_game(args.game)
        ref = g.runs / args.ref
    expect = json.loads(Path(args.expect).read_text(encoding="utf-8")) if args.expect else None
    if target.is_dir() and (target / "run.json").exists():
        rep = analyze_run(target, ref=ref)
    elif target.is_dir():
        frames = frames_from_dir(target)
        out = target / "analysis"
        rep = analyze_frames(frames, out, subject=f"imagens {target.name}",
                             ref=frames_from_dir(ref) if ref and ref.is_dir() else None, by="index", expect=expect)
    else:
        rep = analyze_video(target, ref=ref, fps=args.fps, scene=args.scene, max_frames=args.max_frames, expect=expect)
    md = (Path(rep.get("contact_sheet", "")).parent / "report.md") if rep.get("contact_sheet") else None
    if md and (paths.ROOT / md).exists():
        print((paths.ROOT / md).read_text(encoding="utf-8"))
    e = rep.get("expect")
    return 1 if (e and e.get("passed") is False) else 0


def register(sub) -> None:
    p = sub.add_parser("analyze", help="analisar um run, um vídeo (mp4/mkv/...) ou uma pasta de imagens")
    p.add_argument("target", help="diretório de run, arquivo de vídeo ou pasta de imagens")
    p.add_argument("--ref", help="referência para comparar (run, vídeo ou pasta)")
    p.add_argument("--game")
    p.add_argument("--fps", type=float, help="(vídeo) amostragem uniforme")
    p.add_argument("--scene", type=float, help="(vídeo) amostrar só trocas de cena (ex.: 0.25)")
    p.add_argument("--max-frames", type=int, default=600)
    p.add_argument("--expect", help="(vídeo/imagens) JSON com critérios, mesmo formato do cenário")
    p.set_defaults(func=cmd_analyze)
