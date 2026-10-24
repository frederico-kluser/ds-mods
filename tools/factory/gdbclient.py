"""Minimal GDB remote-serial-protocol client for melonDS's stub.

Why not arm-none-eabi-gdb: melonDS 1.1's stub (src/debug/GdbStub.cpp) waits for an initial '+' right after
accept() and consumes that byte; gdb opens with '$qSupported…', so its first packet is corrupted and the
session hangs. This client sends '+' first and speaks the few packets the agent needs.
"""
from __future__ import annotations

import socket
import struct
import time

REG_NAMES = [f"r{i}" for i in range(13)] + ["sp", "lr", "pc"]


def _checksum(body: bytes) -> bytes:
    return b"%02x" % (sum(body) & 0xFF)


class GdbRemote:
    def __init__(self, host: str = "127.0.0.1", port: int = 3333, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.sock.sendall(b"+")                 # the stub's handshake expects this byte
        self._buf = b""
        self._read_ack()

    # ── transport ──
    def _recv(self) -> bytes:
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError("stub GDB fechou a conexão")
        return chunk

    def _read_ack(self) -> None:
        end = time.time() + 2
        while time.time() < end:
            if self._buf:
                if self._buf[:1] in (b"+", b"-"):
                    self._buf = self._buf[1:]
                return
            try:
                self._buf += self._recv()
            except socket.timeout:
                return

    def _read_packet(self, timeout: float | None = None) -> bytes:
        old = self.sock.gettimeout()
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            while True:
                start = self._buf.find(b"$")
                if start >= 0:
                    hash_pos = self._buf.find(b"#", start)
                    if hash_pos >= 0 and len(self._buf) >= hash_pos + 3:
                        body = self._buf[start + 1:hash_pos]
                        self._buf = self._buf[hash_pos + 3:]
                        self.sock.sendall(b"+")
                        return body
                self._buf += self._recv()
        finally:
            self.sock.settimeout(old)

    def command(self, body: str, *, timeout: float | None = None) -> str:
        raw = body.encode()
        self.sock.sendall(b"$" + raw + b"#" + _checksum(raw))
        self._read_ack()
        return self._read_packet(timeout).decode("latin-1")

    # ── high level ──
    def stop_reason(self) -> str:
        return self.command("?")

    def registers(self) -> dict[str, int]:
        hexs = self.command("g")
        vals = [struct.unpack("<I", bytes.fromhex(hexs[i:i + 8]))[0] for i in range(0, min(len(hexs), 16 * 8), 8)]
        regs = dict(zip(REG_NAMES, vals))
        if len(hexs) >= 17 * 8:  # some stubs append cpsr
            regs["cpsr"] = struct.unpack("<I", bytes.fromhex(hexs[-8:]))[0]
        return regs

    def read(self, addr: int, size: int) -> bytes:
        out = b""
        while size > 0:
            n = min(size, 0x400)
            reply = self.command(f"m{addr:x},{n:x}")
            if reply.startswith("E"):
                raise IOError(f"leitura falhou em 0x{addr:08X}: {reply}")
            out += bytes.fromhex(reply)
            addr += n
            size -= n
        return out

    def write(self, addr: int, data: bytes) -> None:
        reply = self.command(f"M{addr:x},{len(data):x}:{data.hex()}")
        if reply != "OK":
            raise IOError(f"escrita falhou em 0x{addr:08X}: {reply}")

    def set_breakpoint(self, addr: int, thumb: bool = False, kind: str = "0") -> str:
        return self.command(f"Z{kind},{addr:x},{2 if thumb else 4}")

    def clear_breakpoint(self, addr: int, thumb: bool = False, kind: str = "0") -> str:
        return self.command(f"z{kind},{addr:x},{2 if thumb else 4}")

    def cont(self, wait: float | None = None) -> str | None:
        """Continue; with wait=N seconds, block for the stop reply (breakpoint hit) or return None on timeout."""
        raw = b"c"
        self.sock.sendall(b"$" + raw + b"#" + _checksum(raw))
        self._read_ack()
        if wait is None:
            return None
        try:
            return self._read_packet(wait).decode("latin-1")
        except socket.timeout:
            return None

    def interrupt(self) -> str:
        self.sock.sendall(b"\x03")
        return self._read_packet(5).decode("latin-1")

    def step(self) -> str:
        return self.command("s", timeout=5)

    def detach(self) -> None:
        """Detach and wait for the stub's OK before closing (closing early leaves melonDS's stub unable to
        serve the next connection)."""
        try:
            self.command("D", timeout=3)
        except Exception:
            pass
        finally:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
            time.sleep(0.3)
