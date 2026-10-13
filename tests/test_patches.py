import unittest

from daoc_gfx import display, patches, textures
from daoc_gfx.common import GfxError
from test_textures import Fixture, NOT_RUNNING, QUIET


class MemoryRegistry:
    """Stands in for the prefix's registry; the wine round trip is covered by the smoke run."""

    def __init__(self, value):
        self.value = value

    def retina(self):
        return self.value

    def set_retina(self, value):
        self.value = value


class Patches(Fixture):
    def setUp(self):
        super().setUp()
        self.registry = MemoryRegistry("n")
        self.conf = self.app / display.CONF

    def change(self, enable=(), disable=(), archives=None, from_dir=None):
        patches.change(self.app, set(enable), set(disable), lambda: self.registry, archives,
                       is_running=NOT_RUNNING, log=QUIET, from_dir=from_dir)

    def exported_pack(self):
        """The pack, installed from archives, exported and uninstalled again."""
        textures.install(self.app, self.pack(), is_running=NOT_RUNNING, log=QUIET)
        dest = self.tmp / "export"
        textures.export(self.app, dest, is_running=NOT_RUNNING, log=QUIET)
        textures.uninstall(self.app, is_running=NOT_RUNNING, log=QUIET)
        return dest

    def select(self, *answers):
        replies = iter(answers)
        shown = []
        changed = patches.select(self.app, lambda: self.registry, ask=lambda prompt: next(replies),
                                 out=shown.append, is_running=NOT_RUNNING, log=QUIET)
        return changed, shown

    def states(self):
        return patches.states(self.app)


class EnableDisable(Patches):
    def test_retina_off_restores_the_value_from_before_it_was_turned_on(self):
        self.change(enable=["retina"])
        self.assertEqual(self.registry.value, "y")
        self.change(enable=["srgb"])  # another patch must not record "y" as the original
        self.change(disable=["retina"])
        self.assertEqual(self.registry.value, "n")
        self.assertIn("color.space = accurate", self.conf.read_text())
        self.change(disable=["srgb"])
        self.assertFalse(self.conf.exists())
        self.assertEqual(self.states(), {"retina": "off", "srgb": "off", "textures": "not installed",
                                         "anisotropy": "off", "reshade": "off"})

    def test_your_own_mtld3d_conf_blocks_only_mtld3d_patches(self):
        self.conf.write_text("render.scale = 0.75\n")
        with self.assertRaisesRegex(GfxError, "your own file"):
            self.change(enable=["srgb"])
        self.assertEqual(self.conf.read_text(), "render.scale = 0.75\n")
        self.change(enable=["retina"])
        self.change(disable=["retina"])
        self.assertEqual(self.registry.value, "n")
        self.assertEqual(self.conf.read_text(), "render.scale = 0.75\n")

    def test_textures_need_archives_until_installed_then_switch(self):
        with self.assertRaisesRegex(GfxError, "not installed"):
            self.change(enable=["textures"])
        self.change(enable=["textures"], archives=self.pack())
        self.assertEqual(self.read("zones/textures/a.dds"), b"new-a")
        self.change(disable=["textures"])
        self.assertEqual(self.states()["textures"], "off")
        self.assertEqual(self.read("zones/textures/a.dds"), b"old-a")
        self.change(enable=["textures"])  # installed: no archives needed
        self.assertEqual(self.read("zones/textures/a.dds"), b"new-a")

    def test_textures_install_from_an_exported_folder(self):
        dest = self.exported_pack()
        with self.assertRaisesRegex(GfxError, "only for enable textures"):
            self.change(enable=["srgb"], from_dir=dest)
        with self.assertRaisesRegex(GfxError, "not both"):
            self.change(enable=["textures"], archives=[self.tmp / "p1.7z"], from_dir=dest)
        self.assertEqual(self.states()["srgb"], "off")
        self.change(enable=["textures"], from_dir=dest)
        self.assertEqual(self.states()["textures"], "on")
        self.assertEqual(self.read("zones/textures/c.dds"), b"new-c")

    def test_unknown_patch(self):
        with self.assertRaisesRegex(GfxError, "no patch 'ultra'"):
            self.change(enable=["ultra"])


class Select(Patches):
    def test_checklist_turns_on_and_off_only_what_was_toggled(self):
        changed, _ = self.select("1 2", "")
        self.assertTrue(changed)
        self.assertEqual(self.states(), {"retina": "on", "srgb": "on", "textures": "not installed",
                                         "anisotropy": "off", "reshade": "off"})
        self.select("2", "")
        self.assertEqual(self.states(), {"retina": "on", "srgb": "off", "textures": "not installed",
                                         "anisotropy": "off", "reshade": "off"})
        self.assertEqual(self.registry.value, "y")

    def test_toggling_twice_or_quitting_changes_nothing(self):
        self.assertFalse(self.select("1", "1", "")[0])
        self.assertFalse(self.select("1 2", "q")[0])
        self.assertEqual(self.states()["retina"], "off")

    def test_bad_number_is_reported_and_asked_again(self):
        changed, shown = self.select("9", "x", "2", "")
        self.assertTrue(changed)
        self.assertIn("not a choice: 9", shown)
        self.assertIn("not a choice: x", shown)
        self.assertEqual(self.states()["srgb"], "on")

    def test_texture_pack_is_installed_from_the_archives_asked_for(self):
        p1, p2 = self.pack()
        self.select("3", "", f"'{p1}' {str(p2).replace(' ', chr(92) + ' ')}")
        self.assertEqual(self.states()["textures"], "on")
        self.assertEqual(self.read("figures/skins/s.dds"), b"new-s")

    def test_texture_pack_is_installed_from_an_exported_folder_dropped_in(self):
        dest = self.exported_pack()
        self.select("3", "", f"'{dest}'")
        self.assertEqual(self.states()["textures"], "on")
        self.assertEqual(self.read("figures/skins/s.dds"), b"new-s")

    def test_partly_on_textures_stay_as_they_are_unless_toggled(self):
        self.change(enable=["textures"], archives=self.pack())
        textures.use(self.app, "original", ["zones"], is_running=NOT_RUNNING, log=QUIET)
        self.assertEqual(self.states()["textures"], "partly on")
        self.select("1", "")
        self.assertEqual(self.states()["textures"], "partly on")
        self.select("3", "")
        self.assertEqual(self.states()["textures"], "on")


if __name__ == "__main__":
    unittest.main()
