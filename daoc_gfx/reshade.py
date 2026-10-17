"""The `reshade` patch: crosire's ReShade (the add-on build) as the client
folder's d3d9.dll, chained to mtld3d, with a pinned set of effects.

Turning it on downloads the pinned files (reshade.json; nothing third-party is
committed here), checks their sha256, and puts next to game.dll:

  d3d9.dll             ReShade32.dll from the official installer
  d3dcompiler_47.dll   Microsoft's 32-bit compiler; the arm64 Wine's own
                       (vkd3d-shader) rejects ReShade's effects
  reshade-shaders/     the effects of the pinned shader repositories
  ReShade.ini          search paths, DAoC's depth layout, the overlay key (F12)
  reshade-presets/     ReShadePreset.ini, a starter preset
  daoc-world.addon32   this repo's ReShade add-on (addon/): effects before the
                       UI, a larger water reflection
  daoc-world.ini       its settings (from addon/daoc-world.ini)

It sets the launcher line to d3d9=n,b (launcher.py) and, in the prefix,
HKCU\\Software\\Wine\\AppDefaults\\game.dll\\DllOverrides d3dcompiler_47 to
native,builtin, recording the value from before for each prefix it set.

Turning it off removes the files it put there, puts the launcher line and the
registry value back, and keeps ReShade.ini, daoc-world.ini and reshade-presets/
(your settings and presets, which ReShade rewrites as you tune them) aside in
.daoc-gfx/reshade-kept/ for the next time it is turned on. Screenshots
(reshade-screenshots/) are yours and stay.

It needs the client folder's d3d9.dll slot, as the `anisotropy` patch does, so
the two are never on together.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from . import anisotropy, launcher as launcher_line
from .common import GfxError, game_running, operation_lock, read_json, refuse_if_running, state_dir, write_json
from .textures import TAR

NAME = "reshade"
SOURCES: dict = json.loads(Path(__file__).with_name("reshade.json").read_text(encoding="utf-8"))
CURL = "/usr/bin/curl"

TARGET = "d3d9.dll"
COMPILER = "d3dcompiler_47.dll"
SHADERS = "reshade-shaders"
PRESETS = "reshade-presets"
PRESET = "ReShadePreset.ini"
INI = "ReShade.ini"
LOG = "ReShade.log"
STATE = "reshade.json"
KEPT = "reshade-kept"
STAGING = "reshade-staging"
DOWNLOADS = "downloads"
RESHADE_MARKER = b"crosire's ReShade"
ADDON = Path(__file__).with_name("daoc-world.addon32")
ADDON_INI = Path(__file__).resolve().parent.parent / "addon" / "daoc-world.ini"
ADDON_TARGET = ADDON.name
ADDON_INI_TARGET = ADDON_INI.name
ADDON_LOG = "daoc-world.log"

OVERRIDE_KEY = r"HKCU\Software\Wine\AppDefaults\game.dll\DllOverrides"
OVERRIDE_NAME = "d3dcompiler_47"
OVERRIDE_VALUE = "native,builtin"

ON, OFF, PARTLY_ON = "on", "off", "partly on"

# DAoC's depth is not reversed (ReShade.fxh assumes it is); F12 is free in the
# client's default key map, while ReShade's default Home also moves the camera.
INI_TEXT = """[GENERAL]
EffectSearchPaths=.\\reshade-shaders\\Shaders\\**
TextureSearchPaths=.\\reshade-shaders\\Textures\\**
PresetPath=.\\reshade-presets\\ReShadePreset.ini
PreprocessorDefinitions=RESHADE_DEPTH_INPUT_IS_REVERSED=0

[INPUT]
KeyOverlay=123,0,0,0

[SCREENSHOT]
SavePath=.\\reshade-screenshots
"""
PRESET_TEXT = """Techniques=SMAA@SMAA.fx,LumaSharpen@LumaSharpen.fx,Vibrance@Vibrance.fx

[Vibrance.fx]
Vibrance=0.150000
"""


def _crlf(text: str) -> bytes:
    return text.replace("\n", "\r\n").encode("utf-8")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree_sha256(root: Path, dirs: Iterable[str] = ("Shaders", "Textures")) -> str:
    """One hash over the files under `dirs` of a shader repository: each file's
    '/'-separated path and sha256, sorted by path. It does not depend on how the
    repository was packed, so a re-compressed archive of the same commit matches."""
    rows = []
    for d in dirs:
        base = root / d
        for path in sorted(base.rglob("*")) if base.is_dir() else []:
            if path.is_file():
                rows.append(f"{path.relative_to(root).as_posix()}\0{_sha256(path)}\n")
    return hashlib.sha256("".join(sorted(rows)).encode("utf-8")).hexdigest()


def download(url: str, dest: Path) -> None:
    """Fetch `url` to `dest` (via a .part file)."""
    part = dest.with_name(dest.name + ".part")
    done = subprocess.run([CURL, "-fsSL", "--retry", "2", "-o", str(part), url],
                          capture_output=True, text=True, errors="replace")
    if done.returncode != 0:
        part.unlink(missing_ok=True)
        raise GfxError(f"download failed: {url}: {(done.stderr or '').strip() or f'curl exit {done.returncode}'}")
    os.replace(part, dest)


# ------------------------------------------------------------------ state

def d3d9_state(app: Path) -> str:
    """'absent', 'current' (the pinned ReShade), 'older' (another ReShade build),
    'anisotropy' (that patch's proxy) or 'foreign'."""
    target = app / TARGET
    if not target.exists():
        return "absent"
    if _sha256(target) == SOURCES["reshade"]["member_sha256"]:
        return "current"
    data = target.read_bytes()
    if anisotropy.PROXY_MARKER in data:
        return "anisotropy"
    return "older" if RESHADE_MARKER in data else "foreign"


def _compiler_current(app: Path) -> bool:
    path = app / COMPILER
    return path.is_file() and _sha256(path) == SOURCES["d3dcompiler_47"]["member_sha256"]


def _addon_current(app: Path) -> bool:
    path = app / ADDON_TARGET
    return path.is_file() and path.read_bytes() == ADDON.read_bytes()


def _copy(src: Path, dest: Path) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


def _shader_commits() -> List[str]:
    return [s["commit"] for s in SOURCES["shaders"]]


def _record(app: Path) -> Optional[dict]:
    return read_json(state_dir(app) / STATE)


def launcher_for(app: Path, launcher: Optional[Path]) -> Path:
    return launcher_line.launcher_for(app, launcher, STATE)


def state(app: Path, launcher: Optional[Path] = None, prefix: Optional[Path] = None) -> str:
    """On when every piece is in place (for `prefix`, when given, its registry
    value too); off when none of the client-folder pieces is."""
    record = _record(app)
    d3d9 = d3d9_state(app)
    owner = launcher_line.state(launcher_for(app, launcher))
    if record is None and d3d9 != "current" and owner != NAME:
        return OFF
    if (record is not None and d3d9 == "current" and owner == NAME and _compiler_current(app)
            and _addon_current(app)
            and (app / SHADERS).is_dir() and record.get("shaders") == _shader_commits()
            and (prefix is None or str(prefix.resolve()) in record.get("overrides", {}))):
        return ON
    return PARTLY_ON


# ---------------------------------------------------------------- fetching

def _cached(app: Path, url: str, sha256: Optional[str], fetch: Callable[[str, Path], None]) -> Path:
    """The download of `url` in .daoc-gfx/downloads, fetched when missing or when
    its sha256 is not `sha256` (None: no hash to check, e.g. a source tarball)."""
    folder = state_dir(app) / DOWNLOADS
    folder.mkdir(exist_ok=True)
    dest = folder / url.rstrip("/").rsplit("/", 1)[-1]
    if dest.exists() and sha256 is not None and _sha256(dest) != sha256:
        dest.unlink()
    if not dest.exists():
        fetch(url, dest)
        if sha256 is not None and _sha256(dest) != sha256:
            got = _sha256(dest)
            dest.unlink()
            raise GfxError(f"{url} has sha256 {got}, not the pinned {sha256}")
    return dest


def _extract_member(app: Path, spec: dict, into: Path, fetch) -> Path:
    archive = _cached(app, spec["url"], spec["sha256"], fetch)
    into.mkdir(parents=True, exist_ok=True)
    done = subprocess.run([TAR, "-xf", str(archive), "-C", str(into), spec["member"]],
                          capture_output=True, text=True, errors="replace")
    member = into / spec["member"]
    if done.returncode != 0 or not member.is_file():
        raise GfxError(f"cannot extract {spec['member']} from {archive.name}: {done.stderr.strip()}")
    if _sha256(member) != spec["member_sha256"]:
        raise GfxError(f"{spec['member']} in {archive.name} is not the pinned file")
    return member


def _stage_shaders(app: Path, staging: Path, fetch) -> Path:
    """Every pinned repository's Shaders/ and Textures/, checked, merged into one folder."""
    merged = staging / SHADERS
    for spec in SOURCES["shaders"]:
        url = f"{spec['repo']}/archive/{spec['commit']}.tar.gz"
        repo = staging / spec["commit"]
        for attempt in (1, 2):  # a stale cached archive is fetched once more
            archive = _cached(app, url, None, fetch)
            shutil.rmtree(repo, ignore_errors=True)
            repo.mkdir(parents=True)
            done = subprocess.run([TAR, "-xzf", str(archive), "-C", str(repo), "--strip-components", "1"],
                                  capture_output=True, text=True, errors="replace")
            if done.returncode == 0 and tree_sha256(repo) == spec["tree_sha256"]:
                break
            archive.unlink()
            if attempt == 2:
                raise GfxError(f"{url} does not hold the pinned shader files "
                               f"({done.stderr.strip() or 'tree hash differs'})")
        for d in ("Shaders", "Textures"):
            for path in sorted((repo / d).rglob("*")) if (repo / d).is_dir() else []:
                if path.is_file():
                    dest = merged / path.relative_to(repo)
                    if dest.exists():
                        raise GfxError(f"two shader repositories have {path.relative_to(repo)}")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(path, dest)
        shutil.rmtree(repo)
    return merged


# --------------------------------------------------------------- on and off

def _check_launcher(where: Path) -> str:
    script = launcher_line.state(where)
    if script == launcher_line.MISSING:
        raise GfxError(f"no OfflineDAoC-macOS launcher at {where}; give its .app with --launcher")
    if script == anisotropy.NAME:
        raise GfxError("the launcher is set for anisotropy; anisotropy and reshade cannot both be on: "
                       "turn anisotropy off first")
    if script not in (launcher_line.STOCK, NAME):
        raise GfxError(f"{where / launcher_line.SCRIPT} has no WINEDLLOVERRIDES line this patch knows; "
                       f"it expects: {launcher_line.STOCK_LINE}")
    return script


def _check_slot(app: Path, record: Optional[dict]) -> None:
    d3d9 = d3d9_state(app)
    if d3d9 == "anisotropy":
        raise GfxError("the anisotropy patch's d3d9.dll is in the client folder; anisotropy and reshade "
                       "cannot both be on: turn anisotropy off first")
    if record is not None:
        if d3d9 == "foreign":
            raise GfxError(f"{app / TARGET} is not ReShade; move it aside to enable reshade")
        return
    mine = [name for name, there in (
        (TARGET, d3d9 not in ("absent", "current")),
        (COMPILER, (app / COMPILER).exists() and not _compiler_current(app)),
        (INI, (app / INI).exists()), (SHADERS, (app / SHADERS).exists()),
        (PRESETS, (app / PRESETS).exists())) if there]
    if mine:
        raise GfxError(f"the client folder already has {', '.join(mine)} of your own (a ReShade you "
                       "installed?); move them aside to enable reshade")


def _place(src: Path, dest: Path) -> None:
    """Move `src` over `dest` (a file, or a folder replacing any folder there)."""
    if src.is_dir() and dest.exists():
        shutil.rmtree(dest)
    os.replace(src, dest)


def enable(app: Path, get_registry: Callable, launcher: Optional[Path] = None, is_running=game_running,
           log=print, fetch: Optional[Callable[[str, Path], None]] = None) -> None:
    """Turn the patch on, or finish it (a missing piece, an older pinned file, a
    fresh launcher, another prefix)."""
    fetch = fetch or download
    with operation_lock(app):
        refuse_if_running(app, is_running)
        where = launcher_for(app, launcher)
        script = _check_launcher(where)
        record = _record(app)
        _check_slot(app, record)
        registry = get_registry()

        staging = state_dir(app) / STAGING
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        need_d3d9 = d3d9_state(app) != "current"
        need_compiler = not _compiler_current(app)
        need_shaders = not (app / SHADERS).is_dir() or (record or {}).get("shaders") != _shader_commits()
        if need_d3d9:
            log(f"reshade: ReShade {SOURCES['reshade']['version']}")
            d3d9 = _extract_member(app, SOURCES["reshade"], staging / "reshade", fetch)
        if need_compiler:
            log("reshade: d3dcompiler_47")
            compiler = _extract_member(app, SOURCES["d3dcompiler_47"], staging / "compiler", fetch)
        if need_shaders:
            log("reshade: effects")
            shaders = _stage_shaders(app, staging, fetch)

        # Recorded before the first change, so 'disable' can undo a half-done enable.
        record = dict(record or {}, overrides=(record or {}).get("overrides", {}))
        if launcher is not None:
            record["launcher"] = str(where)
        record_path = state_dir(app) / STATE
        write_json(record_path, record)

        if need_shaders:
            _place(shaders, app / SHADERS)
            record["shaders"] = _shader_commits()
            write_json(record_path, record)
        if need_compiler:
            _place(compiler, app / COMPILER)
        kept = state_dir(app) / KEPT  # your settings from the last time it was on
        for name in (INI, ADDON_INI_TARGET, PRESETS):
            if not (app / name).exists() and (kept / name).exists():
                _place(kept / name, app / name)
        if not (app / INI).exists():
            (app / INI).write_bytes(_crlf(INI_TEXT))
        if not (app / ADDON_INI_TARGET).exists():
            _copy(ADDON_INI, app / ADDON_INI_TARGET)
        if not _addon_current(app):
            _copy(ADDON, app / ADDON_TARGET)
        if not (app / PRESETS).exists():
            (app / PRESETS).mkdir()
            (app / PRESETS / PRESET).write_bytes(_crlf(PRESET_TEXT))
        if need_d3d9:
            _place(d3d9, app / TARGET)
        if script == launcher_line.STOCK:
            launcher_line.replace_line(where, launcher_line.STOCK_LINE, launcher_line.patched_line(NAME))

        prefix = str(registry.prefix)
        if prefix not in record["overrides"]:
            before = registry.read(OVERRIDE_KEY, OVERRIDE_NAME)
            # A value this patch left in a prefix (e.g. before a client update replaced
            # the folder and this record) is not the value to restore.
            record["overrides"][prefix] = None if before == OVERRIDE_VALUE else before
            write_json(record_path, record)
        registry.write(OVERRIDE_KEY, OVERRIDE_NAME, OVERRIDE_VALUE)
        shutil.rmtree(staging, ignore_errors=True)


def disable(app: Path, get_registry: Callable, launcher: Optional[Path] = None, is_running=game_running) -> None:
    """Turn the patch off. The registry value goes back in the prefix given; a
    prefix it was set in that is not given keeps native,builtin, which only means
    the builtin compiler once d3dcompiler_47.dll is gone."""
    with operation_lock(app):
        refuse_if_running(app, is_running)
        record = _record(app)
        where = launcher_for(app, launcher)
        overrides: Dict[str, Optional[str]] = (record or {}).get("overrides", {})
        registry = get_registry() if overrides else None

        if launcher_line.state(where) == NAME:
            launcher_line.replace_line(where, launcher_line.patched_line(NAME), launcher_line.STOCK_LINE)
        d3d9 = d3d9_state(app)
        if d3d9 == "current" or (record is not None and d3d9 == "older"):
            (app / TARGET).unlink()
        if _compiler_current(app) or (record is not None and (app / COMPILER).is_file()):
            (app / COMPILER).unlink()
        if record is not None:
            if (app / SHADERS).is_dir():
                shutil.rmtree(app / SHADERS)
            (app / LOG).unlink(missing_ok=True)
            (app / ADDON_TARGET).unlink(missing_ok=True)
            (app / ADDON_LOG).unlink(missing_ok=True)
            kept = state_dir(app) / KEPT
            for name in (INI, ADDON_INI_TARGET, PRESETS):
                if (app / name).exists():
                    kept.mkdir(exist_ok=True)
                    if (kept / name).is_dir():
                        shutil.rmtree(kept / name)
                    os.replace(app / name, kept / name)
        if registry is not None and str(registry.prefix) in overrides:
            registry.write(OVERRIDE_KEY, OVERRIDE_NAME, overrides[str(registry.prefix)])
        shutil.rmtree(state_dir(app) / STAGING, ignore_errors=True)
        (state_dir(app) / STATE).unlink(missing_ok=True)
