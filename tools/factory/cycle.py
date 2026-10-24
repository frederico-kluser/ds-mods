"""`factory cycle`: one iteration of the autonomous loop — build → baseline (cached) → run → analyze → verdict.

The agent repeats: edit the mod → `factory cycle <scenario> --mod <name>` → read report.md + compare.png →
decide → edit again. Each iteration is appended to games/<ID>/mods/<name>/iterations.jsonl so progress
("evoluiu / estável / regrediu / quebrou / sucesso") is judged against the previous best, not from memory.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from . import paths
from .analyze import analyze_run
from .emu import load_scenario, run_scenario
from .game import Game, load_game
from .util import info, read_json, table, warn


def _baseline_key(g: Game, scen: dict) -> str:
    h = hashlib.sha256()
    h.update(json.dumps({k: v for k, v in scen.items() if k not in ("description", "expect")}, sort_keys=True).encode())
    h.update(g.meta.get("sha1", "").encode())
    h.update((paths.LUA / "factory_runner.lua").read_bytes())
    return h.hexdigest()[:12]


def baseline(g: Game, scenario: str, *, refresh: bool = False, video: bool = False) -> Path:
    name, scen = load_scenario(g, scenario)
    key = _baseline_key(g, scen) + ("v" if video else "")
    dest = g.cache / "baselines" / f"{name}-{key}"
    if dest.exists() and not refresh and (read_json(dest / "run.json") or {}).get("result", {}).get("ok"):
        info(f"baseline em cache: {paths.rel(dest)}")
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    run_scenario(g, scenario, variant="base", out_dir=dest, video=video)
    return dest


def _verdict(cur: dict, history: list[dict]) -> str:
    if not cur.get("run_ok"):
        return "QUEBROU"
    if cur.get("passed") is True:
        return "SUCESSO"
    prev = [h for h in history if h.get("run_ok")]
    if not prev:
        return "PRIMEIRA"
    best = max(h.get("score", 0) for h in prev)
    last = prev[-1]
    if cur.get("score", 0) > best:
        return "EVOLUIU"
    if cur.get("score", 0) < last.get("score", 0):
        return "REGREDIU"
    if (cur.get("max_changed_pct") or 0) != (last.get("max_changed_pct") or 0):
        return "MUDOU (mesma nota)"
    return "ESTÁVEL"


def cycle(g: Game, scenario: str, mod: str | None, *, note: str = "", build: bool = True, video: bool = False,
          refresh_baseline: bool = False) -> dict:
    t0 = time.time()
    if mod and build:
        from .mods import build_mod
        build_mod(g, mod)
    base_dir = baseline(g, scenario, refresh=refresh_baseline, video=video)
    if mod:
        run_dir = run_scenario(g, scenario, variant=f"mod:{mod}", video=video)
        rep = analyze_run(run_dir, ref=base_dir)
    else:
        run_dir = base_dir
        rep = analyze_run(run_dir, ref=None)
    meta = read_json(run_dir / "run.json") or {}
    e = rep.get("expect") or {}
    comp = rep.get("comparison") or {}
    entry = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "scenario": Path(scenario).stem, "mod": mod,
             "run": paths.rel(run_dir), "baseline": paths.rel(base_dir), "run_ok": bool(meta.get("result", {}).get("ok")),
             "passed": e.get("passed"), "score": e.get("score", 0), "total": e.get("total", 0),
             "max_changed_pct": comp.get("max_changed_pct"), "first_divergence": (comp.get("first_divergence") or {}).get("frame"),
             "freezes": len(rep.get("freezes", [])), "ram_end": rep.get("ram_end"), "note": note,
             "report": rep.get("report_md"), "seconds": round(time.time() - t0, 1)}
    hist_path = (g.mods / mod / "iterations.jsonl") if mod else (g.cache / "base_iterations.jsonl")
    history = [json.loads(x) for x in hist_path.read_text(encoding="utf-8").splitlines() if x.strip()] \
        if hist_path.exists() else []
    entry["iteration"] = len(history) + 1
    entry["verdict"] = _verdict(entry, [h for h in history if h.get("scenario") == entry["scenario"]])
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(hist_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print()
    print(f"=== CICLO #{entry['iteration']} · {entry['scenario']} · mod={mod or '-'} → {entry['verdict']} ===")
    print(f"expectativas: {entry['score']}/{entry['total']} (passou={entry['passed']}) · mudança visual máx "
          f"{entry['max_changed_pct']}% · 1ª divergência frame {entry['first_divergence']} · congelamentos {entry['freezes']}")
    if rep.get("ram_diff"):
        print("RAM: " + ", ".join(f"{k}: {v['ref_end']}→{v['cur_end']}" for k, v in rep["ram_diff"].items()))
    for r in e.get("results", []):
        print(f"  {'✔' if r['passed'] else ('✘' if r['passed'] is False else '?')} {r['check']} — {r['detail']}")
    if e.get("notes"):
        print(f"  critério visual a julgar: {e['notes']}")
    print("Inspecione (Read):")
    for k in ("compare_sheet", "contact_sheet", "timeline", "report_md"):
        if rep.get(k):
            print(f"  {k}: {rep[k]}")
    print(f"histórico: {paths.rel(hist_path)}")
    return entry


def cmd_cycle(args) -> int:
    g = load_game(args.game)
    entry = cycle(g, args.scenario, args.mod, note=args.note or "", build=not args.no_build, video=args.video,
                  refresh_baseline=args.refresh_baseline)
    return 0 if entry["verdict"] in ("SUCESSO", "EVOLUIU", "PRIMEIRA", "ESTÁVEL", "MUDOU (mesma nota)") else 1


def cmd_history(args) -> int:
    g = load_game(args.game)
    p = g.mods / args.mod / "iterations.jsonl"
    if not p.exists():
        warn("sem histórico")
        return 0
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            h = json.loads(line)
            rows.append((h["iteration"], h["at"], h["scenario"], h["verdict"], f"{h['score']}/{h['total']}",
                         h.get("max_changed_pct"), (h.get("note") or "")[:50]))
    print(table(rows, ["#", "quando", "cenário", "veredito", "nota", "Δvis%", "observação"]))
    return 0


def register(sub) -> None:
    p = sub.add_parser("cycle", help="1 iteração do ciclo: build mod → baseline → run → análise → veredito")
    p.add_argument("scenario")
    p.add_argument("--mod", help="mod a testar (sem --mod: só roda/analisa o jogo base)")
    p.add_argument("--game")
    p.add_argument("--note", help="o que mudou nesta iteração (vai para o histórico)")
    p.add_argument("--no-build", action="store_true")
    p.add_argument("--video", action="store_true", help="capturar todos os frames e gerar vídeo")
    p.add_argument("--refresh-baseline", action="store_true")
    p.set_defaults(func=cmd_cycle)

    p = sub.add_parser("history", help="histórico de iterações de um mod")
    p.add_argument("mod")
    p.add_argument("--game")
    p.set_defaults(func=cmd_history)
