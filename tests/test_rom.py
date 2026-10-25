"""ROM header/NitroFS: ndsbuild.build_rom → rom.inspect / rom.list_files round trip."""
import struct
import tempfile
import unittest
from pathlib import Path

from factory import ndsbuild, rom


class Crc16Test(unittest.TestCase):
    def test_modbus_check_value(self):
        self.assertEqual(rom.crc16(b"123456789"), 0x4B37)        # CRC-16/MODBUS reference value


class RoundTripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import ndspy.rom  # the header logo comes from the installed package; it is never written to the repo

        secure = struct.pack("<Q", rom.DECRYPTED_SECURE_ID)
        cls.arm9 = secure + bytes(0x200 - len(secure))
        cls.files = {"data/message.txt": b"ola\n", "data/levels/level1.bin": bytes(range(64)), "readme.txt": b"x" * 10}
        data = ndsbuild.build_rom(
            arm9=cls.arm9, arm7=bytes(0x100), title="UNITTEST", gamecode="UTST", arm9_entry=0x02000800,
            arm7_entry=0x02380000, arm7_ram=0x02380000, module_params_offset=None,
            logo=ndspy.rom.NintendoDSRom().nintendoLogo, files=cls.files,
            overlays=[{"id": 0, "ram": 0x02080000, "data": bytes(48)}])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "unittest.nds"
        cls.path.write_bytes(data)
        cls.info = rom.inspect(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_header(self):
        i = self.info
        self.assertTrue(i.header_crc_ok)
        self.assertTrue(i.logo_crc_ok)
        self.assertEqual((i.title, i.gamecode, i.game_id), ("UNITTEST", "UTST", "UTST"))
        self.assertEqual((i.arm9_rom_offset, i.arm9_entry, i.arm9_ram), (0x4000, 0x02000800, 0x02000000))
        self.assertEqual(i.secure_area, "decrypted")
        self.assertIsNone(i.module_params)
        self.assertFalse(i.is_homebrew)

    def test_overlay_table(self):
        self.assertEqual([(o.id, o.ram_address, o.ram_size) for o in self.info.arm9_overlays], [(0, 0x02080000, 48)])

    def test_nitrofs_listing(self):
        listed = {name: size for _, name, _, size in rom.list_files(self.path)}
        self.assertEqual(listed, {k: len(v) for k, v in self.files.items()})

    def test_encrypted_secure_area_is_detected(self):
        data = bytearray(self.path.read_bytes())
        data[0x4000:0x4008] = b"\x11" * 8
        p = Path(self.tmp.name) / "encrypted.nds"
        p.write_bytes(bytes(data))
        self.assertEqual(rom.inspect(p, scan_strings=False).secure_area, "encrypted")

    def test_too_small_is_rejected(self):
        p = Path(self.tmp.name) / "tiny.nds"
        p.write_bytes(b"\x00" * 16)
        with self.assertRaises(ValueError):
            rom.inspect(p)


if __name__ == "__main__":
    unittest.main()
