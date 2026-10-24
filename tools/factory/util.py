"""Small shared helpers: logging, subprocess, hashing, downloads, archives, JSON."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import paths


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def info(msg: str) -> None:
    print(f"[factory] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[factory] AVISO: {msg}", file=sys.stderr, flush=True)


def fail(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[name-defined]
    print(f"[factory] ERRO: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def run(cmd: Sequence[str | os.PathLike], *, cwd: Path | str | None = None, env: dict | None = None,
        check: bool = True, capture: bool = False, timeout: float | None = None,
        quiet: bool = False) -> subprocess.CompletedProcess:
    """Run a command with the toolchain environment; raise SystemExit on failure when check=True."""
    argv = [str(c) for c in cmd]
    if not quiet:
        info("$ " + " ".join(_quote(a) for a in argv))
    try:
        proc = subprocess.run(argv, cwd=str(cwd) if cwd else None, env=env or paths.tool_env(),
                              capture_output=capture, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except FileNotFoundError as exc:
        fail(f"executável não encontrado: {argv[0]} ({exc})")
    except subprocess.TimeoutExpired:
        fail(f"tempo esgotado ({timeout}s): {' '.join(argv)}")
    if check and proc.returncode != 0:
        if capture:
            sys.stdout.write(proc.stdout or "")
            sys.stderr.write(proc.stderr or "")
        fail(f"comando falhou (exit {proc.returncode}): {' '.join(argv)}")
    return proc


def _quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hashes(path: Path) -> dict[str, str]:
    algos = {"sha1": hashlib.sha1(), "sha256": hashlib.sha256(), "md5": hashlib.md5()}
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            for h in algos.values():
                h.update(chunk)
    return {k: h.hexdigest() for k, h in algos.items()}


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def download(url: str, dest: Path, *, expect_sha256: str | None = None, retries: int = 3) -> str:
    """Stream url to dest (atomic), returning the sha256; verifies expect_sha256 when given."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ds-mods-factory/0.1"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
                total = int(resp.headers.get("Content-Length") or 0)
                h = hashlib.sha256()
                done = 0
                last_print = 0.0
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last_print > 5:
                        pct = f"{100 * done / total:.0f}%" if total else f"{done >> 20} MB"
                        info(f"  … {dest.name}: {pct}")
                        last_print = now
            digest = h.hexdigest()
            if expect_sha256 and digest.lower() != expect_sha256.lower():
                tmp.unlink(missing_ok=True)
                fail(f"sha256 não confere para {url}: esperado {expect_sha256}, obtido {digest}")
            os.replace(tmp, dest)
            return digest
        except SystemExit:
            raise
        except Exception as exc:  # network hiccup: retry
            last_err = exc
            warn(f"download falhou (tentativa {attempt}/{retries}): {url}: {exc}")
            time.sleep(2 * attempt)
    fail(f"não foi possível baixar {url}: {last_err}")


def _strip_parts(name: str, strip: int) -> str | None:
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None  # never escape the destination
    if len(parts) <= strip:
        return None
    return "/".join(parts[strip:])


def extract_zip(archive: Path, dest: Path, strip: int = 0) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            rel_name = _strip_parts(member.filename, strip)
            if rel_name is None:
                continue
            target = dest / rel_name
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def extract_7z(archive: Path, dest: Path, strip: int = 0) -> None:
    staging = dest.parent / (dest.name + ".7z-staging")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    # bsdtar (shipped with Windows 10+) understands BCJ2-filtered 7z archives; py7zr does not.
    tar = shutil.which("tar")
    ok = False
    if tar:
        ok = subprocess.run([tar, "-xf", str(archive), "-C", str(staging)],
                            capture_output=True).returncode == 0
    if not ok:
        import py7zr  # type: ignore

        shutil.rmtree(staging, ignore_errors=True)
        with py7zr.SevenZipFile(archive, mode="r") as z:
            z.extractall(path=staging)
    dest.mkdir(parents=True, exist_ok=True)
    for src in staging.rglob("*"):
        if src.is_dir():
            continue
        rel_name = _strip_parts(src.relative_to(staging).as_posix(), strip)
        if rel_name is None:
            continue
        target = dest / rel_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), target)
    shutil.rmtree(staging, ignore_errors=True)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def table(rows: Iterable[Sequence[Any]], headers: Sequence[str]) -> str:
    rows = [[str(c) for c in r] for r in rows]
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * w for w in widths)
    body = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(r)) for r in rows]
    return "\n".join([line, sep, *body])


def parse_int(value: str | int) -> int:
    if isinstance(value, int):
        return value
    v = value.strip().lower().replace("_", "")
    return int(v, 16) if v.startswith("0x") else int(v)
