import unittest
from pathlib import Path

from daoc_gfx import userdat
from daoc_gfx.common import GfxError, read_json, state_dir
from test_textures import Fixture, NOT_RUNNING, QUIET

CAMELOT = "AppData/Roaming/Electronic Arts/Dark Age of Camelot"
# The client's bytes: CRLF lines, a latin-1 name, no final newline.
CLIENT = b"[main]\r\nlast_char=Ma\xefve\r\n\r\n[shadows]\r\nitem1=0\r\n\r\n[sound]\r\nitem1=10"


class UserDat(Fixture):
    def setUp(self):
        super().setUp()
        (self.app / "paths.dat").write_bytes(b"[paths]\r\nsettings=OfflineDAoC035\r\n")
        self.prefix = self.tmp / "prefix"
        self.camelot = self.prefix / "drive_c/users/crossover" / CAMELOT
        self.dat = self.user_dat("OfflineDAoC03", CLIENT)

    def user_dat(self, folder: str, data: bytes, prefix: Path = None, user: str = "crossover") -> Path:
        path = (prefix or self.prefix) / "drive_c/users" / user / CAMELOT / folder / "user.dat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def set(self, section="shadows", key="item1", value="2", prefixes=None):
        return userdat.set(self.app, prefixes or [self.prefix], section, key, value,
                           is_running=NOT_RUNNING, log=QUIET)

    def restore(self, section="shadows", key="item1", prefixes=None):
        userdat.restore(self.app, prefixes or [self.prefix], section, key, is_running=NOT_RUNNING, log=QUIET)

    def record(self):
        return read_json(state_dir(self.app) / userdat.STATE)


class SetRestore(UserDat):
    def test_userdat_sets_and_restores_byte_identical(self):
        self.assertEqual(self.set(), [self.dat.resolve()])
        self.assertEqual(self.dat.read_bytes(), CLIENT.replace(b"item1=0", b"item1=2", 1))
        self.restore()
        self.assertEqual(self.dat.read_bytes(), CLIENT)
        self.assertIsNone(self.record())

    def test_userdat_keeps_user_edits_outside_key(self):
        self.set()
        # The client rewrites user.dat at exit: other options change, lines come and go.
        played = (self.dat.read_bytes().replace(b"Ma\xefve", b"Bob")
                  .replace(b"[sound]\r\nitem1=10", b"[sound]\r\nitem1=3\r\nitem2=7")
                  .replace(b"[shadows]\r\n", b"[shadows]\r\nitem0=5\r\n"))
        self.dat.write_bytes(played)
        self.restore()
        self.assertEqual(self.dat.read_bytes(), played.replace(b"item1=2", b"item1=0"))

    def test_lookup_ignores_case_and_keeps_the_files_spelling(self):
        data = b"[Shadows]\r\nItem1 = 0\r\n"
        self.dat.write_bytes(data)
        self.set("SHADOWS", "ITEM1", "2")
        self.assertEqual(self.dat.read_bytes(), b"[Shadows]\r\nItem1 = 2\r\n")
        self.restore("shadows", "item1")
        self.assertEqual(self.dat.read_bytes(), data)

    def test_setting_twice_keeps_the_first_original(self):
        self.set(value="2")
        self.set(value="3")
        self.assertIn(b"item1=3", self.dat.read_bytes())
        self.restore()
        self.assertEqual(self.dat.read_bytes(), CLIENT)

    def test_other_keys_are_recorded_separately(self):
        self.set()
        self.set("sound", "item1", "0")
        self.restore()
        self.assertEqual(self.dat.read_bytes(), CLIENT.replace(b"item1=10", b"item1=0"))
        self.restore("sound", "item1")
        self.assertEqual(self.dat.read_bytes(), CLIENT)

    def test_restore_without_a_record_changes_nothing(self):
        self.restore()
        self.assertEqual(self.dat.read_bytes(), CLIENT)


class Absent(UserDat):
    def test_missing_key_is_added_to_its_section_and_removed_again(self):
        data = b"[shadows]\r\nitem2=1\r\n\r\n[sound]\r\nitem1=10\r\n"
        self.dat.write_bytes(data)
        self.set()
        self.assertEqual(self.dat.read_bytes(), b"[shadows]\r\nitem2=1\r\nitem1=2\r\n\r\n[sound]\r\nitem1=10\r\n")
        self.restore()
        self.assertEqual(self.dat.read_bytes(), data)

    def test_missing_key_in_the_last_section_without_a_final_newline(self):
        data = b"[main]\r\nx=1\r\n[shadows]\r\nitem2=1"
        self.dat.write_bytes(data)
        self.set()
        self.assertEqual(self.dat.read_bytes(), data + b"\r\nitem1=2\r\n")
        self.restore()
        self.assertEqual(self.dat.read_bytes(), data)

    def test_missing_section_is_appended_and_removed_again(self):
        self.dat.write_bytes(b"[main]\r\nlast_char=Ma\xefve")
        self.set()
        self.assertEqual(self.dat.read_bytes(), b"[main]\r\nlast_char=Ma\xefve\r\n[shadows]\r\nitem1=2\r\n")
        self.restore()
        self.assertEqual(self.dat.read_bytes(), b"[main]\r\nlast_char=Ma\xefve")

    def test_added_section_keeps_keys_the_client_added_to_it(self):
        self.dat.write_bytes(b"[main]\r\nx=1\r\n")
        self.set()
        self.dat.write_bytes(b"[main]\r\nx=2\r\n[shadows]\r\nitem1=2\r\nitem2=4\r\n")
        self.restore()
        self.assertEqual(self.dat.read_bytes(), b"[main]\r\nx=2\r\n[shadows]\r\nitem2=4\r\n")

    def test_added_key_the_client_dropped_is_left_alone(self):
        self.dat.write_bytes(b"[main]\r\nx=1\r\n")
        self.set()
        self.dat.write_bytes(b"[main]\r\nx=2\r\n")
        self.restore()
        self.assertEqual(self.dat.read_bytes(), b"[main]\r\nx=2\r\n")
        self.assertIsNone(self.record())

    def test_lf_file_stays_lf(self):
        self.dat.write_bytes(b"[main]\nx=1\n")
        self.set()
        self.assertEqual(self.dat.read_bytes(), b"[main]\nx=1\n[shadows]\nitem1=2\n")
        self.restore()
        self.assertEqual(self.dat.read_bytes(), b"[main]\nx=1\n")


class Files(UserDat):
    def test_only_the_apps_settings_folder_is_changed(self):
        other = self.user_dat("OfflineDAoC02", CLIENT)
        self.set()
        self.assertEqual(other.read_bytes(), CLIENT)
        self.assertNotEqual(self.dat.read_bytes(), CLIENT)

    def test_without_a_settings_line_every_client_folder_is_changed(self):
        (self.app / "paths.dat").unlink()
        other = self.user_dat("OfflineDAoC02", CLIENT)
        self.assertEqual(sorted(self.set()), sorted([self.dat.resolve(), other.resolve()]))
        self.restore()
        self.assertEqual((self.dat.read_bytes(), other.read_bytes()), (CLIENT, CLIENT))

    def test_every_user_and_prefix_given(self):
        second = self.tmp / "prefix-arm64"
        theirs = self.user_dat("OfflineDAoC03", CLIENT, prefix=second, user="ada")
        self.assertEqual(len(self.set(prefixes=[self.prefix, second])), 2)
        self.assertIn(b"item1=2", theirs.read_bytes())
        self.restore(prefixes=[second])  # the other prefix keeps its record
        self.assertEqual(theirs.read_bytes(), CLIENT)
        self.assertIn(b"item1=2", self.dat.read_bytes())
        self.restore()
        self.assertEqual(self.dat.read_bytes(), CLIENT)
        self.assertIsNone(self.record())

    def test_no_user_dat_is_refused_and_none_is_created(self):
        self.dat.unlink()
        with self.assertRaises(GfxError):
            self.set()
        self.assertFalse(self.dat.exists())
        self.assertIsNone(self.record())

    def test_a_user_dat_gone_since_is_dropped_from_the_record(self):
        self.set()
        self.dat.unlink()
        self.restore()
        self.assertFalse(self.dat.exists())
        self.assertIsNone(self.record())

    def test_refused_while_the_client_runs(self):
        with self.assertRaises(GfxError):
            userdat.set(self.app, [self.prefix], "shadows", "item1", "2", is_running=lambda app: True)
        with self.assertRaises(GfxError):
            userdat.restore(self.app, [self.prefix], "shadows", "item1", is_running=lambda app: True)
        self.assertEqual(self.dat.read_bytes(), CLIENT)


if __name__ == "__main__":
    unittest.main()
