"""Display patches: Wine's Retina mode and mtld3d.conf settings for one client.

`apply` sets exactly which display patches are on. Turning Retina mode on
records the prefix's RetinaMode value from before, and turning it off restores
that value. The mtld3d.conf keys of every enabled mtld3d patch go into one file
whose first line is a marker; an mtld3d.conf without the marker is the user's
own and is never overwritten or deleted. A patch's `userdat` options are set in
the prefix's user.dat when it turns on and put back when it turns off
(userdat.py), under the same operation lock.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

from . import userdat
from .common import GfxError, game_running, operation_lock, read_json, refuse_if_running, state_dir, write_json

STATE = "display.json"
CONF = "mtld3d.conf"
CONF_MARKER = "# Written by daoc-gfx (OfflineDAoC-macOS-graphics)"
MAC_DRIVER_KEY = r"HKCU\Software\Wine\Mac Driver"
NO_CONF, OUR_CONF, YOUR_CONF = "none", "daoc-gfx", "your own"


class WineRegistry:
    """String values in the prefix's registry: Retina mode
    (HKCU\\Software\\Wine\\Mac Driver\\RetinaMode) and the reshade patch's DLL override."""

    def __init__(self, wine_home: Path, prefix: Path) -> None:
        self.wine = wine_home / "bin" / "wine"
        self.wineserver = wine_home / "bin" / "wineserver"
        if not self.wine.is_file():
            raise GfxError(f"no wine at {self.wine}")
        if not (prefix / "system.reg").is_file():
            raise GfxError(f"{prefix} is not a wine prefix")
        self.prefix = prefix.resolve()
        self.env = dict(os.environ, WINEPREFIX=str(self.prefix), WINEDEBUG="-all")

    def _reg(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.wine), "reg", *args], env=self.env, capture_output=True,
                              text=True, errors="replace")

    def read(self, key: str, name: str) -> Optional[str]:
        """The REG_SZ value `name` under `key`, or None when it is not set."""
        done = self._reg("query", key, "/v", name)
        found = re.search(re.escape(name) + r"\s+REG_SZ\s+(\S*)", done.stdout)
        return found.group(1) if done.returncode == 0 and found else None

    def write(self, key: str, name: str, value: Optional[str]) -> None:
        """Set `name` under `key` to the string `value`; None deletes it."""
        if value is None:
            if self.read(key, name) is None:
                return
            done = self._reg("delete", key, "/v", name, "/f")
        else:
            done = self._reg("add", key, "/v", name, "/t", "REG_SZ", "/d", value, "/f")
        if done.returncode != 0:
            raise GfxError(f"wine reg failed: {(done.stderr or done.stdout).strip()}")
        # Wait for the prefix's wineserver to save the registry and exit.
        subprocess.run([str(self.wineserver), "-w"], env=self.env, check=False)

    def retina(self) -> Optional[str]:
        return self.read(MAC_DRIVER_KEY, "RetinaMode")

    def set_retina(self, value: Optional[str]) -> None:
        self.write(MAC_DRIVER_KEY, "RetinaMode", value)


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
          get_registry: Callable[[], WineRegistry], is_running=game_running,
          prefix: Optional[Path] = None, refresh: Iterable[str] = (), log=print) -> None:
    """Make `wanted` the exact set of enabled display patches (ids of `catalog`).
    The user.dat options of patches turning on, and of the `refresh` ones, are set
    in `prefix`; those of patches turning off are put back there."""
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
        options_on = [p for p in catalog if p in wanted and "userdat" in catalog[p]
                      and (p not in state["enabled"] or p in refresh)]
        options_off = [p for p in catalog if p in state["enabled"] and p not in wanted and "userdat" in catalog[p]]
        if (options_on or options_off) and prefix is None:
            raise GfxError(f"the user.dat options of {', '.join(options_on + options_off)} need "
                           "--prefix <wine prefix>")
        if options_on:
            userdat.existing_user_dats(app, [prefix])
        # Everything that can refuse comes before the first change: the registry is opened
        # (and Retina mode changed) before user.dat is touched, which a disable could not undo
        # for a patch left out of the enabled list.
        retina_on = any(catalog[p]["kind"] == "retina" for p in wanted)
        retina_was = any(catalog.get(p, {}).get("kind") == "retina" for p in state["enabled"])
        registry = get_registry() if retina_on != retina_was else None
        if retina_on and not retina_was:
            state["retina_before"] = registry.retina()
            write_json(state_path, state)  # recorded before the change, so disable can restore it
            registry.set_retina("y")
        elif retina_was and not retina_on:
            registry.set_retina(state.pop("retina_before", None))

        for p in options_off:
            for section, options in catalog[p]["userdat"].items():
                for key in options:
                    userdat.restore(app, [prefix], section, key, is_running=is_running, log=log)
        for p in options_on:
            for section, options in catalog[p]["userdat"].items():
                for key, value in options.items():
                    userdat.set(app, [prefix], section, key, value, is_running=is_running, log=log)

        if keys:
            conf.write_text(render_conf(conf_ids, keys), encoding="utf-8")
        elif conf.exists() and conf_is_ours(conf):
            conf.unlink()

        state["enabled"] = [p for p in catalog if p in wanted]
        if state["enabled"]:
            write_json(state_path, state)
        elif state_path.exists():
            state_path.unlink()
