"""The patch catalog (patches.json), each patch's on/off state, and turning
patches on and off individually or from an interactive checklist."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Sequence, Set

from . import anisotropy, display, textures
from .common import GfxError, game_running

CATALOG_FILE = Path(__file__).with_name("patches.json")
DISPLAY_KINDS = ("retina", "mtld3d")

ON, OFF, PARTLY_ON = "on", "off", "partly on"
NOT_INSTALLED = "not installed"
NEEDS_RECOVER = "needs 'textures recover'"


def catalog() -> Dict[str, dict]:
    return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))


def check_ids(cat: Dict[str, dict], ids: Iterable[str]) -> None:
    for p in ids:
        if p not in cat:
            raise GfxError(f"no patch '{p}'; choose from: {', '.join(cat)}")


def texture_state(app: Path) -> str:
    """On when every installed folder has its pack files live, off when none has."""
    if textures.pending(app):
        return NEEDS_RECOVER
    states = {state for _, state in textures.status(app)}
    if not states:
        return NOT_INSTALLED
    if states == {textures.PACK_ACTIVE}:
        return ON
    if states == {textures.ORIGINAL_ACTIVE}:
        return OFF
    if states <= {textures.PACK_ACTIVE, textures.ORIGINAL_ACTIVE}:
        return PARTLY_ON
    return NEEDS_RECOVER


def states(app: Path, launcher: Optional[Path] = None) -> Dict[str, str]:
    cat = catalog()
    shown = set(display.enabled(app))

    def one(p: str, kind: str) -> str:
        if kind == "textures":
            return texture_state(app)
        if kind == "anisotropy":
            return anisotropy.state(app, launcher)
        return ON if p in shown else OFF
    return {p: one(p, spec["kind"]) for p, spec in cat.items()}


def change(app: Path, enable: Set[str], disable: Set[str], get_registry: Callable,
           archives: Optional[Sequence[Path]] = None, is_running=game_running, log=print,
           launcher: Optional[Path] = None, level: Optional[int] = None) -> None:
    """Turn the `enable` patches on and the `disable` ones off; leave every other patch alone.
    `level` is the anisotropy patch's level, when it is being turned on."""
    cat = catalog()
    check_ids(cat, enable | disable)
    both = enable & disable
    if both:
        raise GfxError(f"both on and off: {', '.join(sorted(both))}")
    if level is not None and not any(cat[p]["kind"] == "anisotropy" for p in enable):
        raise GfxError("a level is only for enable anisotropy")

    display_ids = {p for p in cat if cat[p]["kind"] in DISPLAY_KINDS}
    current = set(display.enabled(app))
    wanted = (current | (enable & display_ids)) - disable
    if wanted != current:
        display.apply(app, cat, wanted, get_registry, is_running)

    for p in (p for p in cat if cat[p]["kind"] == "anisotropy"):
        if p in enable:
            anisotropy.enable(app, launcher, is_running, level)
        elif p in disable:
            anisotropy.disable(app, launcher, is_running)

    for p in (p for p in cat if cat[p]["kind"] == "textures"):
        state = texture_state(app)
        if state == NEEDS_RECOVER and p in enable | disable:
            raise GfxError("the texture pack needs 'textures recover' first")
        if p in enable:
            if state == NOT_INSTALLED:
                if not archives:
                    raise GfxError("the texture pack is not installed; give its archives "
                                   "(enable textures --archives <part>...)")
                textures.install(app, archives, is_running=is_running, log=log)
            elif state != ON:
                textures.use(app, "pack", is_running=is_running, log=log)
        elif p in disable and state in (ON, PARTLY_ON):
            textures.use(app, "original", is_running=is_running, log=log)


def select(app: Path, get_registry: Callable, ask: Callable[[str], str] = input, out=print,
           is_running=game_running, log=print, launcher: Optional[Path] = None) -> bool:
    """Interactive checklist. Returns whether anything changed."""
    cat = catalog()
    ids = list(cat)
    current = states(app, launcher)
    chosen = {p for p in ids if current[p] == ON}
    partly = {p for p in ids if current[p] == PARTLY_ON}  # left as is unless toggled
    while True:
        out("")
        for n, p in enumerate(ids, 1):
            mark = "x" if p in chosen else ("~" if p in partly else " ")
            note = f"  ({current[p]})" if current[p] in (NOT_INSTALLED, NEEDS_RECOVER, PARTLY_ON) else ""
            out(f"  {n}. [{mark}] {cat[p]['title']}{note}")
            out(f"         {cat[p]['description']}")
        try:
            answer = ask("Toggle by number (e.g. 1 3), Enter to apply, q to quit: ").strip()
        except EOFError:
            answer = "q"
        if answer.lower() == "q":
            out("nothing changed")
            return False
        if not answer:
            break
        picks = answer.replace(",", " ").split()
        bad = [t for t in picks if not t.isdigit() or not 1 <= int(t) <= len(ids)]
        if bad:
            out(f"not a choice: {' '.join(bad)}")
            continue
        for t in picks:
            p = ids[int(t) - 1]
            partly.discard(p)
            chosen ^= {p}

    enable = {p for p in chosen if current[p] != ON}
    disable = {p for p in ids if p not in chosen and p not in partly and current[p] in (ON, PARTLY_ON)}
    if not enable and not disable:
        out("nothing changed")
        return False
    archives = None
    if any(cat[p]["kind"] == "textures" and current[p] == NOT_INSTALLED for p in enable):
        try:
            raw = ask("Texture pack archives, every part (drag them here), then Enter: ")
        except EOFError:
            raw = ""
        archives = [Path(a).expanduser() for a in shlex.split(raw)]
    level = None
    if any(cat[p]["kind"] == "anisotropy" for p in enable):
        level = ask_level(app, ask, out)
    change(app, enable, disable, get_registry, archives, is_running=is_running, log=log, launcher=launcher,
           level=level)
    for p in ids:
        if p in enable:
            out(f"on : {cat[p]['title']}")
        elif p in disable:
            out(f"off: {cat[p]['title']}")
    out("Changes take effect at the next client launch.")
    return True


def ask_level(app: Path, ask: Callable[[str], str], out=print) -> int:
    choices = "/".join(map(str, anisotropy.LEVELS))
    current = anisotropy.level(app)
    default = current if current in anisotropy.LEVELS else anisotropy.DEFAULT_LEVEL
    while True:
        try:
            raw = ask(f"Anisotropic filtering level, {choices} [{default}]: ").strip().rstrip("xX")
        except EOFError:
            raw = ""
        if not raw:
            return default
        if raw.isdigit() and int(raw) in anisotropy.LEVELS:
            return int(raw)
        out(f"not a level: {raw} (choose {choices})")
