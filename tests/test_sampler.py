import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from daoc_gfx import anisotropy

PROXY_DIR = Path(__file__).resolve().parent.parent / "proxy"
# The order proxy/Makefile hashes them in.
SOURCES = ["sampler.h", "sampler.c", "d3d9_proxy.c", "d3d9.def", "Makefile"]


class SamplerRule(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "no C compiler (Xcode Command Line Tools)")
    def test_rule_and_transitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "sampler_test"
            subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-o", str(exe),
                            str(Path(__file__).with_name("sampler_test.c")), str(PROXY_DIR / "sampler.c")],
                           check=True)
            done = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)


class ShippedProxy(unittest.TestCase):
    def test_the_committed_dll_was_built_from_these_sources(self):
        digest = hashlib.sha256(b"".join((PROXY_DIR / n).read_bytes() for n in SOURCES)).hexdigest()
        marker = anisotropy.PROXY_MARKER + b", source " + digest.encode()
        self.assertIn(marker, anisotropy.PROXY.read_bytes(),
                      "daoc_gfx/d3d9.dll is stale: run make in proxy/ and commit it")


if __name__ == "__main__":
    unittest.main()
