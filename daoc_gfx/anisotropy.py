"""The `anisotropy` patch: a d3d9.dll proxy in the client folder that has
mtld3d filter the game's mipmapped textures anisotropically (proxy/), and the
one launcher line that lets Wine load it (launcher.py).

Turning the patch on copies the proxy next to game.dll and sets the launcher
line to d3d9=n,b; turning it off undoes both. The proxy and the launcher line
mark themselves, so the files are the record. A reinstall that copies a fresh
launcher leaves the patch partly on; enabling it again finishes it. The
`reshade` patch needs the same d3d9.dll slot, so the two are never on together.

The level (2x to 16x) is daoc-gfx-d3d9.conf next to game.dll, which the proxy
reads at load; without it the proxy uses 16x.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from . import launcher as launcher_line
from .common import GfxError, game_running, operation_lock, refuse_if_running, state_dir, write_json

PROXY = Path(__file__).with_name("d3d9.dll")
PROXY_MARKER = b"daoc-gfx d3d9 anisotropy proxy"
PROXY_LOG = "daoc-gfx-d3d9.log"
CONF = "daoc-gfx-d3d9.conf"
CONF_MARKER = "# Written by daoc-gfx (OfflineDAoC-macOS-graphics): the anisotropy patch's level"
LEVELS = (2, 4, 8, 16)
DEFAULT_LEVEL = 16
_LEVEL_KEY = re.compile(r"[ \t]*anisotropy[ \t]*=")
_LEVEL_LINE = re.compile(r"[ \t]*anisotropy[ \t]*=[ \t]*(\d{1,3})[ \t\r]*")
TARGET = "d3d9.dll"
STATE = "anisotropy.json"
NAME = "anisotropy"

# daoc-macos's `daoc graphics update` reads STOCK_LINE from this module to check
# its launcher still carries the line; keep the name here.
STOCK_LINE = launcher_line.STOCK_LINE
PATCHED_LINE = launcher_line.patched_line(NAME)

ON, OFF, PARTLY_ON = "on", "off", "partly on"


def launcher_for(app: Path, launcher: Optional[Path]) -> Path:
    return launcher_line.launcher_for(app, launcher, STATE)


def proxy_state(app: Path) -> str:
    """'absent', 'current' (the shipped proxy), 'older' (an earlier build of it)
    or 'foreign' (some other d3d9.dll, e.g. ReShade)."""
    target = app / TARGET
    if not target.exists():
        return "absent"
    data = target.read_bytes()
    if data == PROXY.read_bytes():
        return "current"
    return "older" if PROXY_MARKER in data else "foreign"


def state(app: Path, launcher: Optional[Path] = None) -> str:
    proxy = proxy_state(app)
    script = launcher_line.state(launcher_for(app, launcher))
    if proxy == "current" and script == NAME:
        return ON
    if proxy in ("absent", "foreign") and script != NAME:
        return OFF
    return PARTLY_ON


def level(app: Path) -> int:
    """The level the proxy uses: the conf file's, else the default. Mirrors
    gfx_parse_level in proxy/sampler.c (first `anisotropy = N`, N in 1..16)."""
    conf = app / CONF
    if conf.exists():
        for line in conf.read_text(encoding="utf-8", errors="replace").split("\n"):
            if _LEVEL_KEY.match(line):
                found = _LEVEL_LINE.fullmatch(line)
                value = int(found.group(1)) if found else 0
                return value if 1 <= value <= 16 else DEFAULT_LEVEL
    return DEFAULT_LEVEL


def _write_conf(app: Path, value: int) -> None:
    tmp = app / (CONF + ".daoc-gfx.tmp")
    tmp.write_text(f"{CONF_MARKER}\nanisotropy = {value}\n", encoding="utf-8")
    os.replace(tmp, app / CONF)


def _conf_is_ours(app: Path) -> bool:
    conf = app / CONF
    if not conf.exists():
        return False
    with open(conf, encoding="utf-8", errors="replace") as f:
        return f.readline().rstrip("\n") == CONF_MARKER


def _copy_proxy(app: Path) -> None:
    tmp = app / (TARGET + ".daoc-gfx.tmp")
    tmp.write_bytes(PROXY.read_bytes())
    os.replace(tmp, app / TARGET)


def enable(app: Path, launcher: Optional[Path] = None, is_running=game_running,
           level_wanted: Optional[int] = None) -> None:
    """Turn the patch on, or finish it; `level_wanted` sets the level (else the
    current one stays)."""
    if level_wanted is not None and level_wanted not in LEVELS:
        raise GfxError(f"anisotropy level {level_wanted}: choose from {', '.join(map(str, LEVELS))}")
    with operation_lock(app):
        refuse_if_running(app, is_running)
        where = launcher_for(app, launcher)
        script = launcher_line.state(where)
        if script == launcher_line.MISSING:
            raise GfxError(f"no OfflineDAoC-macOS launcher at {where}; give its .app with --launcher")
        if script not in (launcher_line.STOCK, NAME):
            owner = f"is set for the {script} patch" if script != launcher_line.UNKNOWN else \
                "has no WINEDLLOVERRIDES line this patch knows"
            raise GfxError(f"{where / launcher_line.SCRIPT} {owner}; it expects: {STOCK_LINE}")
        if proxy_state(app) == "foreign":
            raise GfxError(f"{app / TARGET} is not daoc-gfx's anisotropy proxy (the reshade patch's ReShade, "
                           "or another wrapper); turn reshade off or move it aside to enable anisotropy")
        if launcher is not None:
            write_json(state_dir(app) / STATE, {"launcher": str(where)})
        _write_conf(app, level_wanted if level_wanted is not None else level(app))
        if proxy_state(app) != "current":
            _copy_proxy(app)
        if script == launcher_line.STOCK:
            launcher_line.replace_line(where, STOCK_LINE, PATCHED_LINE)


def disable(app: Path, launcher: Optional[Path] = None, is_running=game_running) -> None:
    with operation_lock(app):
        refuse_if_running(app, is_running)
        where = launcher_for(app, launcher)
        if launcher_line.state(where) == NAME:
            launcher_line.replace_line(where, PATCHED_LINE, STOCK_LINE)
        if proxy_state(app) in ("current", "older"):
            (app / TARGET).unlink()
        if (app / PROXY_LOG).exists():
            (app / PROXY_LOG).unlink()
        if _conf_is_ours(app):
            (app / CONF).unlink()
        record = state_dir(app) / STATE
        if record.exists():
            record.unlink()
