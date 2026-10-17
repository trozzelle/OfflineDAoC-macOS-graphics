from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADDON_DIR = REPO / "addon"
ADDON = REPO / "daoc_gfx" / "daoc-world.addon32"
# The order addon/Makefile hashes them in.
SOURCES = ["world_core.h", "world_core.c", "reshade_abi.h", "daoc_world.c", "Makefile"]


class WorldRules(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "no C compiler (Xcode Command Line Tools)")
    def test_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "test_world"
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-o", str(exe),
                            str(ADDON_DIR / "test_world.c"), str(ADDON_DIR / "world_core.c")],
                           check=True)
            # From addon/, where the test reads the shipped daoc-world.ini.
            done = subprocess.run([str(exe)], capture_output=True, text=True, cwd=ADDON_DIR)
        self.assertEqual(done.returncode, 0, done.stdout)


class ShippedAddon(unittest.TestCase):
    def test_the_committed_addon_was_built_from_these_sources(self):
        digest = hashlib.sha256(b"".join((ADDON_DIR / n).read_bytes() for n in SOURCES)).hexdigest()
        marker = b"daoc-gfx daoc-world add-on, source " + digest.encode()
        self.assertIn(marker, ADDON.read_bytes(),
                      "daoc_gfx/daoc-world.addon32 is stale: run make -C addon and commit it")


if __name__ == "__main__":
    unittest.main()
