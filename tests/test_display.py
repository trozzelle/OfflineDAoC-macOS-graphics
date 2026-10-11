import subprocess
import tempfile
import unittest
from pathlib import Path

from daoc_gfx import display
from daoc_gfx.common import GfxError

NOT_RUNNING = lambda app: False  # noqa: E731


class MemoryRegistry:
    """Stands in for the prefix's registry; the wine round trip is covered by the smoke run."""

    def __init__(self, value):
        self.value = value

    def retina(self):
        return self.value

    def set_retina(self, value):
        self.value = value


class DisplayPresets(unittest.TestCase):
    def setUp(self):
        self.app = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.app)])
        (self.app / "game.dll").write_bytes(b"MZ")
        self.conf = self.app / display.CONF

    def apply(self, registry, name):
        return display.apply(self.app, registry, name, is_running=NOT_RUNNING)

    def revert(self, registry):
        return display.revert(self.app, registry, is_running=NOT_RUNNING)

    def test_revert_restores_the_setting_from_before_the_first_preset(self):
        registry = MemoryRegistry("n")
        self.apply(registry, "retina-srgb")
        self.assertEqual(registry.value, "y")
        self.assertIn("color.space = accurate", self.conf.read_text())
        self.apply(registry, "retina")  # a second preset must not record "y" as the original
        self.assertFalse(self.conf.exists())
        self.assertEqual(self.revert(registry), "retina")
        self.assertEqual(registry.value, "n")
        self.assertEqual(display.status(self.app), {"preset": None, "conf": "none"})

    def test_preset_without_retina_keeps_the_original_value(self):
        registry = MemoryRegistry(None)
        self.apply(registry, "retina")
        self.apply(registry, "srgb")
        self.assertIsNone(registry.value)
        self.assertTrue(display.conf_is_ours(self.conf))

    def test_your_own_mtld3d_conf_is_never_touched(self):
        self.conf.write_text("render.scale = 0.75\n")
        registry = MemoryRegistry(None)
        with self.assertRaisesRegex(GfxError, "your own file"):
            self.apply(registry, "srgb")
        self.assertIsNone(registry.value)
        self.assertEqual(self.conf.read_text(), "render.scale = 0.75\n")
        self.assertEqual(display.status(self.app), {"preset": None, "conf": "your own"})

    def test_unknown_preset(self):
        with self.assertRaisesRegex(GfxError, "no display preset 'ultra'"):
            self.apply(MemoryRegistry(None), "ultra")


if __name__ == "__main__":
    unittest.main()
