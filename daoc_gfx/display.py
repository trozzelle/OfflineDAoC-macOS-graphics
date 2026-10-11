"""Display presets: Wine's Retina mode and mtld3d.conf settings for one client.

A preset (presets/display.json) names a Retina mode setting and the mtld3d.conf
keys it wants. Applying one records the prefix's Retina setting from before the
first preset, so `revert` puts it back exactly. daoc-gfx writes mtld3d.conf only
when no such file exists or when it wrote the existing one itself (first-line
marker); a file of your own is never overwritten or deleted.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional

from .common import GfxError, game_running, operation_lock, read_json, refuse_if_running, state_dir, write_json

PRESETS_FILE = Path(__file__).resolve().parent.parent / "presets" / "display.json"
STATE = "display.json"
CONF = "mtld3d.conf"
CONF_MARKER = "# Written by daoc-gfx (OfflineDAoC-macOS-graphics)"
MAC_DRIVER_KEY = r"HKCU\Software\Wine\Mac Driver"


def presets() -> Dict[str, dict]:
    return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))


class WineRegistry:
    """The prefix's Retina mode value (HKCU\\Software\\Wine\\Mac Driver\\RetinaMode)."""

    def __init__(self, wine_home: Path, prefix: Path) -> None:
        self.wine = wine_home / "bin" / "wine"
        self.wineserver = wine_home / "bin" / "wineserver"
        if not self.wine.is_file():
            raise GfxError(f"no wine at {self.wine}")
        if not (prefix / "system.reg").is_file():
            raise GfxError(f"{prefix} is not a wine prefix")
        self.env = dict(os.environ, WINEPREFIX=str(prefix.resolve()), WINEDEBUG="-all")

    def _reg(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.wine), "reg", *args], env=self.env, capture_output=True,
                              text=True, errors="replace")

    def retina(self) -> Optional[str]:
        """The RetinaMode string, or None when the value is not set."""
        done = self._reg("query", MAC_DRIVER_KEY, "/v", "RetinaMode")
        found = re.search(r"RetinaMode\s+REG_SZ\s+(\S*)", done.stdout)
        return found.group(1) if done.returncode == 0 and found else None

    def set_retina(self, value: Optional[str]) -> None:
        if value is None:
            if self.retina() is None:
                return
            done = self._reg("delete", MAC_DRIVER_KEY, "/v", "RetinaMode", "/f")
        else:
            done = self._reg("add", MAC_DRIVER_KEY, "/v", "RetinaMode", "/t", "REG_SZ", "/d", value, "/f")
        if done.returncode != 0:
            raise GfxError(f"wine reg failed: {(done.stderr or done.stdout).strip()}")
        # Wait for the prefix's wineserver to save the registry and exit.
        subprocess.run([str(self.wineserver), "-w"], env=self.env, check=False)


def conf_is_ours(conf: Path) -> bool:
    with open(conf, encoding="utf-8", errors="replace") as f:
        return f.readline().rstrip("\n") == CONF_MARKER


def render_conf(name: str, keys: Dict[str, str]) -> str:
    lines = [CONF_MARKER, f"# Display preset '{name}'. 'daoc-gfx display revert' removes this file.", ""]
    lines += [f"{key} = {value}" for key, value in keys.items()]
    return "\n".join(lines) + "\n"


def apply(app: Path, registry, name: str, is_running=game_running) -> dict:
    table = presets()
    if name not in table:
        raise GfxError(f"no display preset '{name}'; choose from: {', '.join(table)}")
    preset = table[name]
    with operation_lock(app):
        refuse_if_running(app, is_running)
        conf = app / CONF
        if conf.exists() and not conf_is_ours(conf):
            raise GfxError(f"{conf} is your own file; move it aside to use a display preset")
        state_path = state_dir(app) / STATE
        state = read_json(state_path)
        if state is None:
            # Recorded before the first change, so revert can restore it.
            state = {"retina_before": registry.retina()}
        state["preset"] = name
        write_json(state_path, state)
        registry.set_retina("y" if preset["retina"] else state["retina_before"])
        keys = preset.get("mtld3d", {})
        if keys:
            conf.write_text(render_conf(name, keys), encoding="utf-8")
        elif conf.exists():
            conf.unlink()
        return preset


def revert(app: Path, registry, is_running=game_running) -> Optional[str]:
    with operation_lock(app):
        refuse_if_running(app, is_running)
        state_path = state_dir(app) / STATE
        state = read_json(state_path)
        if state is None:
            return None
        registry.set_retina(state["retina_before"])
        conf = app / CONF
        if conf.exists() and conf_is_ours(conf):
            conf.unlink()
        state_path.unlink()
        return state["preset"]


def status(app: Path) -> dict:
    state = read_json(state_dir(app) / STATE)
    conf = app / CONF
    return {
        "preset": state["preset"] if state else None,
        "conf": "none" if not conf.exists() else ("daoc-gfx" if conf_is_ours(conf) else "your own"),
    }
