import os
import unittest

from daoc_gfx import anisotropy, launcher, patches
from daoc_gfx.common import GfxError
from test_textures import Fixture, NOT_RUNNING, QUIET

STOCK_LAUNCHER = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    'if [ "$MODE" = "wine" ]; then\n'
    "    # 'd3d9,dxgi=b': the wine build's own d3d9 (mtld3d, picked by its compatdb).\n"
    '    export WINEDLLOVERRIDES="${DAOC_DLL_OVERRIDES:-d3d9,dxgi=b}"\n'
    '    export WINEPREFIX="${DAOC_WINEPREFIX:-$INSTALL_DIR/dist/prefix}"\n'
    "fi\n"
)


class Anisotropy(Fixture):
    def setUp(self):
        super().setUp()
        self.launcher = self.tmp / "DAoCLauncher.app"
        self.script = self.launcher / launcher.SCRIPT
        self.script.parent.mkdir(parents=True)
        self.script.write_text(STOCK_LAUNCHER)
        os.chmod(self.script, 0o755)
        self.proxy = self.app / "d3d9.dll"

    def change(self, enable=(), disable=(), launcher="default", level=None):
        patches.change(self.app, set(enable), set(disable), lambda: None,
                       is_running=NOT_RUNNING, log=QUIET,
                       launcher=self.launcher if launcher == "default" else launcher, level=level)

    def state(self):
        return patches.states(self.app, self.launcher)["anisotropy"]

    def test_on_installs_the_proxy_and_loads_it_first_off_restores_both(self):
        self.change(enable=["anisotropy"])
        self.assertEqual(self.state(), "on")
        self.assertEqual(self.proxy.read_bytes(), anisotropy.PROXY.read_bytes())
        text = self.script.read_text()
        self.assertIn('    export WINEDLLOVERRIDES="${DAOC_DLL_OVERRIDES:-d3d9=n,b;dxgi=b}"', text)
        self.assertEqual(text.replace(anisotropy.PATCHED_LINE, anisotropy.STOCK_LINE), STOCK_LAUNCHER)
        self.assertEqual(self.script.stat().st_mode & 0o777, 0o755)

        (self.app / anisotropy.PROXY_LOG).write_text("log of a run")
        self.change(disable=["anisotropy"], launcher=None)  # the recorded launcher is used
        self.assertEqual(self.state(), "off")
        self.assertEqual(self.script.read_text(), STOCK_LAUNCHER)
        self.assertFalse(self.proxy.exists())
        self.assertFalse((self.app / anisotropy.PROXY_LOG).exists())
        self.assertFalse((self.app / anisotropy.CONF).exists())

    def test_the_level_is_set_kept_when_not_given_and_changed_while_on(self):
        self.change(enable=["anisotropy"])
        self.assertEqual(anisotropy.level(self.app), 16)
        self.change(enable=["anisotropy"], level=4)
        self.assertIn("anisotropy = 4\n", (self.app / anisotropy.CONF).read_text())
        self.script.write_text(STOCK_LAUNCHER)  # a reinstall; enabling again keeps the level
        self.change(enable=["anisotropy"])
        self.assertEqual(anisotropy.level(self.app), 4)
        self.assertEqual(self.state(), "on")

    def test_a_level_needs_anisotropy_being_enabled_and_one_of_the_levels(self):
        with self.assertRaisesRegex(GfxError, "only for enable anisotropy"):
            self.change(enable=["srgb"], level=8)
        with self.assertRaisesRegex(GfxError, "choose from 2, 4, 8, 16"):
            self.change(enable=["anisotropy"], level=3)
        self.assertFalse(self.proxy.exists())

    def test_a_conf_without_a_valid_level_reads_as_the_default(self):
        conf = self.app / anisotropy.CONF
        for text, want in (("anisotropy = 8\n", 8), ("# anisotropy = 8\nanisotropy=2\r\n", 2),
                           ("anisotropy = 32\n", 16), ("anisotropy = 8x\n", 16),
                           ("anisotropy_max = 4\nanisotropy = 4\n", 4), ("", 16)):
            conf.write_text(text)
            self.assertEqual(anisotropy.level(self.app), want, text)

    def test_the_checklist_asks_the_level_and_offers_the_current_one(self):
        replies = iter(["4", "", "9", "8x"])
        shown = []
        patches.select(self.app, lambda: None, ask=lambda prompt: next(replies), out=shown.append,
                       is_running=NOT_RUNNING, log=QUIET, launcher=self.launcher)
        self.assertEqual(self.state(), "on")
        self.assertIn("not a level: 9 (choose 2/4/8/16)", shown)
        self.assertEqual(anisotropy.level(self.app), 8)

    def test_a_fresh_launcher_from_a_reinstall_leaves_it_partly_on_until_enabled_again(self):
        self.change(enable=["anisotropy"])
        self.script.write_text(STOCK_LAUNCHER)
        self.assertEqual(self.state(), "partly on")
        self.change(enable=["anisotropy"])
        self.assertEqual(self.state(), "on")

    def test_a_moved_install_still_finds_its_default_launcher(self):
        install = self.tmp / "install"
        app = install / "dist" / "OfflineDAoC" / "runtime" / "client-opendaoc" / "app"
        app.parent.mkdir(parents=True)
        os.rename(self.app, app)
        os.rename(self.launcher, install / "dist" / "DAoCLauncher.app")
        self.app = app
        self.change(enable=["anisotropy"], launcher=None)

        os.rename(install, self.tmp / "moved")
        self.app = self.tmp / "moved" / app.relative_to(install)
        script = self.tmp / "moved" / "dist" / "DAoCLauncher.app" / launcher.SCRIPT
        self.assertEqual(patches.states(self.app)["anisotropy"], "on")
        self.change(enable=["anisotropy"], launcher=None, level=4)
        self.assertEqual(patches.states(self.app)["anisotropy"], "on")
        self.change(disable=["anisotropy"], launcher=None)
        self.assertEqual(patches.states(self.app)["anisotropy"], "off")
        self.assertEqual(script.read_text(), STOCK_LAUNCHER)

    def test_an_older_proxy_build_is_replaced(self):
        self.change(enable=["anisotropy"])
        self.proxy.write_bytes(b"MZ " + anisotropy.PROXY_MARKER + b", source old")
        self.assertEqual(self.state(), "partly on")
        self.change(enable=["anisotropy"])
        self.assertEqual(self.proxy.read_bytes(), anisotropy.PROXY.read_bytes())

    def test_another_d3d9_dll_is_never_replaced_or_removed(self):
        self.proxy.write_bytes(b"MZ reshade")
        with self.assertRaisesRegex(GfxError, "not daoc-gfx's"):
            self.change(enable=["anisotropy"])
        self.assertEqual(self.script.read_text(), STOCK_LAUNCHER)
        self.assertEqual(self.state(), "off")
        self.change(disable=["anisotropy"])
        self.assertEqual(self.proxy.read_bytes(), b"MZ reshade")

    def test_a_level_file_of_your_own_survives_turning_it_off(self):
        self.change(enable=["anisotropy"])
        conf = self.app / anisotropy.CONF
        conf.write_text("anisotropy = 4\n")
        self.change(disable=["anisotropy"])
        self.assertEqual(conf.read_text(), "anisotropy = 4\n")

    def test_a_launcher_without_the_known_line_is_refused(self):
        self.script.write_text("#!/bin/bash\nexport WINEDLLOVERRIDES=d3d9=b\n")
        with self.assertRaisesRegex(GfxError, "no WINEDLLOVERRIDES line"):
            self.change(enable=["anisotropy"])
        self.assertFalse(self.proxy.exists())

    def test_no_launcher_is_refused(self):
        with self.assertRaisesRegex(GfxError, "no OfflineDAoC-macOS launcher"):
            self.change(enable=["anisotropy"], launcher=self.tmp / "nowhere.app")
        self.assertFalse(self.proxy.exists())

    def test_refused_while_the_client_runs(self):
        with self.assertRaisesRegex(GfxError, "running"):
            anisotropy.enable(self.app, self.launcher, is_running=lambda app: True)
        self.assertFalse(self.proxy.exists())
        self.assertEqual(self.script.read_text(), STOCK_LAUNCHER)


if __name__ == "__main__":
    unittest.main()
