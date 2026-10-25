"""GDB client and bridge (factory gdb / debug start) against a fake of melonDS 1.1's stub."""
import socket
import threading
import time
import unittest

from factory import gdbproxy
from factory.gdbclient import GdbRemote

from .fake_gdbstub import FRAME_COUNTER, FakeMelonStub


def wait_until(cond, timeout: float = 3.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class GdbClientTest(unittest.TestCase):
    def setUp(self):
        self.stub = FakeMelonStub()
        self.addCleanup(self.stub.close)
        self.g = GdbRemote(port=self.stub.port, timeout=3)
        self.addCleanup(self.g.sock.close)

    def test_handshake_registers_and_stop_reason(self):
        self.assertEqual(self.g.stop_reason(), "S02")            # halted on attach, like melonDS
        regs = self.g.registers()
        self.assertEqual(regs["pc"], 0x02000800)
        self.assertEqual(regs["sp"], 0x03002F7C)
        self.assertEqual(regs["cpsr"], 0x1F)                     # last word after the FPA registers

    def test_read_write_across_chunks(self):
        data = bytes(range(256)) * 5                             # 0x500 bytes: reads are split at 0x400
        self.g.write(0x02100100, data)
        self.assertEqual(self.g.read(0x02100100, len(data)), data)
        self.assertIn("m2100500,100", self.stub.received)
        with self.assertRaises(IOError):
            self.g.read(0x01000000, 4)

    def test_breakpoint_hit_reports_pc(self):
        self.assertEqual(self.g.set_breakpoint(0x02000A08), "OK")
        self.assertEqual(self.g.cont(wait=2), "S05")
        self.assertEqual(self.g.registers()["pc"], 0x02000A08)
        self.assertEqual(self.g.clear_breakpoint(0x02000A08), "OK")
        self.assertEqual(self.stub.breakpoints, set())

    def test_second_connection_is_never_answered(self):
        late = GdbRemote(port=self.stub.port, timeout=0.3)       # the quirk the bridge exists for
        self.addCleanup(late.sock.close)
        with self.assertRaises(TimeoutError):
            late.registers()


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.stub = FakeMelonStub()
        self.addCleanup(self.stub.close)
        self.bridge = gdbproxy.Bridge(self.stub.port)
        self.addCleanup(self.bridge.handle, {"op": "quit"})

    def running(self) -> bool:
        start = self.stub.frame_counter()
        return wait_until(lambda: self.stub.frame_counter() > start + 2, 1.5)

    def counter(self) -> str:
        return self.bridge.handle({"op": "read", "addr": FRAME_COUNTER, "size": 4})["data"]

    def test_attach_lets_the_game_run(self):
        self.assertTrue(self.running())

    def test_plain_request_resumes_afterwards(self):
        r = self.bridge.handle({"op": "regs"})
        self.assertTrue(r["ok"])
        self.assertFalse(r["halted"])
        self.assertTrue(self.running())

    def test_halt_holds_until_continue(self):
        self.assertTrue(self.bridge.handle({"op": "halt"})["halted"])
        first = self.counter()
        time.sleep(0.05)
        self.assertEqual(self.counter(), first)                 # still stopped between requests
        r = self.bridge.handle({"op": "continue"})
        self.assertFalse(r["halted"])
        self.assertTrue(self.running())

    def test_breakpoint_hit_holds_the_cpu(self):
        r = self.bridge.handle({"op": "break", "addr": 0x02000A08, "wait": 2})
        self.assertEqual(r["hit"], "S05")
        self.assertEqual(r["regs"]["pc"], 0x02000A08)
        self.assertTrue(r["halted"])
        self.assertEqual(self.stub.breakpoints, set())          # removed after the hit
        first = self.counter()
        time.sleep(0.05)
        self.assertEqual(self.counter(), first)

    def test_breakpoint_timeout_resumes(self):
        r = self.bridge.handle({"op": "break", "addr": 0x02000000, "wait": 0.2})   # never executed
        self.assertIsNone(r["hit"])
        self.assertFalse(r["halted"])
        self.assertTrue(self.running())

    def test_step_holds_and_memory_edits(self):
        r = self.bridge.handle({"op": "step", "n": 3})
        self.assertTrue(r["halted"])
        self.assertEqual(self.stub.steps, 3)
        self.assertTrue(self.bridge.handle({"op": "write", "addr": 0x02100004, "hex": "09000000"})["ok"])
        self.assertEqual(self.bridge.handle({"op": "read", "addr": 0x02100004, "size": 4})["data"], "09000000")

    def test_errors_keep_the_bridge_alive(self):
        r = self.bridge.handle({"op": "read", "addr": 0x01000000, "size": 4})
        self.assertFalse(r["ok"])
        self.assertIn("leitura falhou", r["error"])
        self.assertFalse(self.bridge.handle({"op": "bogus"})["ok"])
        self.assertTrue(self.bridge.handle({"op": "regs"})["ok"])


class ServeTest(unittest.TestCase):
    def test_many_requests_share_one_stub_connection(self):
        stub = FakeMelonStub()
        self.addCleanup(stub.close)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            listen = s.getsockname()[1]
        server = threading.Thread(target=gdbproxy.serve, args=(stub.port, listen), daemon=True)
        server.start()
        self.assertTrue(wait_until(lambda: _ping(listen), 5))
        for _ in range(5):
            self.assertIn("pc", gdbproxy.request(listen, {"op": "regs"}, timeout=5)["regs"])
        self.assertTrue(gdbproxy.request(listen, {"op": "quit"}, timeout=5)["quit"])
        server.join(5)
        self.assertFalse(server.is_alive())
        self.assertEqual(stub.connections, 1)
        self.assertTrue(stub.detached)


def _ping(port: int) -> bool:
    try:
        return bool(gdbproxy.request(port, {"op": "ping"}, timeout=2).get("ok"))
    except OSError:
        return False


if __name__ == "__main__":
    unittest.main()
