import hashlib
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from daoc_gfx import anisotropy, launcher, patches, reshade
from daoc_gfx.common import GfxError
from daoc_gfx.textures import TAR
from test_anisotropy import STOCK_LAUNCHER
from test_textures import Fixture, NOT_RUNNING, QUIET, snapshot

KEY, NAME = reshade.OVERRIDE_KEY, reshade.OVERRIDE_NAME


class Registry:
    """One prefix's registry; the wine round trip is covered by the smoke run."""

    def __init__(self, prefix: Path, values=None):
        prefix.mkdir(exist_ok=True)
        self.prefix = prefix.resolve()
        self.values = dict(values or {})

    def read(self, key, name):
        return self.values.get((key, name))

    def write(self, key, name, value):
        if value is None:
            self.values.pop((key, name), None)
        else:
            self.values[(key, name)] = value


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReShade(Fixture):
    def setUp(self):
        super().setUp()
        self.launcher = self.tmp / "DAoCLauncher.app"
        self.script = self.launcher / launcher.SCRIPT
        self.script.parent.mkdir(parents=True)
        self.script.write_text(STOCK_LAUNCHER)
        os.chmod(self.script, 0o755)
        self.registry = Registry(self.tmp / "prefix", {(KEY, NAME): "builtin"})
        self.served, self.fetched = {}, []

        self.dll = b"MZ crosire's ReShade 6.8"
        self.compiler = b"MZ d3dcompiler_47"
        self.slim = {"Shaders/ReShade.fxh": b"fxh", "Textures/lut.png": b"png", "README.md": b"not installed"}
        self.sweetfx = {"Shaders/SweetFX/SMAA.fx": b"smaa", "Textures/SweetFX/a.png": b"tex"}
        setup = self.serve("https://reshade.test/ReShade_Setup.exe", self.zip("setup", "ReShade32.dll", self.dll))
        cab = self.serve("https://ms.test/sdk.cab", self.zip("cab", "fila319", self.compiler))
        self.sources = {
            "reshade": {"version": "6.8.0", "url": "https://reshade.test/ReShade_Setup.exe",
                        "sha256": sha(setup.read_bytes()), "member": "ReShade32.dll", "member_sha256": sha(self.dll)},
            "d3dcompiler_47": {"url": "https://ms.test/sdk.cab", "sha256": sha(cab.read_bytes()),
                               "member": "fila319", "member_sha256": sha(self.compiler)},
            "shaders": [self.repo("https://git.test/slim", "c1", self.slim),
                        self.repo("https://git.test/sweetfx", "c2", self.sweetfx)],
        }
        patcher = mock.patch.object(reshade, "SOURCES", self.sources)
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- fixtures

    def zip(self, name: str, member: str, data: bytes) -> Path:
        src = self.tmp / f"zip-{name}"
        src.mkdir()
        (src / member).write_bytes(data)
        out = self.tmp / f"{name}.zip"
        subprocess.run([TAR, "--format", "zip", "-cf", str(out), member], cwd=src, check=True)
        return out

    def tarball(self, name: str, files: dict) -> Path:
        src = self.tmp / f"tgz-{name}"
        for rel, data in files.items():
            p = src / f"repo-{name}" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        out = self.tmp / f"{name}.tar.gz"
        subprocess.run([TAR, "-czf", str(out), f"repo-{name}"], cwd=src, check=True)
        return out

    def repo(self, url: str, commit: str, files: dict) -> dict:
        archive = self.serve(f"{url}/archive/{commit}.tar.gz", self.tarball(commit, files))
        unpacked = self.tmp / f"tree-{commit}"
        unpacked.mkdir()
        subprocess.run([TAR, "-xzf", str(archive), "-C", str(unpacked), "--strip-components", "1"], check=True)
        return {"repo": url, "commit": commit, "tree_sha256": reshade.tree_sha256(unpacked)}

    def serve(self, url: str, path: Path) -> Path:
        self.served[url] = path
        return path

    def fetch(self, url: str, dest: Path) -> None:
        self.fetched.append(url)
        shutil.copyfile(self.served[url], dest)

    def change(self, enable=(), disable=(), registry=None, level=None):
        with mock.patch.object(reshade, "download", self.fetch):
            patches.change(self.app, set(enable), set(disable), lambda: registry or self.registry,
                           is_running=NOT_RUNNING, log=QUIET, launcher=self.launcher, level=level)

    def state(self, prefix=None):
        return patches.states(self.app, self.launcher, prefix)["reshade"]



class OnOff(ReShade):
    def test_on_installs_and_loads_it_off_restores_everything_and_keeps_your_settings(self):
        self.change(enable=["reshade"])
        self.assertEqual(self.state(self.registry.prefix), "on")
        app = self.app
        self.assertEqual((app / "d3d9.dll").read_bytes(), self.dll)
        self.assertEqual((app / "d3dcompiler_47.dll").read_bytes(), self.compiler)
        self.assertEqual((app / "reshade-shaders/Shaders/SweetFX/SMAA.fx").read_bytes(), b"smaa")
        self.assertEqual((app / "reshade-shaders/Textures/lut.png").read_bytes(), b"png")
        self.assertFalse((app / "reshade-shaders/README.md").exists())
        ini = (app / "ReShade.ini").read_bytes()
        self.assertIn(b"RESHADE_DEPTH_INPUT_IS_REVERSED=0\r\n", ini)
        self.assertIn(b"PresetPath=.\\reshade-presets\\ReShadePreset.ini\r\n", ini)
        self.assertIn(b"SMAA@SMAA.fx", (app / "reshade-presets/ReShadePreset.ini").read_bytes())
        self.assertEqual(self.script.read_text().replace(launcher.patched_line("reshade"), launcher.STOCK_LINE),
                         STOCK_LAUNCHER)
        self.assertEqual(self.registry.read(KEY, NAME), "native,builtin")

        # Tuned in the overlay, a screenshot taken, a log written; then off.
        (app / "reshade-presets/ReShadePreset.ini").write_text("Techniques=CAS@CAS.fx\n")
        (app / "reshade-presets/Mine.ini").write_text("Techniques=LUT@LUT.fx\n")
        (app / "reshade-screenshots").mkdir()
        (app / "reshade-screenshots/shot.png").write_bytes(b"png")
        (app / "ReShade.log").write_text("log")
        self.change(disable=["reshade"])
        self.assertEqual(self.state(self.registry.prefix), "off")
        self.assertEqual(self.script.read_text(), STOCK_LAUNCHER)
        self.assertEqual(self.registry.read(KEY, NAME), "builtin")
        self.assertEqual(snapshot(self.app), {**self.original, "reshade-screenshots/shot.png": b"png"})

        fetched = len(self.fetched)
        self.change(enable=["reshade"])
        self.assertEqual(len(self.fetched), fetched)  # the checked downloads are reused
        self.assertEqual((app / "reshade-presets/ReShadePreset.ini").read_text(), "Techniques=CAS@CAS.fx\n")
        self.assertEqual((app / "reshade-presets/Mine.ini").read_text(), "Techniques=LUT@LUT.fx\n")
        self.assertEqual(self.state(self.registry.prefix), "on")

    def test_an_override_this_patch_left_behind_is_not_restored(self):
        registry = Registry(self.tmp / "prefix", {(KEY, NAME): "native,builtin"})
        self.change(enable=["reshade"], registry=registry)
        self.change(disable=["reshade"], registry=registry)
        self.assertIsNone(registry.read(KEY, NAME))

    def test_a_fresh_launcher_or_another_prefix_is_partly_on_until_enabled_again(self):
        self.change(enable=["reshade"])
        self.script.write_text(STOCK_LAUNCHER)
        self.assertEqual(self.state(), "partly on")
        self.change(enable=["reshade"])
        self.assertEqual(self.state(), "on")

        other = Registry(self.tmp / "prefix-arm64")
        self.assertEqual(self.state(other.prefix), "partly on")
        self.change(enable=["reshade"], registry=other)
        self.assertEqual(self.state(other.prefix), "on")
        self.assertEqual(other.read(KEY, NAME), "native,builtin")
        self.change(disable=["reshade"], registry=other)
        self.assertIsNone(other.read(KEY, NAME))

    def test_a_newer_pinned_release_leaves_it_partly_on_until_enabled_again(self):
        self.change(enable=["reshade"])
        newer = b"MZ crosire's ReShade 6.9"
        setup = self.serve("https://reshade.test/ReShade_Setup_6.9.exe", self.zip("setup-6.9", "ReShade32.dll", newer))
        self.sources["reshade"].update(url="https://reshade.test/ReShade_Setup_6.9.exe",
                                       sha256=sha(setup.read_bytes()), member_sha256=sha(newer))
        self.assertEqual(self.state(), "partly on")
        self.change(enable=["reshade"])
        self.assertEqual((self.app / "d3d9.dll").read_bytes(), newer)
        self.assertEqual(self.state(), "on")

    def test_reshade_patch_installs_addon(self):
        self.change(enable=["reshade"])
        app = self.app
        self.assertEqual((app / "daoc-world.addon32").read_bytes(), reshade.ADDON.read_bytes())
        self.assertEqual((app / "daoc-world.ini").read_bytes(), reshade.ADDON_INI.read_bytes())
        self.assertEqual(self.state(), "on")

        # The ini is yours: what is in it never changes the state.
        (app / "daoc-world.ini").write_text("effects_before_ui = 0\n")
        self.assertEqual(self.state(), "on")
        # An add-on that is missing or not the committed build is partly on until enabled again.
        (app / "daoc-world.addon32").write_bytes(b"MZ an older daoc-world")
        self.assertEqual(self.state(), "partly on")
        self.change(enable=["reshade"])
        self.assertEqual((app / "daoc-world.addon32").read_bytes(), reshade.ADDON.read_bytes())
        self.assertEqual((app / "daoc-world.ini").read_text(), "effects_before_ui = 0\n")
        (app / "daoc-world.addon32").unlink()
        self.assertEqual(self.state(), "partly on")
        self.change(enable=["reshade"])
        self.assertEqual(self.state(), "on")

    def test_reshade_patch_removes_addon(self):
        self.change(enable=["reshade"])
        app = self.app
        (app / "daoc-world.ini").write_text("reflection_scale = 4\n")
        (app / "daoc-world.log").write_text("log")
        self.change(disable=["reshade"])
        self.assertEqual(self.state(), "off")
        for name in ("daoc-world.addon32", "daoc-world.ini", "daoc-world.log"):
            self.assertFalse((app / name).exists(), name)
        self.assertEqual(snapshot(app), self.original)
        self.change(enable=["reshade"])  # your ini comes back
        self.assertEqual((app / "daoc-world.ini").read_text(), "reflection_scale = 4\n")
        self.assertEqual((app / "daoc-world.addon32").read_bytes(), reshade.ADDON.read_bytes())


class Refusals(ReShade):
    def test_a_download_that_is_not_the_pinned_file_changes_nothing(self):
        self.served["https://ms.test/sdk.cab"] = self.zip("tampered", "fila319", b"MZ other")
        with self.assertRaisesRegex(GfxError, "not the pinned"):
            self.change(enable=["reshade"])
        self.assertEqual(snapshot(self.app), self.original)
        self.assertEqual(self.script.read_text(), STOCK_LAUNCHER)
        self.assertEqual(self.registry.read(KEY, NAME), "builtin")
        self.assertEqual(self.state(), "off")

    def test_shader_files_that_are_not_the_pinned_commit_are_refused(self):
        url = "https://git.test/sweetfx/archive/c2.tar.gz"
        self.serve(url, self.tarball("c2-moved", {**self.sweetfx, "Shaders/SweetFX/SMAA.fx": b"other"}))
        with self.assertRaisesRegex(GfxError, "pinned shader files"):
            self.change(enable=["reshade"])
        self.assertEqual(self.fetched.count(url), 2)  # a stale cache is fetched once more first
        self.assertEqual(snapshot(self.app), self.original)

    def test_a_reshade_of_your_own_is_never_replaced_or_removed(self):
        (self.app / "d3d9.dll").write_bytes(b"MZ crosire's ReShade 5.0")
        (self.app / "ReShade.ini").write_text("[GENERAL]\n")
        with self.assertRaisesRegex(GfxError, "of your own"):
            self.change(enable=["reshade"])
        self.assertEqual(self.fetched, [])
        self.change(disable=["reshade"])
        self.assertEqual((self.app / "d3d9.dll").read_bytes(), b"MZ crosire's ReShade 5.0")
        self.assertEqual((self.app / "ReShade.ini").read_text(), "[GENERAL]\n")


class WithAnisotropy(ReShade):
    def test_only_one_of_the_two_d3d9_patches_is_ever_on(self):
        self.change(enable=["anisotropy"])
        with self.assertRaisesRegex(GfxError, "cannot both be on"):
            self.change(enable=["reshade"])
        self.assertEqual((self.app / "d3d9.dll").read_bytes(), anisotropy.PROXY.read_bytes())
        self.assertEqual(self.fetched, [])

        self.change(enable=["reshade"], disable=["anisotropy"])  # a swap in one call
        self.assertEqual(self.state(), "on")
        self.assertEqual(patches.states(self.app, self.launcher)["anisotropy"], "off")
        with self.assertRaisesRegex(GfxError, "cannot both be on"):
            self.change(enable=["anisotropy"])
        self.change(enable=["anisotropy"], disable=["reshade"], level=8)
        self.assertEqual((self.app / "d3d9.dll").read_bytes(), anisotropy.PROXY.read_bytes())
        self.assertEqual(self.state(), "off")
        self.assertEqual(anisotropy.level(self.app), 8)

    def test_both_at_once_is_refused(self):
        with self.assertRaisesRegex(GfxError, "cannot both be on"):
            self.change(enable=["anisotropy", "reshade"])
        self.assertEqual(snapshot(self.app), self.original)

    def test_the_checklist_asks_again_when_both_are_ticked(self):
        ids = list(patches.catalog())
        both = f"{ids.index('anisotropy') + 1} {ids.index('reshade') + 1}"
        replies = iter([both, "", str(ids.index("anisotropy") + 1), ""])
        shown = []
        with mock.patch.object(reshade, "download", self.fetch):
            patches.select(self.app, lambda: self.registry, ask=lambda _: next(replies), out=shown.append,
                           is_running=NOT_RUNNING, log=QUIET, launcher=self.launcher)
        self.assertTrue(any("cannot both be on" in line for line in shown))
        self.assertEqual(self.state(), "on")
        self.assertEqual(patches.states(self.app, self.launcher)["anisotropy"], "off")


if __name__ == "__main__":
    unittest.main()
