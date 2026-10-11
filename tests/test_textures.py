import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from daoc_gfx import textures
from daoc_gfx.common import GfxError, state_dir

NOT_RUNNING = lambda app: False  # noqa: E731
QUIET = lambda *a: None  # noqa: E731


def snapshot(app: Path) -> dict:
    """Relative path -> bytes for every file of the client, daoc-gfx records excluded."""
    files = {}
    for dirpath, dirnames, names in os.walk(app):
        dirnames[:] = [d for d in dirnames if d != ".daoc-gfx"]
        for n in names:
            p = Path(dirpath) / n
            files[str(p.relative_to(app))] = p.read_bytes()
    return files


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        self.app = self.tmp / "app"
        for rel, data in {"game.dll": b"MZ", "zones/textures/a.dds": b"old-a",
                          "zones/textures/b.dds": b"old-b", "figures/skins/s.dds": b"old-s",
                          "items/i.dds": b"old-i"}.items():
            p = self.app / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        self.original = snapshot(self.app)

    def archive(self, name: str, files: dict, rename: str = "") -> Path:
        """A 7z made with bsdtar; `rename` is a bsdtar -s substitution for odd entry names."""
        src = self.tmp / f"src-{name}"
        for rel, data in files.items():
            p = src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        out = self.tmp / name
        cmd = [textures.TAR, "--format", "7zip", "-cf", str(out)]
        if rename:
            cmd += ["-s", rename]
        subprocess.run(cmd + sorted({Path(r).parts[0] for r in files}), cwd=src, check=True)
        return out

    def pack(self):
        return [self.archive("p1.7z", {"zones/textures/a.dds": b"new-a", "zones/textures/c.dds": b"new-c"}),
                self.archive("p2.7z", {"figures/skins/s.dds": b"new-s"})]

    def install(self, archives, **kw):
        return textures.install(self.app, archives, is_running=NOT_RUNNING, log=QUIET, **kw)

    def use(self, variant, **kw):
        return textures.use(self.app, variant, is_running=NOT_RUNNING, log=QUIET, **kw)

    def read(self, rel):
        return (self.app / rel).read_bytes()


class InstallSwitchUninstall(Fixture):
    def test_round_trip_ends_byte_identical(self):
        self.assertEqual(self.install(self.pack()), ["figures", "zones"])
        self.assertEqual(self.read("zones/textures/a.dds"), b"new-a")
        self.assertEqual(self.read("zones/textures/c.dds"), b"new-c")
        self.assertEqual(self.read("zones/textures/b.dds"), b"old-b")  # not in the pack: kept
        self.assertEqual(self.read("figures/skins/s.dds"), b"new-s")
        self.assertEqual(textures.status(self.app),
                         [("figures", textures.PACK_ACTIVE), ("zones", textures.PACK_ACTIVE)])

        self.assertEqual(self.use("original", components=["ZONES"]), ["zones"])
        self.assertEqual(self.read("zones/textures/a.dds"), b"old-a")
        self.assertFalse((self.app / "zones/textures/c.dds").exists())
        self.assertEqual(self.read("figures/skins/s.dds"), b"new-s")
        self.assertEqual(self.use("original"), ["figures"])  # zones already there
        live = {k: v for k, v in snapshot(self.app).items() if ".texturepack/" not in k}
        self.assertEqual(live, self.original)

        self.assertEqual(self.use("pack"), ["figures", "zones"])
        self.assertEqual(self.read("zones/textures/c.dds"), b"new-c")

        textures.uninstall(self.app, is_running=NOT_RUNNING, log=QUIET)
        self.assertEqual(snapshot(self.app), self.original)
        self.assertEqual(textures.status(self.app), [])
        self.assertEqual(sorted(p.name for p in self.app.iterdir()),
                         [".daoc-gfx", "figures", "game.dll", "items", "zones"])

    def test_pack_paths_match_client_folders_regardless_of_case(self):
        self.install([self.archive("p.7z", {"Zones/Textures/A.DDS": b"new-a"})])
        self.assertEqual(self.read("zones/textures/a.dds"), b"new-a")
        self.assertEqual(sorted(os.listdir(self.app / "zones/textures")), ["a.dds", "b.dds"])

    def test_installing_over_an_installed_folder_is_refused(self):
        self.install(self.pack())
        with self.assertRaisesRegex(GfxError, "zones: pack active"):
            self.install([self.archive("again.7z", {"zones/textures/a.dds": b"newer"})])
        self.assertEqual(self.read("zones/textures/a.dds"), b"new-a")

    def test_component_selection_installs_only_that_folder(self):
        self.assertEqual(self.install(self.pack(), components=["figures"]), ["figures"])
        self.assertEqual(self.read("zones/textures/a.dds"), b"old-a")
        self.assertEqual(textures.status(self.app), [("figures", textures.PACK_ACTIVE)])


class Refusals(Fixture):
    def assertRefused(self, archives, message):
        with self.assertRaisesRegex(GfxError, message):
            self.install(archives)
        self.assertEqual(snapshot(self.app), self.original)
        self.assertEqual(textures.status(self.app), [])

    def test_folder_the_client_does_not_have(self):
        self.assertRefused([self.archive("p.7z", {"Textures/zones/a.dds": b"x"})],
                           "'Textures' is not a folder of this client")

    def test_file_outside_a_folder(self):
        self.assertRefused([self.archive("p.7z", {"game.dll": b"evil"})], "is a file")

    def test_same_file_in_two_parts(self):
        self.assertRefused([self.archive("p1.7z", {"zones/textures/a.dds": b"1"}),
                            self.archive("p2.7z", {"Zones/textures/A.dds": b"2"})], "is in p1.7z and p2.7z")

    def test_path_escaping_the_client(self):
        self.assertRefused([self.archive("p.7z", {"zones/x.dds": b"x"}, rename=",^zones,../zones,")],
                           "unsafe path")

    def test_client_running(self):
        with self.assertRaisesRegex(GfxError, "running"):
            textures.install(self.app, self.pack(), is_running=lambda app: True, log=QUIET)
        self.assertEqual(snapshot(self.app), self.original)


class Interruptions(Fixture):
    def journal(self, value):
        (state_dir(self.app) / textures.JOURNAL).write_text(json.dumps(value))

    def test_failed_merge_restores_the_originals(self):
        real_replace, merged = os.replace, []

        def failing(src, dst):
            if ".daoc-gfx" not in str(dst):  # a pack file moving into the client
                if merged:
                    raise OSError("disk went away")
                merged.append(dst)
            return real_replace(src, dst)

        with mock.patch.object(textures.os, "replace", failing):
            with self.assertRaisesRegex(OSError, "disk went away"):
                self.install(self.pack())
        self.assertEqual(len(merged), 1)  # one pack file was live before the failure
        self.assertEqual(snapshot(self.app), self.original)
        self.assertEqual(textures.status(self.app), [])
        self.assertIsNone(textures.pending(self.app))

    def test_pending_operation_blocks_others_until_recovered(self):
        self.install(self.pack())
        self.journal({"action": "use", "components": ["zones"], "starting": {"zones": textures.PACK_ACTIVE}})
        with self.assertRaisesRegex(GfxError, "recover"):
            self.use("original")
        self.assertEqual(textures.recover(self.app, log=QUIET), "use")
        self.assertEqual(self.use("original"), ["figures", "zones"])

    def test_switch_cut_after_moving_the_live_folder_returns_to_the_start(self):
        self.install(self.pack())
        (self.app / "zones").rename(self.app / "zones.swap")
        self.journal({"action": "use", "components": ["zones"], "starting": {"zones": textures.PACK_ACTIVE}})
        textures.recover(self.app, log=QUIET)
        self.assertEqual(textures.detect(self.app, "zones"), textures.PACK_ACTIVE)
        self.assertEqual(self.read("zones/textures/a.dds"), b"new-a")

    def test_switch_cut_before_its_last_rename_is_finished(self):
        self.install(self.pack())
        (self.app / "zones").rename(self.app / "zones.swap")
        (self.app / "zones.backup").rename(self.app / "zones")
        self.journal({"action": "use", "components": ["zones"], "starting": {"zones": textures.PACK_ACTIVE}})
        textures.recover(self.app, log=QUIET)
        self.assertEqual(textures.detect(self.app, "zones"), textures.ORIGINAL_ACTIVE)
        self.assertEqual(self.read("zones/textures/a.dds"), b"old-a")
        self.assertEqual((self.app / "zones.texturepack/textures/a.dds").read_bytes(), b"new-a")

    def test_uninstall_cut_midway_is_completed(self):
        self.install(self.pack())
        (self.app / "zones").rename(self.app / "zones.texturepack")  # backup not yet moved back
        self.journal({"action": "uninstall", "phase": "restoring", "components": ["figures", "zones"]})
        self.assertEqual(textures.recover(self.app, log=QUIET), "uninstall")
        self.assertEqual(snapshot(self.app), self.original)
        self.assertEqual(textures.status(self.app), [])

    def test_install_cut_during_backup_leaves_originals_untouched(self):
        (self.app / "zones.backup").mkdir()  # a partial clone
        self.journal({"action": "install", "phase": "backing-up", "components": ["zones"]})
        textures.recover(self.app, log=QUIET)
        self.assertEqual(snapshot(self.app), self.original)
        self.assertEqual(textures.status(self.app), [])


if __name__ == "__main__":
    unittest.main()
