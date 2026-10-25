"""Small pure pieces: CLI wiring, verdicts, Lua serialization, scenario normalization, melonDS TOML, ARM decoding."""
import argparse
import contextlib
import importlib
import io
import unittest
from pathlib import Path

from factory import cli, cycle, debug, disasm, emu, util


class CliTest(unittest.TestCase):
    def test_every_module_imports_and_registers(self):
        for name in cli.MODULES:
            with self.subTest(module=name):
                importlib.import_module(f"factory.{name}")
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        for cmd in ("new", "play", "run", "cycle", "analyze", "mod", "decomp", "debug", "gdb", "selftest",
                    "selftest-media", "memory", "doctor"):
            self.assertIn(cmd, sub.choices)

    def test_help_exits_cleanly(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            cli.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)


class VerdictTest(unittest.TestCase):
    def test_verdicts(self):
        ok = {"run_ok": True}
        hist = [{"run_ok": True, "score": 1, "max_changed_pct": 10.0}, {"run_ok": True, "score": 2, "max_changed_pct": 20.0}]
        cases = [
            ({"run_ok": False}, hist, "QUEBROU"),
            ({**ok, "passed": True}, hist, "SUCESSO"),
            ({**ok, "score": 1}, [], "PRIMEIRA"),
            ({**ok, "score": 3}, hist, "EVOLUIU"),
            ({**ok, "score": 1}, hist, "REGREDIU"),
            ({**ok, "score": 2, "max_changed_pct": 25.0}, hist, "MUDOU (mesma nota)"),
            ({**ok, "score": 2, "max_changed_pct": 20.0}, hist, "ESTÁVEL"),
            ({**ok, "score": 0}, [{"run_ok": False, "score": 9}], "PRIMEIRA"),     # broken runs do not count
        ]
        for cur, history, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(cycle._verdict(cur, history), expected)


class LuaAndScenarioTest(unittest.TestCase):
    def test_lua_literal(self):
        self.assertEqual(emu._lua_literal(None), "nil")
        self.assertEqual(emu._lua_literal(True), "true")
        self.assertEqual(emu._lua_literal([1, 1.5, "a'b\\c\n"]), "{1, 1.5, 'a\\'b\\\\c\\n'}")
        self.assertEqual(emu._lua_literal(Path("C:\\x\\y")), "'C:/x/y'")
        self.assertEqual(emu._lua_literal({"frame": 1, "my-key": {"x": False}}), "{frame = 1, ['my-key'] = {x = false}}")
        with self.assertRaises(TypeError):
            emu._lua_literal({1, 2})

    def test_normalize_inputs_and_watches(self):
        self.assertEqual(emu._normalize_inputs([{"frame": "10", "buttons": ["A"], "touch": [10.6, 20]}]),
                         [{"frame": 10, "hold": 1, "buttons": ["A"], "touch": [10, 20]}])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            emu._normalize_inputs([{"frame": 1, "buttons": ["Turbo"]}])
        self.assertEqual(emu._normalize_watches([{"name": "score", "addr": "0x02100004"},
                                                 {"name": "hp", "addr": 0x10, "type": "u8", "domain": "Data TCM"}]),
                         [{"name": "score", "addr": 0x02100004, "type": "u32"},
                          {"name": "hp", "addr": 0x10, "type": "u8", "domain": "Data TCM"}])


class MelonTomlTest(unittest.TestCase):
    TOML = "[Instance0]\nEnabled = keep\n\n[Instance0.Gdb]\nEnabled = false\n\n[Other]\nX = 1\n"

    def test_update_insert_and_append(self):
        t = debug._set_toml(self.TOML, "Instance0.Gdb", "Enabled", "true")
        self.assertIn("[Instance0.Gdb]\nEnabled = true\n", t)
        self.assertIn("[Instance0]\nEnabled = keep\n", t)                 # same key elsewhere untouched
        t = debug._set_toml(t, "Instance0.Gdb", "Port", "3333")
        self.assertLess(t.index("Port = 3333"), t.index("[Other]"))       # inserted inside its own table
        t = debug._set_toml(t, "Instance0.Gdb.ARM9", "BreakOnStartup", "false")
        self.assertTrue(t.rstrip().endswith("[Instance0.Gdb.ARM9]\nBreakOnStartup = false"))


class UtilAndDisasmTest(unittest.TestCase):
    def test_parse_int_and_sizes(self):
        self.assertEqual(util.parse_int("0x02_000_000"), 0x02000000)
        self.assertEqual(util.parse_int(" 0X1f "), 31)
        self.assertEqual(util.parse_int("42"), 42)
        self.assertEqual(util.parse_int(7), 7)
        self.assertEqual(util.human_size(512), "512 B")
        self.assertEqual(util.human_size(2048), "2.0 KB")

    def test_table(self):
        self.assertEqual(util.table([("a", 1), ("bbb", 22)], ["x", "y"]).splitlines(),
                         ["x    y ", "---  --", "a    1 ", "bbb  22"])

    def test_arm_and_thumb_decoding(self):
        arm = disasm.disasm(bytes.fromhex("011081e21eff2fe1"), 0x02000A00, "arm")
        self.assertEqual([(i.address, i.mnemonic, i.op_str) for i in arm],
                         [(0x02000A00, "add", "r1, r1, #1"), (0x02000A04, "bx", "lr")])
        thumb = disasm.disasm(bytes.fromhex("01307047"), 0x02000C00, "thumb")
        self.assertEqual([i.mnemonic for i in thumb], ["adds", "bx"])


if __name__ == "__main__":
    unittest.main()
