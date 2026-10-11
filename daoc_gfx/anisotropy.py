"""The `anisotropy` patch: a d3d9.dll proxy in the client folder that has
mtld3d filter the game's mipmapped textures anisotropically (proxy/), and the
one launcher line that lets Wine load it.

OfflineDAoC-macOS's launcher (dist/DAoCLauncher.app, a copy install-client.sh
makes of its client/DAoCLauncher.app) exports WINEDLLOVERRIDES with d3d9=b,
which keeps any d3d9.dll in the client folder from loading. Turning the patch on
copies the proxy next to game.dll and changes that default to d3d9=n,b in the
installed launcher; turning it off undoes both. The proxy and the launcher line
mark themselves, so the files are the record. A reinstall that copies a fresh
launcher leaves the patch partly on; enabling it again finishes it.

The level (2x to 16x) is daoc-gfx-d3d9.conf next to game.dll, which the proxy
reads at load; without it the proxy uses 16x.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .common import GfxError, game_running, operation_lock, read_json, refuse_if_running, state_dir, write_json

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

LAUNCHER_SCRIPT = Path("Contents") / "MacOS" / "launch"
STOCK_LINE = 'export WINEDLLOVERRIDES="${DAOC_DLL_OVERRIDES:-d3d9,dxgi=b}"'
PATCHED_LINE = ('export WINEDLLOVERRIDES="${DAOC_DLL_OVERRIDES:-d3d9=n,b;dxgi=b}"'
                "  # daoc-gfx anisotropy: the client folder's d3d9.dll first")

ON, OFF, PARTLY_ON = "on", "off", "partly on"


def default_launcher(app: Path) -> Path:
    """dist/DAoCLauncher.app for an OfflineDAoC-macOS client at
    dist/OfflineDAoC/runtime/client-opendaoc/app (the launcher finds the client
    the same way, from its own location)."""
    parents = app.parents
    return (parents[3] if len(parents) > 3 else app) / "DAoCLauncher.app"


def launcher_for(app: Path, launcher: Optional[Path]) -> Path:
    """The launcher given, else the one recorded when the patch was turned on,
    else the default."""
    if launcher is not None:
        return launcher.expanduser().resolve()
    state = read_json(state_dir(app) / STATE)
    return Path(state["launcher"]) if state else default_launcher(app)


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


def launcher_state(launcher: Path) -> str:
    """'patched', 'stock', 'missing' or 'unknown' (neither line, exactly once)."""
    script = launcher / LAUNCHER_SCRIPT
    if not script.is_file():
        return "missing"
    lines = [line.strip() for line in script.read_text(encoding="utf-8").splitlines()]
    if lines.count(PATCHED_LINE) == 1 and STOCK_LINE not in lines:
        return "patched"
    if lines.count(STOCK_LINE) == 1 and PATCHED_LINE not in lines:
        return "stock"
    return "unknown"


def state(app: Path, launcher: Optional[Path] = None) -> str:
    proxy = proxy_state(app)
    script = launcher_state(launcher_for(app, launcher))
    if proxy == "current" and script == "patched":
        return ON
    if proxy in ("absent", "foreign") and script != "patched":
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


def _replace_line(script: Path, old: str, new: str) -> None:
    """Swap the one line `old` (indentation kept) for `new`, atomically, keeping the mode."""
    text = script.read_text(encoding="utf-8")
    out = []
    for line in text.splitlines(keepends=True):
        if line.strip() == old:
            indent = line[: len(line) - len(line.lstrip())]
            ending = line[len(line.rstrip("\r\n")):]
            line = f"{indent}{new}{ending}"
        out.append(line)
    tmp = script.with_name(script.name + ".daoc-gfx.tmp")
    tmp.write_text("".join(out), encoding="utf-8")
    os.chmod(tmp, script.stat().st_mode)
    os.replace(tmp, script)


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
        script = launcher_state(where)
        if script == "missing":
            raise GfxError(f"no OfflineDAoC-macOS launcher at {where}; give its .app with --launcher")
        if script == "unknown":
            raise GfxError(f"{where / LAUNCHER_SCRIPT} has no WINEDLLOVERRIDES line this patch knows; "
                           f"it expects: {STOCK_LINE}")
        if proxy_state(app) == "foreign":
            raise GfxError(f"{app / TARGET} is not daoc-gfx's (ReShade or another wrapper?); "
                           "move it aside to enable anisotropy")
        write_json(state_dir(app) / STATE, {"launcher": str(where)})
        _write_conf(app, level_wanted if level_wanted is not None else level(app))
        if proxy_state(app) != "current":
            _copy_proxy(app)
        if script == "stock":
            _replace_line(where / LAUNCHER_SCRIPT, STOCK_LINE, PATCHED_LINE)


def disable(app: Path, launcher: Optional[Path] = None, is_running=game_running) -> None:
    with operation_lock(app):
        refuse_if_running(app, is_running)
        where = launcher_for(app, launcher)
        if launcher_state(where) == "patched":
            _replace_line(where / LAUNCHER_SCRIPT, PATCHED_LINE, STOCK_LINE)
        if proxy_state(app) in ("current", "older"):
            (app / TARGET).unlink()
        for name in (PROXY_LOG, CONF):
            if (app / name).exists():
                (app / name).unlink()
        record = state_dir(app) / STATE
        if record.exists():
            record.unlink()
