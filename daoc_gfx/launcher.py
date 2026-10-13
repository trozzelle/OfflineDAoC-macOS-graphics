"""The one WINEDLLOVERRIDES line of the OfflineDAoC-macOS launcher that the
patches loading a d3d9.dll from the client folder (`anisotropy`, `reshade`) change.

OfflineDAoC-macOS's launcher (dist/DAoCLauncher.app, a copy `daoc client install`
makes of its client/DAoCLauncher.app) exports WINEDLLOVERRIDES with d3d9=b,
which keeps any d3d9.dll in the client folder from loading. A patch that puts one
there changes that default to d3d9=n,b and names itself in a comment on the line;
only one patch can own the line, as only one d3d9.dll fits next to game.dll.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .common import read_json, state_dir

SCRIPT = Path("Contents") / "MacOS" / "launch"
STOCK_LINE = 'export WINEDLLOVERRIDES="${DAOC_DLL_OVERRIDES:-d3d9,dxgi=b}"'
_PATCHED = 'export WINEDLLOVERRIDES="${DAOC_DLL_OVERRIDES:-d3d9=n,b;dxgi=b}"  # daoc-gfx '
_PATCHED_END = ": the client folder's d3d9.dll first"

MISSING, STOCK, UNKNOWN = "missing", "stock", "unknown"


def patched_line(owner: str) -> str:
    return f"{_PATCHED}{owner}{_PATCHED_END}"


def default_launcher(app: Path) -> Path:
    """dist/DAoCLauncher.app for an OfflineDAoC-macOS client at
    dist/OfflineDAoC/runtime/client-opendaoc/app (the launcher finds the client
    the same way, from its own location)."""
    parents = app.parents
    return (parents[3] if len(parents) > 3 else app) / "DAoCLauncher.app"


def launcher_for(app: Path, launcher: Optional[Path], record: str) -> Path:
    """The launcher given, else the one the patch recorded (in its state file
    `record`) when turned on with --launcher, else the default (found from where
    the client is now, so a moved install still finds its own)."""
    if launcher is not None:
        return launcher.expanduser().resolve()
    state = read_json(state_dir(app) / record)
    return Path(state["launcher"]) if state and state.get("launcher") else default_launcher(app)


def state(launcher: Path) -> str:
    """'missing', 'stock', the owning patch's name, or 'unknown' (not exactly one
    known line)."""
    script = launcher / SCRIPT
    if not script.is_file():
        return MISSING
    lines = [line.strip() for line in script.read_text(encoding="utf-8").splitlines()]
    stock = lines.count(STOCK_LINE)
    owners = [line[len(_PATCHED):-len(_PATCHED_END)] for line in lines
              if line.startswith(_PATCHED) and line.endswith(_PATCHED_END)]
    if stock == 1 and not owners:
        return STOCK
    if stock == 0 and len(owners) == 1 and owners[0]:
        return owners[0]
    return UNKNOWN


def replace_line(launcher: Path, old: str, new: str) -> None:
    """Swap the one line `old` (indentation kept) for `new`, atomically, keeping the mode."""
    script = launcher / SCRIPT
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
