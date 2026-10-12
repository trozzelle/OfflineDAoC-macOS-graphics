import contextlib
import io
import json
import os
import unittest

from daoc_gfx import anisotropy, cli, patches, textures
from daoc_gfx.common import state_dir
from test_anisotropy import STOCK_LAUNCHER
from test_patches import MemoryRegistry
from test_textures import Fixture, NOT_RUNNING, QUIET


class Cli(Fixture):
    def setUp(self):
        super().setUp()
        self.launcher = self.tmp / "DAoCLauncher.app"
        script = self.launcher / anisotropy.LAUNCHER_SCRIPT
        script.parent.mkdir(parents=True)
        script.write_text(STOCK_LAUNCHER)
        os.chmod(script, 0o755)

    def main(self, *argv, app=None):
        """(exit status, stdout, stderr) of one daoc-gfx run."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--app", str(app or self.app), "--launcher", str(self.launcher)] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def status(self) -> dict:
        code, out, err = self.main("status", "--json")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.endswith("}\n"))
        return json.loads(out)  # exactly one JSON object, nothing else on stdout

    def change(self, enable=(), disable=()):
        patches.change(self.app, set(enable), set(disable), lambda: MemoryRegistry("n"),
                       is_running=NOT_RUNNING, log=QUIET, launcher=self.launcher)


class StatusJson(Cli):
    def test_a_fresh_client_is_all_off_with_no_components(self):
        s = self.status()
        self.assertEqual(list(s), ["schema", "patches", "anisotropy_level", "mtld3d_conf", "textures"])
        self.assertEqual(s, {"schema": 1,
                             "patches": {"retina": "off", "srgb": "off", "textures": "not installed",
                                         "anisotropy": "off"},
                             "anisotropy_level": None,
                             "mtld3d_conf": "none",
                             "textures": {"components": [], "pending": None}})

    def test_keys_and_types_with_patches_on(self):
        self.change(enable=["srgb", "anisotropy"])
        s = self.status()
        self.assertEqual(set(s["patches"]), set(patches.catalog()))
        self.assertTrue(all(isinstance(v, str) for v in s["patches"].values()))
        self.assertEqual(s["patches"]["anisotropy"], "on")
        self.assertIsInstance(s["anisotropy_level"], int)
        self.assertEqual(s["anisotropy_level"], 16)
        self.assertEqual(s["mtld3d_conf"], "daoc-gfx")
        (self.app / "mtld3d.conf").write_text("render.scale = 0.75\n")
        self.assertEqual(self.status()["mtld3d_conf"], "your own")

    def test_texture_components_follow_the_pack(self):
        textures.install(self.app, self.pack(), is_running=NOT_RUNNING, log=QUIET)
        s = self.status()
        self.assertEqual(s["patches"]["textures"], "on")
        self.assertEqual(s["textures"], {"components": [{"name": "figures", "state": "pack active"},
                                                        {"name": "zones", "state": "pack active"}],
                                         "pending": None})
        textures.use(self.app, "original", is_running=NOT_RUNNING, log=QUIET)
        s = self.status()
        self.assertEqual(s["patches"]["textures"], "off")
        self.assertEqual([c["state"] for c in s["textures"]["components"]],
                         ["originals active", "originals active"])

    def test_a_pending_journal_is_reported(self):
        (state_dir(self.app) / textures.JOURNAL).write_text(json.dumps(
            {"action": "install", "phase": "backing-up", "components": ["zones"]}))
        s = self.status()
        self.assertEqual(s["textures"]["pending"], "install")
        self.assertEqual(s["patches"]["textures"], "needs 'textures recover'")

    def test_a_missing_client_folder_fails_with_nothing_on_stdout(self):
        code, out, err = self.main("status", "--json", app=self.tmp / "nope")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("not a DAoC client app folder", err)


class TextureFolders(Cli):
    def test_export_then_install_from_the_folder_gives_the_same_status(self):
        textures.install(self.app, self.pack(), is_running=NOT_RUNNING, log=QUIET)
        before = self.status()
        export = self.tmp / "export"
        code, out, err = self.main("textures", "export", str(export))
        self.assertEqual(code, 0, err)
        self.assertIn("exported: figures, zones", out)
        textures.uninstall(self.app, is_running=NOT_RUNNING, log=QUIET)
        code, out, err = self.main("textures", "install", "--from-dir", str(export))
        self.assertEqual(code, 0, err)
        self.assertIn("installed: figures, zones (pack files active)", out)
        self.assertEqual(self.status(), before)

    def test_install_takes_archives_or_a_folder_not_both_or_neither(self):
        for argv in (["textures", "install"],
                     ["textures", "install", str(self.tmp / "p.7z"), "--from-dir", str(self.tmp)]):
            with self.assertRaises(SystemExit) as raised:
                self.main(*argv)
            self.assertEqual(raised.exception.code, 2)

    def test_enable_from_dir_is_only_for_textures(self):
        code, out, err = self.main("enable", "srgb", "--from-dir", str(self.tmp))
        self.assertEqual(code, 1)
        self.assertIn("only for enable textures", err)
        self.assertEqual(patches.states(self.app)["srgb"], "off")


if __name__ == "__main__":
    unittest.main()
