"""melonDS (standalone) for playing and live debugging through its GDB stub.

`factory debug start` writes toolchain/melonds/portable/melonDS.toml with the GDB stub enabled
(ARM9 :3333, ARM7 :3334), launches the ROM and a small bridge (gdbproxy.py) that keeps the ONE connection
melonDS 1.1's stub accepts per run; `factory gdb regs|read|write|break|step|halt|continue` talk to that bridge.
Verified quirks (melonDS 1.1, Windows): the stub serves only the first connection; probing the port with a
connect() poisons it; arm-none-eabi-gdb hangs on its handshake; a partial melonDS.toml crashes melonDS, so the
default one is generated first; a forced kill can truncate melonDS.toml (close gracefully).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import paths
from .emu import resolve_rom
from .game import load_game
from .util import fail, info, parse_int, read_json, warn, write_json

PID_FILE = paths.RUNTIME / "melonds.json"
PROXY_PORT = 3340


def portable_dir() -> Path:
    d = paths.melonds().parent / "portable"
    d.mkdir(exist_ok=True)
    return d


def _set_toml(text: str, table: str, key: str, value: str) -> str:
    """Minimal TOML upsert (melonDS writes simple tables; we only touch a few scalar keys)."""
    lines = text.splitlines()
    header = f"[{table}]"
    try:
        i = lines.index(header)
    except ValueError:
        lines += ["", header, f"{key} = {value}"]
        return "\n".join(lines) + "\n"
    j = i + 1
    while j < len(lines) and not lines[j].startswith("["):
        if re.match(rf"^\s*{re.escape(key)}\s*=", lines[j]):
            lines[j] = f"{key} = {value}"
            return "\n".join(lines) + "\n"
        j += 1
    lines.insert(j, f"{key} = {value}")
    return "\n".join(lines) + "\n"


def _close_gracefully(pid: int, timeout: float = 6.0) -> None:
    """WM_CLOSE first (melonDS rewrites melonDS.toml on exit; a forced kill can leave it empty)."""
    subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
    end = time.time() + timeout
    while time.time() < end:
        if not _alive(pid):
            return
        time.sleep(0.25)
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout or ""
    return str(pid) in out


def ensure_default_toml() -> Path:
    """melonDS crashes on start with a partial melonDS.toml, so start from the config it writes itself."""
    toml = portable_dir() / "melonDS.toml"
    backup = portable_dir() / "melonDS.default.toml"
    if toml.exists() and toml.stat().st_size > 1000 and "[Instance0" in toml.read_text(encoding="utf-8"):
        return toml
    if backup.exists() and backup.stat().st_size > 1000:
        shutil.copy2(backup, toml)
        return toml
    toml.unlink(missing_ok=True)
    info("gerando melonDS.toml padrão (abre e fecha o melonDS uma vez)…")
    proc = subprocess.Popen([str(paths.melonds())], cwd=str(paths.melonds().parent))
    time.sleep(4)
    _close_gracefully(proc.pid)
    if not toml.exists() or toml.stat().st_size < 1000:
        fail("o melonDS não gerou a configuração padrão (feche-o e rode de novo)")
    shutil.copy2(toml, backup)
    return toml


def configure(gdb: bool, break_on_start: bool, arm9_port: int, arm7_port: int) -> Path:
    # melonDS 1.1 reads the stub settings from the INSTANCE table (EmuInstance.cpp: localCfg.GetTable("Gdb"))
    toml = ensure_default_toml()
    text = toml.read_text(encoding="utf-8")
    text = _set_toml(text, "Instance0.Gdb", "Enabled", "true" if gdb else "false")
    text = _set_toml(text, "Instance0.Gdb.ARM9", "Port", str(arm9_port))
    text = _set_toml(text, "Instance0.Gdb.ARM9", "BreakOnStartup", "true" if break_on_start else "false")
    text = _set_toml(text, "Instance0.Gdb.ARM7", "Port", str(arm7_port))
    text = _set_toml(text, "Instance0.Gdb.ARM7", "BreakOnStartup", "false")
    toml.write_text(text, encoding="utf-8")
    return toml


def _port_open(port: int) -> bool:
    """True when something LISTENs on the port. Never connect to probe it: melonDS's stub accepts the probe,
    waits for the '+' handshake, gets EOF and then stops serving later connections."""
    try:
        import psutil  # type: ignore

        return any(c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN
                   for c in psutil.net_connections(kind="tcp"))
    except Exception:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout or ""
        return any(f":{port} " in ln and "LISTEN" in ln.upper() for ln in out.splitlines())


def _wait_port(port: int, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if _port_open(port):
            return True
        time.sleep(0.25)
    return False


def _stop_session(st: dict) -> None:
    from . import gdbproxy

    if st.get("proxy_pid"):
        try:
            gdbproxy.request(st.get("proxy_port", PROXY_PORT), {"op": "quit"}, timeout=10)
        except Exception:
            pass
        time.sleep(0.5)
        if _alive(int(st["proxy_pid"])):
            subprocess.run(["taskkill", "/PID", str(st["proxy_pid"]), "/F"], capture_output=True)
    if st.get("pid"):
        _close_gracefully(int(st["pid"]))


def cmd_start(args) -> int:
    g = load_game(args.game)
    rom, label, _ = resolve_rom(g, args.rom)
    old = read_json(PID_FILE) or {}
    if old.get("pid"):
        info(f"fechando a sessão anterior do melonDS (pid {old['pid']})")
        _stop_session(old)
        PID_FILE.unlink(missing_ok=True)
    if not args.no_gdb and _port_open(args.port):
        fail(f"a porta {args.port} já está em uso (outro melonDS aberto?) — feche-o ou use --port")
    toml = configure(not args.no_gdb, args.break_on_start, args.port, args.port + 1)
    proc = subprocess.Popen([str(paths.melonds()), str(rom)], cwd=str(paths.melonds().parent),
                            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    state = {"pid": proc.pid, "rom": str(rom), "port": args.port, "at": time.strftime("%H:%M:%S")}
    write_json(PID_FILE, state)
    info(f"melonDS aberto (pid {proc.pid}) com {label}; config {paths.rel(toml)}")
    if args.no_gdb:
        return 0
    if not _wait_port(args.port, 15):
        warn("o stub GDB não abriu a porta (JIT ligado? build sem GDB?)")
        return 1
    time.sleep(1.5)
    log = open(paths.RUNTIME / "gdbproxy.log", "w", encoding="utf-8")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proxy = subprocess.Popen([sys.executable, str(paths.TOOLS / "factory_cli.py"), "debug", "proxy",
                              "--stub-port", str(args.port), "--listen", str(args.proxy_port)],
                             stdout=log, stderr=subprocess.STDOUT, creationflags=flags, cwd=str(paths.ROOT))
    log.close()
    state.update({"proxy_pid": proxy.pid, "proxy_port": args.proxy_port})
    write_json(PID_FILE, state)
    if not _wait_port(args.proxy_port, 15):
        warn(f"a ponte GDB não subiu — veja {paths.rel(paths.RUNTIME / 'gdbproxy.log')}")
        return 1
    info(f"GDB pronto (stub :{args.port} ↔ ponte :{args.proxy_port}). Ex.: `factory gdb regs`, "
         "`factory gdb read 0x02000000 64`, `factory gdb break 0x0201ABCD --wait 20`")
    return 0


def cmd_proxy(args) -> int:
    from .gdbproxy import serve

    serve(args.stub_port, args.listen)
    return 0


def cmd_stop(args) -> int:
    st = read_json(PID_FILE) or {}
    if not st.get("pid"):
        info("nenhum melonDS registrado")
        return 0
    _stop_session(st)
    PID_FILE.unlink(missing_ok=True)
    info(f"melonDS (pid {st['pid']}) encerrado")
    return 0


def _fmt_regs(regs: dict | None) -> str:
    return "  ".join(f"{k}={v:08X}" for k, v in (regs or {}).items())


def cmd_gdb(args) -> int:
    from . import gdbproxy

    st = read_json(PID_FILE) or {}
    port = st.get("proxy_port", PROXY_PORT)
    if not st.get("proxy_pid") or not _port_open(port):
        fail("sessão de depuração não está ativa — rode `factory debug start`")
    a = args.args
    req: dict
    if args.action == "regs":
        req = {"op": "regs"}
    elif args.action == "read":
        req = {"op": "read", "addr": parse_int(a[0]), "size": parse_int(a[1]) if len(a) > 1 else 64}
    elif args.action == "write":
        req = {"op": "write", "addr": parse_int(a[0]), "hex": a[1].replace(" ", "")}
    elif args.action == "break":
        req = {"op": "break", "addr": parse_int(a[0]), "thumb": args.thumb, "wait": args.wait}
    elif args.action == "step":
        req = {"op": "step", "n": int(a[0]) if a else 1}
    elif args.action == "status":
        req = {"op": "ping"}
    else:
        req = {"op": args.action}
    if args.halt:
        req["halt"] = True
    resp = gdbproxy.request(port, req, timeout=args.wait + 30)
    if not resp.get("ok"):
        fail(resp.get("error", "falhou"))
    if "data" in resp:
        data, base = bytes.fromhex(resp["data"]), req["addr"]
        for off in range(0, len(data), 16):
            row = data[off:off + 16]
            words = " ".join(row[i:i + 4][::-1].hex().upper() for i in range(0, len(row), 4))
            print(f"{base + off:08X}  {row.hex(' ').upper():<48}  | {words}")
    if "hit" in resp:
        print("parou:", resp["hit"]) if resp["hit"] else print(f"não parou em {args.wait:.0f}s")
    if resp.get("regs"):
        print(_fmt_regs(resp["regs"]))
    if "written" in resp:
        print(f"OK ({resp['written']} bytes)")
    if resp.get("halted"):
        print("(emulador PARADO — `factory gdb continue` para seguir)")
    elif args.action in ("status", "continue"):
        print("emulador rodando")
    return 0


def register(sub) -> None:
    p = sub.add_parser("debug", help="melonDS standalone com GDB stub: start | stop")
    s = p.add_subparsers(dest="debug_cmd", metavar="<ação>")
    q = s.add_parser("start", help="abrir a ROM no melonDS com GDB stub (ARM9 :3333, ARM7 :3334) + ponte")
    q.add_argument("--game")
    q.add_argument("--rom", help="base | mod:<nome> | caminho.nds")
    q.add_argument("--port", type=int, default=3333)
    q.add_argument("--proxy-port", type=int, default=PROXY_PORT)
    q.add_argument("--break-on-start", action="store_true")
    q.add_argument("--no-gdb", action="store_true", help="só jogar")
    q.set_defaults(func=cmd_start)
    q = s.add_parser("stop")
    q.set_defaults(func=cmd_stop)
    q = s.add_parser("proxy", help="(interno) ponte persistente para o stub GDB")
    q.add_argument("--stub-port", type=int, default=3333)
    q.add_argument("--listen", type=int, default=PROXY_PORT)
    q.set_defaults(func=cmd_proxy)

    p = sub.add_parser("gdb", help="melonDS ao vivo: status | regs | read <addr> [n] | write <addr> <hex> | "
                                   "break <addr> | step [n] | halt | continue")
    p.add_argument("action", choices=["status", "regs", "read", "write", "break", "step", "halt", "continue"])
    p.add_argument("args", nargs="*")
    p.add_argument("--thumb", action="store_true", help="(break) endereço THUMB")
    p.add_argument("--wait", type=float, default=30, help="(break) segundos esperando o breakpoint")
    p.add_argument("--halt", action="store_true", help="deixar o emulador parado depois do comando")
    p.set_defaults(func=cmd_gdb)
