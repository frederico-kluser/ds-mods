"""A stand-in for melonDS 1.1's GDB stub, faithful to the bytes recorded from the real one on Windows:

  connect → silence until the client sends '+', then '+'      ·  '?' → '+$S02#b5' (CPU halted on attach)
  every packet → '+' ack, then the reply                      ·  'c' → '+' only; '$S05#b8' at a breakpoint
  '\\x03' → '$S05#b8' (no ack)                                ·  's' → '+$S05#b8' · 'D' → '+$OK#9a'
  only the FIRST connection of a run is ever answered          ·  'g' → r0-r15, f0-f7 (12 bytes), fps, cpsr

A background "CPU" walks a tiny frame loop (0x02000800 → 0x02000898 → 0x02000A08) and bumps a frame counter
at 0x02100000 once per loop while running, so tests can tell a halted CPU from a running one.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

RAM_BASE, RAM_SIZE = 0x02000000, 0x00200000
FRAME_COUNTER = 0x02100000
LOOP = (0x02000800, 0x02000898, 0x02000A08)


def packet(body: str) -> bytes:
    raw = body.encode()
    return b"$" + raw + b"#" + b"%02x" % (sum(raw) & 0xFF)


class FakeMelonStub:
    def __init__(self) -> None:
        self.ram = bytearray(RAM_SIZE)
        self.pc = LOOP[0]
        self.running = False
        self.breakpoints: set[int] = set()
        self.received: list[str] = []
        self.steps = 0
        self.connections = 0
        self.detached = False
        self._state = threading.Lock()
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._conns: list[socket.socket] = []
        self._active: socket.socket | None = None
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        for target in (self._accept_loop, self._cpu_loop):
            threading.Thread(target=target, daemon=True).start()

    # ── helpers for tests ──
    def frame_counter(self) -> int:
        return struct.unpack_from("<I", self.ram, FRAME_COUNTER - RAM_BASE)[0]

    def close(self) -> None:
        self._stop.set()
        for s in [self.server, *self._conns]:
            try:
                s.close()
            except OSError:
                pass

    # ── network ──
    def _send(self, conn: socket.socket, data: bytes) -> None:
        with self._send_lock:
            try:
                conn.sendall(data)
            except OSError:
                pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self._conns.append(conn)
            self.connections += 1
            if self.connections == 1:          # later connections are accepted but never answered
                self._active = conn
                threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            if conn.recv(1) != b"+":
                return
            self._send(conn, b"+")
            buf = b""
            while not self._stop.is_set():
                data = conn.recv(4096)
                if not data:
                    return
                buf += data
                while buf:
                    head = buf[:1]
                    if head in (b"+", b"-"):
                        buf = buf[1:]
                    elif head == b"\x03":
                        buf = buf[1:]
                        with self._state:
                            self.running = False
                        self._send(conn, packet("S05"))
                    elif head == b"$":
                        end = buf.find(b"#")
                        if end < 0 or len(buf) < end + 3:
                            break                  # wait for the rest of the packet
                        body, buf = buf[1:end].decode("latin-1"), buf[end + 3:]
                        self._send(conn, b"+")
                        self._handle(conn, body)
                    else:
                        buf = buf[1:]
        except OSError:
            return

    def _handle(self, conn: socket.socket, body: str) -> None:
        self.received.append(body)
        reply: str | None
        if body == "?":
            reply = "S02"
        elif body == "g":
            with self._state:
                regs = [0x04000006, 0x106, 0x02000100, 0, FRAME_COUNTER, 0, 0, 0x7C00, 0, 0, 0, 0, 0x02000800,
                        0x03002F7C, 0x02000898, self.pc]
            raw = struct.pack("<16I", *regs) + bytes(8 * 12) + struct.pack("<2I", 0, 0x1F)
            reply = raw.hex()
        elif body.startswith("m"):
            addr, size = (int(x, 16) for x in body[1:].split(","))
            off = addr - RAM_BASE
            reply = self.ram[off:off + size].hex() if 0 <= off and off + size <= RAM_SIZE else "E01"
        elif body.startswith("M"):
            head, data = body[1:].split(":", 1)
            addr, size = (int(x, 16) for x in head.split(","))
            off = addr - RAM_BASE
            if 0 <= off and off + size <= RAM_SIZE and len(data) == 2 * size:
                with self._state:
                    self.ram[off:off + size] = bytes.fromhex(data)
                reply = "OK"
            else:
                reply = "E01"
        elif body[:1] in ("Z", "z"):
            _, addr, _ = body[1:].split(",")
            with self._state:
                (self.breakpoints.add if body[0] == "Z" else self.breakpoints.discard)(int(addr, 16))
            reply = "OK"
        elif body == "c":
            with self._state:
                self.running = True
            reply = None                            # the stop reply comes later, from the CPU thread
        elif body == "s":
            with self._state:
                self.pc += 4
                self.steps += 1
            reply = "S05"
        elif body == "D":
            with self._state:
                self.detached = True
                self.running = True
            reply = "OK"
        else:
            reply = ""                              # unsupported packet
        if reply is not None:
            self._send(conn, packet(reply))

    # ── the emulated CPU ──
    def _cpu_loop(self) -> None:
        i = 0
        while not self._stop.is_set():
            time.sleep(0.001)
            hit = False
            with self._state:
                if not self.running:
                    continue
                self.pc = LOOP[i % len(LOOP)]
                i += 1
                if self.pc == LOOP[0]:
                    count = self.frame_counter() + 1
                    struct.pack_into("<I", self.ram, FRAME_COUNTER - RAM_BASE, count & 0xFFFFFFFF)
                if self.pc in self.breakpoints:
                    self.running = False
                    hit = True
            if hit and self._active is not None:
                self._send(self._active, packet("S05"))
