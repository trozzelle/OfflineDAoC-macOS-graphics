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


class ExportAndInstallFromFolder(Fixture):
    PACK_FILES = {"figures/skins/s.dds": b"new-s", "zones/textures/a.dds": b"new-a",
                  "zones/textures/c.dds": b"new-c"}

    def export(self, dest=None, **kw):
        dest = dest or self.tmp / "export"
        return textures.export(self.app, dest, is_running=NOT_RUNNING, log=QUIET, **kw), dest

    def install_from(self, source, **kw):
        return textures.install(self.app, from_dir=source, is_running=NOT_RUNNING, log=QUIET, **kw)

    def folder(self, files: dict) -> Path:
        source = self.tmp / "folder"
        for rel, data in files.items():
            p = source / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return source

    def assertFolderRefused(self, source, message):
        with self.assertRaisesRegex(GfxError, message):
            self.install_from(source)
        self.assertEqual(snapshot(self.app), self.original)
        self.assertEqual(textures.status(self.app), [])
        self.assertFalse((state_dir(self.app) / textures.STAGING).exists())

    def test_export_holds_exactly_the_replaced_and_added_files(self):
        same = self.archive("p3.7z", {"items/i.dds": b"old-i"})  # identical to the original
        self.install(self.pack() + [same])
        done, dest = self.export()
        self.assertEqual(done, ["figures", "items", "zones"])
        self.assertEqual(snapshot(dest), self.PACK_FILES)
        self.assertEqual(os.listdir(dest / "items"), [])  # kept, so installing it back keeps it

    def test_a_component_with_no_differing_file_survives_the_round_trip(self):
        same = self.archive("p3.7z", {"items/i.dds": b"old-i"})
        self.install(self.pack() + [same])
        installed = snapshot(self.app)
        _, dest = self.export()
        textures.uninstall(self.app, is_running=NOT_RUNNING, log=QUIET)
        self.assertEqual(self.install_from(dest), ["figures", "items", "zones"])
        self.assertEqual(snapshot(self.app), installed)
        self.assertEqual(textures.status(self.app), [("figures", textures.PACK_ACTIVE),
                                                     ("items", textures.PACK_ACTIVE),
                                                     ("zones", textures.PACK_ACTIVE)])

    def test_export_with_the_originals_active_is_identical(self):
        self.install(self.pack())
        _, first = self.export()
        self.use("original", components=["zones"])
        _, partly = self.export(self.tmp / "partly")
        self.use("original")
        done, second = self.export(self.tmp / "second")
        self.assertEqual(done, ["figures", "zones"])
        self.assertEqual(snapshot(partly), self.PACK_FILES)
        self.assertEqual(snapshot(second), snapshot(first))

    def test_export_leaves_the_client_as_it_was(self):
        self.install(self.pack())
        self.use("original", components=["figures"])
        before = snapshot(self.app)
        self.export()
        self.assertEqual(snapshot(self.app), before)
        self.assertEqual(textures.status(self.app),
                         [("figures", textures.ORIGINAL_ACTIVE), ("zones", textures.PACK_ACTIVE)])

    def test_export_refuses_a_pending_journal_a_non_empty_or_inner_dest(self):
        with self.assertRaisesRegex(GfxError, "no texture pack is installed"):
            self.export()
        self.install(self.pack())
        dest = self.tmp / "export"
        dest.mkdir()
        (dest / "x").write_bytes(b"x")
        with self.assertRaisesRegex(GfxError, "not an empty folder"):
            self.export(dest)
        self.assertEqual(os.listdir(dest), ["x"])
        with self.assertRaisesRegex(GfxError, "inside the client"):
            self.export(self.app / "export")
        (state_dir(self.app) / textures.JOURNAL).write_text(json.dumps(
            {"action": "use", "components": ["zones"], "starting": {"zones": textures.PACK_ACTIVE}}))
        with self.assertRaisesRegex(GfxError, "recover"):
            self.export(self.tmp / "other")
        self.assertFalse((self.tmp / "other").exists())

    def test_export_refuses_the_client_folder_named_in_other_case(self):
        self.install(self.pack())
        upper = self.tmp / self.app.name.upper()
        if not upper.exists():
            self.skipTest("the volume compares names with regard to case")
        before = snapshot(self.app)
        with self.assertRaisesRegex(GfxError, "inside the client"):
            self.export(upper / "export")
        self.assertEqual(snapshot(self.app), before)
        self.assertFalse((self.app / "export").exists())

    def test_uninstall_then_install_from_the_export_restores_every_file(self):
        self.install(self.pack())
        installed = snapshot(self.app)
        _, dest = self.export()
        textures.uninstall(self.app, is_running=NOT_RUNNING, log=QUIET)
        self.assertEqual(snapshot(self.app), self.original)
        self.assertEqual(self.install_from(dest), ["figures", "zones"])
        self.assertEqual(snapshot(self.app), installed)
        self.assertEqual(snapshot(dest), self.PACK_FILES)  # cloned from, not moved out of
        record = json.loads((state_dir(self.app) / textures.MANIFEST).read_text())["components"]["zones"]
        self.assertEqual(record["from_dir"], "export")
        self.assertNotIn("archives", record)
        self.assertFalse((state_dir(self.app) / textures.STAGING).exists())

    def test_folder_install_matches_case_and_selects_components(self):
        source = self.folder({"Zones/Textures/A.DDS": b"new-a", "figures/skins/s.dds": b"new-s"})
        self.assertEqual(self.install_from(source, components=["zones"]), ["zones"])
        self.assertEqual(self.read("zones/textures/a.dds"), b"new-a")
        self.assertEqual(sorted(os.listdir(self.app / "zones/textures")), ["a.dds", "b.dds"])
        self.assertEqual(self.read("figures/skins/s.dds"), b"old-s")
        with self.assertRaisesRegex(GfxError, "zones: pack active"):
            self.install_from(source)

    def test_symlink_in_the_folder_is_refused(self):
        source = self.folder({"zones/textures/a.dds": b"new-a"})
        os.symlink(self.app / "game.dll", source / "zones/textures/link.dds")
        self.assertFolderRefused(source, "not a plain file or folder")

    def test_top_level_folder_the_client_does_not_have_is_refused(self):
        self.assertFolderRefused(self.folder({"Textures/zones/a.dds": b"x"}),
                                 "'Textures' is not a folder of this client")

    def test_file_outside_a_folder_is_refused(self):
        self.assertFolderRefused(self.folder({"game.dll": b"evil"}), "is a file")

    def test_finder_metadata_files_survive_the_round_trip(self):
        self.install(self.pack())
        installed = snapshot(self.app)
        dest = self.tmp / "export"
        dest.mkdir()
        (dest / ".DS_Store").write_bytes(b"finder")  # a new folder made in Finder
        self.export(dest)
        for junk in (".DS_Store", "._.DS_Store", "zones/.DS_Store", "zones/textures/._a.dds"):
            (dest / junk).write_bytes(b"finder")
        textures.uninstall(self.app, is_running=NOT_RUNNING, log=QUIET)
        self.assertEqual(self.install_from(dest), ["figures", "zones"])
        self.assertEqual(snapshot(self.app), installed)

    def test_round_trip_falls_back_to_plain_copies_when_cp_cannot_clone(self):
        self.install(self.pack())
        installed = snapshot(self.app)
        real_run = subprocess.run

        def cp_fails(cmd, *args, **kw):
            if cmd[0] != "cp":
                return real_run(cmd, *args, **kw)
            if cmd[1] == "-cR":
                Path(cmd[-1]).mkdir()  # a partial copy left behind
            return subprocess.CompletedProcess(cmd, 1, "", "cp: clonefile failed")

        with mock.patch.object(textures.subprocess, "run", cp_fails):
            _, dest = self.export()
            self.assertEqual(snapshot(dest), self.PACK_FILES)
            textures.uninstall(self.app, is_running=NOT_RUNNING, log=QUIET)
            self.assertEqual(self.install_from(dest), ["figures", "zones"])
        self.assertEqual(snapshot(self.app), installed)
        self.assertEqual(snapshot(dest), self.PACK_FILES)

    def test_archives_and_a_folder_together_or_neither_are_refused(self):
        with self.assertRaisesRegex(GfxError, "not both"):
            textures.install(self.app, self.pack(), from_dir=self.folder({"zones/a.dds": b"x"}),
                             is_running=NOT_RUNNING, log=QUIET)
        with self.assertRaisesRegex(GfxError, "no archive or pack folder"):
            textures.install(self.app, [], is_running=NOT_RUNNING, log=QUIET)
        self.assertEqual(snapshot(self.app), self.original)


if __name__ == "__main__":
    unittest.main()
