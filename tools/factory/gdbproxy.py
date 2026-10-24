"""Long-lived bridge to melonDS's GDB stub.

melonDS 1.1's stub (Windows build) serves only the FIRST connection of each emulator run, and while a client
is attached the CPU is halted inside the stub until a continue. This proxy owns that single connection for the
whole debug session: it attaches, lets the game run, and serves any number of short JSON requests
(one JSON object per line over 127.0.0.1:<listen>) from `factory gdb ...`, interrupting the CPU only while a
request is being handled. halt / step / a breakpoint hit keep the CPU stopped until `continue`.
"""
from __future__ import annotations

import json
import socket
import socketserver
import threading
import time

from .gdbclient import GdbRemote

LOCK = threading.Lock()


class Bridge:
    def __init__(self, stub_port: int):
        self.g = GdbRemote(port=stub_port, timeout=8)
        self.running = False
        self.held = False          # user asked the CPU to stay stopped (halt/step/breakpoint hit) until `continue`
        self.resume()

    def pause(self) -> str | None:
        if not self.running:
            return None
        reply = self.g.interrupt()
        self.running = False
        return reply

    def resume(self) -> None:
        self.g.cont()
        self.running = True

    def handle(self, req: dict) -> dict:
        op = req.get("op")
        if op == "ping":
            return {"ok": True, "running": self.running, "halted": self.held}
        if op == "quit":
            self.pause()
            self.g.detach()
            return {"ok": True, "quit": True}
        stop = self.pause()
        try:
            if op == "regs":
                out = {"regs": self.g.registers()}
            elif op == "read":
                out = {"data": self.g.read(int(req["addr"]), int(req.get("size", 64))).hex()}
            elif op == "write":
                self.g.write(int(req["addr"]), bytes.fromhex(req["hex"]))
                out = {"written": len(req["hex"]) // 2}
            elif op == "step":
                for _ in range(int(req.get("n", 1))):
                    self.g.step()
                out = {"regs": self.g.registers()}
                self.held = True
            elif op == "break":
                addr, thumb = int(req["addr"]), bool(req.get("thumb"))
                self.g.set_breakpoint(addr, thumb=thumb)
                self.held = False
                hit = self.g.cont(wait=float(req.get("wait", 30)))
                if hit is None:
                    self.running = True
                    self.pause()
                else:
                    self.held = True       # like gdb: stay at the breakpoint so memory can be inspected
                out = {"hit": hit, "regs": self.g.registers() if hit else None}
                self.g.clear_breakpoint(addr, thumb=thumb)
            elif op == "halt":
                out = {"stop": stop, "regs": self.g.registers()}
                self.held = True
            elif op == "continue":
                out = {}
                self.held = False
            else:
                out = {"error": f"op desconhecida: {op}"}
        except Exception as exc:  # report, keep the bridge alive
            out = {"error": f"{type(exc).__name__}: {exc}"}
        if req.get("halt"):
            self.held = True
        if not self.held:
            self.resume()
        out["halted"] = self.held
        out["ok"] = "error" not in out
        return out


def serve(stub_port: int, listen_port: int) -> None:
    bridge = Bridge(stub_port)
    stop_event = threading.Event()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            line = self.rfile.readline()
            try:
                req = json.loads(line.decode("utf-8"))
                with LOCK:
                    resp = bridge.handle(req)
            except Exception as exc:
                resp = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self.wfile.write((json.dumps(resp) + "\n").encode("utf-8"))
            if resp.get("quit"):
                stop_event.set()

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", listen_port), Handler) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        while not stop_event.is_set():
            time.sleep(0.2)
        srv.shutdown()


def request(listen_port: int, req: dict, timeout: float = 120) -> dict:
    with socket.create_connection(("127.0.0.1", listen_port), timeout=timeout) as s:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode("utf-8"))
