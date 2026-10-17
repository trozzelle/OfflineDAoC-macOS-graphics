import unittest

from daoc_gfx import display, patches, textures
from daoc_gfx.common import GfxError
from test_textures import Fixture, NOT_RUNNING, QUIET
from test_userdat import CAMELOT, CLIENT


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

    def change(self, enable=(), disable=(), archives=None, from_dir=None, prefix=None):
        patches.change(self.app, set(enable), set(disable), lambda: self.registry, archives,
                       is_running=NOT_RUNNING, log=QUIET, from_dir=from_dir, prefix=prefix)

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
                                         "anisotropy": "off", "reshade": "off", "figure-fog": "off",
                                         "water": "off", "ambient-occlusion": "off", "god-rays": "off",
                                         "sun-shadows": "off"})

    def test_figure_fog_patch_writes_key(self):
        catalog = patches.catalog()
        display.apply(self.app, catalog, {"figure-fog"}, lambda: self.registry, is_running=NOT_RUNNING)
        self.assertIn("fog.figures = on", self.conf.read_text().splitlines())
        self.assertEqual(self.states()["figure-fog"], "on")
        display.apply(self.app, catalog, {"figure-fog", "water"}, lambda: self.registry, is_running=NOT_RUNNING)
        lines = self.conf.read_text().splitlines()
        self.assertIn("water.fresnel = on", lines)
        self.assertIn("water.glint = 1.0", lines)
        display.apply(self.app, catalog, set(), lambda: self.registry, is_running=NOT_RUNNING)
        self.assertFalse(self.conf.exists())

    def test_ambient_occlusion_and_god_rays_write_keys(self):
        self.change(enable=["ambient-occlusion", "god-rays"])
        lines = self.conf.read_text().splitlines()
        self.assertIn("post.ao = 1.0", lines)
        self.assertIn("post.godRays = 1.0", lines)
        self.assertEqual(self.states()["ambient-occlusion"], "on")
        self.assertEqual(self.states()["god-rays"], "on")
        self.change(disable=["ambient-occlusion", "god-rays"])
        self.assertFalse(self.conf.exists())

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
                                         "anisotropy": "off", "reshade": "off", "figure-fog": "off",
                                         "water": "off", "ambient-occlusion": "off", "god-rays": "off",
                                         "sun-shadows": "off"})
        self.select("2", "")
        self.assertEqual(self.states(), {"retina": "on", "srgb": "off", "textures": "not installed",
                                         "anisotropy": "off", "reshade": "off", "figure-fog": "off",
                                         "water": "off", "ambient-occlusion": "off", "god-rays": "off",
                                         "sun-shadows": "off"})
        self.assertEqual(self.registry.value, "y")

    def test_toggling_twice_or_quitting_changes_nothing(self):
        self.assertFalse(self.select("1", "1", "")[0])
        self.assertFalse(self.select("1 2", "q")[0])
        self.assertEqual(self.states()["retina"], "off")

    def test_bad_number_is_reported_and_asked_again(self):
        changed, shown = self.select("99", "x", "2", "")
        self.assertTrue(changed)
        self.assertIn("not a choice: 99", shown)
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


class SunShadows(Patches):
    """A patch with mtld3d.conf keys and a user.dat option."""

    KEYS = ["shadows.sun = on", "lighting.sunScale = 1.8", "lighting.ambientScale = 0.65"]

    def setUp(self):
        super().setUp()
        (self.app / "paths.dat").write_bytes(b"[paths]\r\nsettings=OfflineDAoC035\r\n")
        self.prefix = self.tmp / "prefix"
        self.dat = self.prefix / "drive_c/users/crossover" / CAMELOT / "OfflineDAoC03" / "user.dat"
        self.dat.parent.mkdir(parents=True)
        self.dat.write_bytes(CLIENT)

    def state(self, prefix=None):
        return patches.states(self.app, prefix=prefix)["sun-shadows"]

    def test_sun_shadows_patch_writes_keys_and_userdat(self):
        self.change(enable=["sun-shadows"], prefix=self.prefix)
        lines = self.conf.read_text().splitlines()
        for key in self.KEYS:
            self.assertIn(key, lines)
        self.assertEqual(self.dat.read_bytes(), CLIENT.replace(b"item1=0", b"item1=2", 1))
        self.assertEqual(self.state(self.prefix), "on")
        self.assertEqual(self.state(), "on")

    def test_sun_shadows_removal_restores_conf_and_userdat(self):
        self.change(enable=["water", "sun-shadows"], prefix=self.prefix)
        self.change(disable=["sun-shadows"], prefix=self.prefix)
        lines = self.conf.read_text().splitlines()
        self.assertIn("water.fresnel = on", lines)
        self.assertFalse(any(key in lines for key in self.KEYS))
        self.assertEqual(self.dat.read_bytes(), CLIENT)
        self.assertEqual(self.state(self.prefix), "off")
        self.change(disable=["water"])
        self.assertFalse(self.conf.exists())

    def test_sun_shadows_refused_without_a_prefix(self):
        with self.assertRaisesRegex(GfxError, "sun-shadows.*--prefix"):
            self.change(enable=["sun-shadows"])
        self.assertFalse(self.conf.exists())
        self.assertEqual(self.dat.read_bytes(), CLIENT)
        self.assertEqual(self.state(), "off")
        self.change(enable=["sun-shadows"], prefix=self.prefix)
        with self.assertRaisesRegex(GfxError, "sun-shadows.*--prefix"):
            self.change(disable=["sun-shadows"])
        self.assertIn("shadows.sun = on", self.conf.read_text().splitlines())
        self.assertIn(b"item1=2", self.dat.read_bytes())
        self.change(enable=["srgb"])  # another patch needs no prefix
        self.assertEqual(self.state(), "on")

    def test_sun_shadows_refused_without_a_user_dat_changes_nothing(self):
        self.dat.unlink()
        with self.assertRaisesRegex(GfxError, "no user.dat"):
            self.change(enable=["sun-shadows"], prefix=self.prefix)
        self.assertFalse(self.conf.exists())
        self.assertEqual(self.state(self.prefix), "off")

    def test_sun_shadows_partly_on_when_the_option_changed_in_game_until_enabled_again(self):
        self.change(enable=["sun-shadows"], prefix=self.prefix)
        self.dat.write_bytes(CLIENT.replace(b"item1=0", b"item1=1", 1))  # Shadows picked in game
        self.assertEqual(self.state(self.prefix), "partly on")
        self.assertEqual(self.state(), "partly on")
        self.change(enable=["sun-shadows"], prefix=self.prefix)
        self.assertEqual(self.state(self.prefix), "on")
        self.change(disable=["sun-shadows"], prefix=self.prefix)
        self.assertEqual(self.dat.read_bytes(), CLIENT)  # the value from before the patch

    def test_another_prefix_is_partly_on(self):
        self.change(enable=["sun-shadows"], prefix=self.prefix)
        other = self.tmp / "prefix-arm64"
        theirs = other / "drive_c/users/ada" / CAMELOT / "OfflineDAoC03" / "user.dat"
        theirs.parent.mkdir(parents=True)
        theirs.write_bytes(CLIENT)
        self.assertEqual(self.state(other), "partly on")
        self.change(enable=["sun-shadows"], prefix=other)
        self.assertEqual(self.state(other), "on")



if __name__ == "__main__":
    unittest.main()
