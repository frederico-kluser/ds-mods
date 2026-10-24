"""`factory setup`: download and install the Windows toolchain described in tools/toolchain.json."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import paths
from .util import download, extract_7z, extract_zip, fail, info, read_json, warn, write_json

GROUPS_DEFAULT = ("core", "extra", "ghidra")


def load_manifest() -> dict:
    data = read_json(paths.MANIFEST)
    if not data or "tools" not in data:
        fail(f"manifesto inválido: {paths.MANIFEST}")
    return data["tools"]


def installed_marker(tool_id: str) -> dict | None:
    return read_json(paths.INSTALLED / f"{tool_id}.json")


def is_installed(tool_id: str, spec: dict) -> bool:
    marker = installed_marker(tool_id)
    if not marker or marker.get("version") != spec["version"]:
        return False
    return (paths.TOOLCHAIN / spec["dest"]).exists()


def install_tool(tool_id: str, spec: dict, *, force: bool = False, relock: bool = False) -> dict:
    if not force and is_installed(tool_id, spec):
        info(f"{tool_id} {spec['version']}: já instalado")
        return installed_marker(tool_id) or {}
    lock = read_json(paths.LOCKFILE, {}) or {}
    locked = lock.get(tool_id, {})
    expect = spec.get("sha256")
    if not expect and not relock and locked.get("url") == spec["url"]:
        expect = locked.get("sha256")

    url = spec["url"]
    fname = url.rsplit("/", 1)[-1].replace("%2B", "+")
    archive = paths.DOWNLOADS / tool_id / fname
    info(f"{tool_id} {spec['version']}: baixando {url}")
    digest = download(url, archive, expect_sha256=expect)

    dest = paths.TOOLCHAIN / spec["dest"]
    kind = spec["type"]
    strip = int(spec.get("strip", 0))
    if kind == "file":
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, dest)
    elif kind == "zip":
        if force and dest.exists() and spec.get("clean", True) and not spec["dest"].startswith(("bin", "ghidra/")):
            shutil.rmtree(dest, ignore_errors=True)
        extract_zip(archive, dest, strip=strip)
    elif kind == "7z":
        extract_7z(archive, dest, strip=strip)
    else:
        fail(f"tipo desconhecido no manifesto: {kind}")

    marker = {"id": tool_id, "version": spec["version"], "url": url, "sha256": digest,
              "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "dest": spec["dest"]}
    write_json(paths.INSTALLED / f"{tool_id}.json", marker)
    lock = read_json(paths.LOCKFILE, {}) or {}
    lock[tool_id] = {"version": spec["version"], "url": url, "sha256": digest,
                     "size": archive.stat().st_size}
    write_json(paths.LOCKFILE, dict(sorted(lock.items())))
    info(f"{tool_id} {spec['version']}: instalado em {paths.rel(dest)}")
    return marker


def post_install() -> None:
    """Small fix-ups that make the tools usable headless (idempotent)."""
    # objdiff/dsd are single files; nothing to do. BizHawk and melonDS configs are generated on demand
    # by emu.py / debug.py, so the installation itself stays pristine.
    lic = paths.TOOLCHAIN / "mwccarm" / "license.dat"
    if (paths.TOOLCHAIN / "mwccarm").exists() and not any((paths.TOOLCHAIN / "mwccarm").rglob("license.dat")):
        warn("nenhum license.dat no pacote mwccarm; criando um vazio (alguns builds exigem LM_LICENSE_FILE)")
        lic.write_text("", encoding="utf-8")
    # Ghidra's launcher looks for Java on PATH; pin it to the bundled Temurin 21 instead
    props = paths.TOOLCHAIN / "ghidra" / "support" / "launch.properties"
    jdk = paths.TOOLCHAIN / "jdk21"
    if props.exists() and jdk.exists():
        text = props.read_text(encoding="utf-8")
        line = f"JAVA_HOME_OVERRIDE={jdk}"
        if line not in text:
            lines = [ln for ln in text.splitlines() if not ln.startswith("JAVA_HOME_OVERRIDE=")]
            lines.append(line)
            props.write_text("\n".join(lines) + "\n", encoding="utf-8")
            info("Ghidra: JAVA_HOME_OVERRIDE → toolchain/jdk21")


def cmd_setup(args) -> int:
    tools = load_manifest()
    selected: list[str]
    if args.only:
        unknown = [t for t in args.only if t not in tools]
        if unknown:
            fail(f"ferramenta(s) desconhecida(s): {', '.join(unknown)} (opções: {', '.join(tools)})")
        selected = list(args.only)
    else:
        groups = set(args.group or GROUPS_DEFAULT)
        selected = [t for t, spec in tools.items() if spec.get("group", "core") in groups]
    # dependencies first
    ordered: list[str] = []
    for t in selected:
        for dep in tools[t].get("requires", []):
            if dep not in ordered:
                ordered.append(dep)
        if t not in ordered:
            ordered.append(t)
    failures = []
    for t in ordered:
        try:
            install_tool(t, tools[t], force=args.force, relock=args.relock)
        except (SystemExit, Exception) as exc:  # keep going: one broken tool must not block the rest
            failures.append(t)
            warn(f"{t}: falhou ({type(exc).__name__}: {exc})")
    post_install()
    if failures:
        warn(f"falharam: {', '.join(failures)}")
        return 1
    info("toolchain pronta. Rode `factory doctor` para validar.")
    return 0


def register(sub) -> None:
    p = sub.add_parser("setup", help="baixa/instala a toolchain Windows (idempotente)")
    p.add_argument("--only", nargs="+", metavar="ID", help="instalar só estes ids do toolchain.json")
    p.add_argument("--group", nargs="+", choices=["core", "extra", "ghidra"],
                   help="grupos a instalar (padrão: todos)")
    p.add_argument("--force", action="store_true", help="reinstalar mesmo se já instalado")
    p.add_argument("--relock", action="store_true", help="aceitar novo sha256 (atualização consciente)")
    p.set_defaults(func=cmd_setup)
