import os
import unittest

from daoc_gfx import anisotropy, patches
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
        self.script = self.launcher / anisotropy.LAUNCHER_SCRIPT
        self.script.parent.mkdir(parents=True)
        self.script.write_text(STOCK_LAUNCHER)
        os.chmod(self.script, 0o755)
        self.proxy = self.app / "d3d9.dll"

    def change(self, enable=(), disable=(), launcher="default"):
        patches.change(self.app, set(enable), set(disable), lambda: None,
                       is_running=NOT_RUNNING, log=QUIET,
                       launcher=self.launcher if launcher == "default" else launcher)

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

    def test_a_fresh_launcher_from_a_reinstall_leaves_it_partly_on_until_enabled_again(self):
        self.change(enable=["anisotropy"])
        self.script.write_text(STOCK_LAUNCHER)
        self.assertEqual(self.state(), "partly on")
        self.change(enable=["anisotropy"])
        self.assertEqual(self.state(), "on")

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
