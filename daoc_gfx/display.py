"""Display patches: Wine's Retina mode and mtld3d.conf settings for one client.

`apply` sets exactly which display patches are on. Turning Retina mode on
records the prefix's RetinaMode value from before, and turning it off restores
that value. The mtld3d.conf keys of every enabled mtld3d patch go into one file
whose first line is a marker; an mtld3d.conf without the marker is the user's
own and is never overwritten or deleted.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from .common import GfxError, game_running, operation_lock, read_json, refuse_if_running, state_dir, write_json

STATE = "display.json"
CONF = "mtld3d.conf"
CONF_MARKER = "# Written by daoc-gfx (OfflineDAoC-macOS-graphics)"
MAC_DRIVER_KEY = r"HKCU\Software\Wine\Mac Driver"
NO_CONF, OUR_CONF, YOUR_CONF = "none", "daoc-gfx", "your own"


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


def conf_owner(app: Path) -> str:
    conf = app / CONF
    return NO_CONF if not conf.exists() else (OUR_CONF if conf_is_ours(conf) else YOUR_CONF)


def render_conf(ids: List[str], keys: Dict[str, str]) -> str:
    lines = [CONF_MARKER,
             f"# Patches: {', '.join(ids)}. Disabling the last of them removes this file.", ""]
    lines += [f"{key} = {value}" for key, value in keys.items()]
    return "\n".join(lines) + "\n"


def enabled(app: Path) -> List[str]:
    state = read_json(state_dir(app) / STATE)
    return state["enabled"] if state else []


def apply(app: Path, catalog: Dict[str, dict], wanted: Set[str],
          get_registry: Callable[[], WineRegistry], is_running=game_running) -> None:
    """Make `wanted` the exact set of enabled display patches (ids of `catalog`)."""
    with operation_lock(app):
        refuse_if_running(app, is_running)
        conf = app / CONF
        conf_ids = [p for p in catalog if p in wanted and catalog[p]["kind"] == "mtld3d"]
        keys: Dict[str, str] = {}
        for p in conf_ids:
            keys.update(catalog[p]["mtld3d"])
        if keys and conf.exists() and not conf_is_ours(conf):
            raise GfxError(f"{conf} is your own file; move it aside to enable {', '.join(conf_ids)}")

        state_path = state_dir(app) / STATE
        state = read_json(state_path) or {"enabled": []}
        retina_on = any(catalog[p]["kind"] == "retina" for p in wanted)
        retina_was = any(catalog.get(p, {}).get("kind") == "retina" for p in state["enabled"])
        if retina_on and not retina_was:
            registry = get_registry()
            state["retina_before"] = registry.retina()
            write_json(state_path, state)  # recorded before the change, so disable can restore it
            registry.set_retina("y")
        elif retina_was and not retina_on:
            get_registry().set_retina(state.pop("retina_before", None))

        if keys:
            conf.write_text(render_conf(conf_ids, keys), encoding="utf-8")
        elif conf.exists() and conf_is_ours(conf):
            conf.unlink()

        state["enabled"] = [p for p in catalog if p in wanted]
        if state["enabled"]:
            write_json(state_path, state)
        elif state_path.exists():
            state_path.unlink()
