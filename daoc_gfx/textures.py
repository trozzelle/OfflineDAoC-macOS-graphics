"""Texture packs: user-supplied archives whose top-level folders are client folders.

Each top-level folder of a pack is one *component* (for example `zones` or
`figures`). Installing a component keeps the client's original folder as
`<name>.backup` (an APFS clone, so it costs no disk space) and writes the pack's
files over the live folder. Switching renames folders:

    pack active:      <name> = pack files,  <name>.backup      = originals
    originals active: <name> = originals,   <name>.texturepack = pack files

Every changing operation writes a journal first; `recover` finishes or undoes an
operation that was interrupted, and no other operation starts while one is
pending. The folder-variant scheme follows Sqeak's Windows installer
(github.com/pfisher99/Eden-DAoC-Texture-Installer).

`export` copies the files an installed pack added or changed out of the client,
laid out like a pack archive; installing from that folder puts them back, for
example into a replaced client.
"""
from __future__ import annotations

import filecmp
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .common import (GfxError, game_running, operation_lock, read_json, refuse_if_running,
                     state_dir, write_json)

BACKUP, PACK, SWAP, ROLLBACK = ".backup", ".texturepack", ".swap", ".rollback-"
VARIANT_SUFFIXES = (BACKUP, PACK, SWAP)

NOT_INSTALLED = "not installed"
PACK_ACTIVE = "pack active"
ORIGINAL_ACTIVE = "originals active"
MISSING = "missing"
CONFLICT = "conflict"

MANIFEST = "textures.json"
JOURNAL = "textures-journal.json"
STAGING = "staging"

# bsdtar (libarchive) ships with macOS and reads 7z, zip and tar archives; GNU tar
# earlier on PATH cannot read 7z, so prefer it by name.
TAR = shutil.which("bsdtar") or "/usr/bin/tar"


# --- folder states --------------------------------------------------------------

def detect(app: Path, name: str) -> str:
    active = (app / name).is_dir()
    backup = (app / (name + BACKUP)).is_dir()
    pack = (app / (name + PACK)).is_dir()
    swap = (app / (name + SWAP)).is_dir()
    rollback = any(p.name.startswith(name + ROLLBACK) for p in app.iterdir())
    if swap or rollback or (backup and pack) or (not active and (backup or pack)):
        return CONFLICT
    if not active:
        return MISSING
    if backup:
        return PACK_ACTIVE
    if pack:
        return ORIGINAL_ACTIVE
    return NOT_INSTALLED


def client_folders(app: Path) -> Dict[str, str]:
    """Lower-cased name -> real name of every plain client folder a pack may target."""
    folders = {}
    for p in app.iterdir():
        if not p.is_dir() or p.is_symlink() or p.name.startswith("."):
            continue
        if p.name.endswith(VARIANT_SUFFIXES) or ROLLBACK in p.name:
            continue
        folders[p.name.lower()] = p.name
    return folders


# --- archives -------------------------------------------------------------------

def entry_segments(raw: str, archive: Path) -> List[str]:
    norm = raw.replace("\\", "/")
    segments = [s for s in norm.split("/") if s]
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or not segments or \
            any(s in (".", "..") for s in segments):
        raise GfxError(f"{archive.name}: unsafe path in archive: {raw!r}")
    return segments


def list_archive(archive: Path) -> List[str]:
    if not archive.is_file():
        raise GfxError(f"archive not found: {archive}")
    listing = subprocess.run([TAR, "-tf", str(archive)], capture_output=True, text=True,
                             errors="surrogateescape")
    if listing.returncode != 0:
        raise GfxError(f"cannot read {archive.name}: {listing.stderr.strip()}")
    return [line for line in listing.stdout.splitlines() if line]


def inspect(app: Path, archives: Sequence[Path]) -> Dict[str, int]:
    """Client folder (real name) -> number of pack files for it, across all archives.

    Refuses unsafe paths, files outside a folder, folders the client does not
    have, and the same file in two places (paths compare case-insensitively, as
    the game's Windows file lookups do).
    """
    if not archives:
        raise GfxError("no archive given")
    folders = client_folders(app)
    counts: Dict[str, int] = {}
    seen: Dict[str, str] = {}
    for archive in archives:
        for raw in list_archive(archive):
            segments = entry_segments(raw, archive)
            top, is_dir = segments[0], raw.endswith("/")
            if len(segments) == 1 and not is_dir:
                raise GfxError(f"{archive.name}: '{top}' is a file; a pack holds client folders")
            real = folders.get(top.lower())
            if real is None:
                raise GfxError(f"{archive.name}: '{top}' is not a folder of this client")
            if is_dir:
                counts.setdefault(real, 0)
                continue
            key = "/".join(segments).lower()
            if key in seen:
                raise GfxError(f"'{raw}' is in {seen[key]} and {archive.name}")
            seen[key] = archive.name
            counts[real] = counts.get(real, 0) + 1
    # A folder with no files would be backed up and switched for nothing.
    return dict(sorted(((k, v) for k, v in counts.items() if v), key=lambda kv: kv[0].lower()))


def inspect_dir(app: Path, source: Path) -> Dict[str, int]:
    """inspect() for a pack folder: a folder laid out like a pack archive's top level
    (what export writes). Refuses anything but plain files and folders as well. A client
    folder with no files counts: export writes one for a component whose pack files all
    equal the originals, and installing it keeps the component installed."""
    if not source.is_dir():
        raise GfxError(f"pack folder not found: {source}")
    folders = client_folders(app)
    counts: Dict[str, int] = {}
    seen = set()
    for dirpath, dirnames, names in os.walk(source):
        for n in dirnames + names:
            p = Path(dirpath) / n
            rel = p.relative_to(source)
            if p.is_symlink() or not (p.is_dir() or p.is_file()):
                raise GfxError(f"{source.name}: not a plain file or folder: {rel}")
            if len(rel.parts) == 1 and not p.is_dir():
                raise GfxError(f"{source.name}: '{n}' is a file; a pack holds client folders")
            if len(rel.parts) == 1 and n.lower() not in folders:
                raise GfxError(f"{source.name}: '{n}' is not a folder of this client")
            if len(rel.parts) == 1:
                counts.setdefault(folders[n.lower()], 0)
        for n in names:
            rel = (Path(dirpath) / n).relative_to(source)
            key = rel.as_posix().lower()
            if key in seen:  # possible on a case-sensitive volume
                raise GfxError(f"{source.name}: '{rel}' is there twice in different case")
            seen.add(key)
            real = folders[rel.parts[0].lower()]
            counts[real] = counts.get(real, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[0].lower()))


def select(available: Sequence[str], requested: Optional[Sequence[str]], what: str) -> List[str]:
    if not requested:
        return list(available)
    by_lower = {name.lower(): name for name in available}
    chosen = []
    for name in requested:
        real = by_lower.get(name.lower())
        if real is None:
            raise GfxError(f"'{name}' is not {what}; choose from: {', '.join(available) or 'none'}")
        if real not in chosen:
            chosen.append(real)
    return chosen


# --- filesystem steps -----------------------------------------------------------

def clone_tree(source: Path, target: Path) -> None:
    """Copy a folder as an APFS clone (instant, shares blocks); plain copy elsewhere."""
    done = subprocess.run(["cp", "-cR", str(source), str(target)], capture_output=True, text=True)
    if done.returncode != 0:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=True)


def clone_files(sources: Sequence[Path], folder: Path) -> None:
    """Copy files into a folder as APFS clones; plain copies on another volume."""
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(sources), 256):  # well below the argument length limit
        chunk = sources[i:i + 256]
        done = subprocess.run(["cp", "-c", *map(str, chunk), str(folder)], capture_output=True, text=True)
        if done.returncode != 0:
            for source in chunk:
                shutil.copy2(source, folder / source.name)


def tree_stats(root: Path) -> Tuple[int, int]:
    files = size = 0
    for dirpath, _, names in os.walk(root):
        for n in names:
            files += 1
            size += os.lstat(os.path.join(dirpath, n)).st_size
    return files, size


def remove_tree(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)


class CaseResolver:
    """Map pack paths onto existing client paths without regard to case."""

    def __init__(self) -> None:
        self.listings: Dict[Path, Dict[str, str]] = {}

    def child(self, parent: Path, name: str) -> Path:
        listing = self.listings.get(parent)
        if listing is None:
            listing = {n.lower(): n for n in os.listdir(parent)} if parent.is_dir() else {}
            self.listings[parent] = listing
        real = listing.get(name.lower())
        if real is None:
            listing[name.lower()] = name
            return parent / name
        return parent / real

    def resolve(self, root: Path, segments: Sequence[str]) -> Path:
        path = root
        for s in segments:
            path = self.child(path, s)
        return path


def staged_files(staging: Path, folders: Dict[str, str],
                 selected: Sequence[str]) -> List[Tuple[Path, str, List[str]]]:
    """(staged file, client folder, path below it) for every file of a selected folder."""
    wanted = {name.lower() for name in selected}
    found = []
    for part in sorted(staging.iterdir()):
        for dirpath, dirnames, names in os.walk(part):
            for n in dirnames + names:
                p = Path(dirpath) / n
                if p.is_symlink() or not (p.is_dir() or p.is_file()):
                    raise GfxError(f"archive entry is not a plain file or folder: {p.relative_to(part)}")
            for n in names:
                segments = list((Path(dirpath) / n).relative_to(part).parts)
                if segments[0].lower() in wanted:
                    found.append((Path(dirpath) / n, folders[segments[0].lower()], segments[1:]))
    return found


# --- records --------------------------------------------------------------------
# Which variant is live is read from the folders (detect), never from a record,
# so a partly finished operation cannot leave the record and the folders apart.

def load_manifest(app: Path) -> dict:
    return read_json(state_dir(app) / MANIFEST) or {"components": {}}


def save_manifest(app: Path, manifest: dict) -> None:
    path = state_dir(app) / MANIFEST
    if manifest["components"]:
        write_json(path, manifest)
    elif path.exists():
        path.unlink()


def known_components(app: Path) -> List[str]:
    names = set(load_manifest(app)["components"])
    for p in app.iterdir():
        for suffix in VARIANT_SUFFIXES:
            if p.is_dir() and p.name.endswith(suffix):
                names.add(p.name[: -len(suffix)])
        if ROLLBACK in p.name:
            names.add(p.name.split(ROLLBACK)[0])
    return sorted(names, key=str.lower)


def begin(app: Path, journal: dict, is_running: Callable[[Path], bool]) -> Path:
    refuse_if_running(app, is_running)
    path = state_dir(app) / JOURNAL
    if path.exists():
        raise GfxError("an interrupted texture operation is pending; run 'textures recover' first")
    write_json(path, journal)
    return path


# --- operations -----------------------------------------------------------------

def install(app: Path, archives: Optional[Sequence[Path]] = None, components: Optional[Sequence[str]] = None,
            is_running: Callable[[Path], bool] = game_running, log=print,
            from_dir: Optional[Path] = None) -> List[str]:
    """Install a pack from its archives, or from `from_dir`, a folder laid out like
    one (what export writes). Either source is staged first; from there on both
    take the same path."""
    if bool(archives) == (from_dir is not None):
        raise GfxError("give the pack's archives or a pack folder, not both" if archives
                       else "no archive or pack folder given")
    with operation_lock(app):
        if from_dir is None:
            inventory = inspect(app, archives)
            selected = select(list(inventory), components, "a folder in these archives")
        else:
            from_dir = from_dir.expanduser().resolve()
            inventory = inspect_dir(app, from_dir)
            selected = select(list(inventory), components, f"a folder in {from_dir.name}")
        for name in selected:
            state = detect(app, name)
            if state != NOT_INSTALLED:
                raise GfxError(f"{name}: {state}; uninstall it before installing another pack over it")
        # Fail fast, before unpacking or copying gigabytes; begin() checks again
        # once the pack is staged.
        refuse_if_running(app, is_running)
        if (state_dir(app) / JOURNAL).exists():
            raise GfxError("an interrupted texture operation is pending; run 'textures recover' first")

        staging = state_dir(app) / STAGING
        remove_tree(staging)
        staging.mkdir()
        try:
            # Stage outside the client first: a damaged archive or a full disk
            # fails here, before any client folder changes.
            if from_dir is None:
                for i, archive in enumerate(archives):
                    part = staging / str(i)
                    part.mkdir()
                    log(f"unpacking {archive.name}")
                    done = subprocess.run([TAR, "-xf", str(archive), "-C", str(part)],
                                          capture_output=True, text=True)
                    if done.returncode != 0:
                        raise GfxError(f"cannot unpack {archive.name}: {done.stderr.strip()}")
                source = {"archives": [{"name": a.name, "bytes": a.stat().st_size} for a in archives]}
            else:
                # A clone: the pack files then move out of staging, never out of from_dir.
                log(f"copying {from_dir.name}")
                clone_tree(from_dir, staging / "0")
                source = {"from_dir": from_dir.name}
            files = staged_files(staging, client_folders(app), selected)

            journal = {"action": "install", "phase": "backing-up", "components": selected}
            journal_path = begin(app, journal, is_running)
            try:
                for name in selected:
                    log(f"backing up {name} -> {name}{BACKUP}")
                    clone_tree(app / name, app / (name + BACKUP))
                    if tree_stats(app / name) != tree_stats(app / (name + BACKUP)):
                        raise GfxError(f"backup of {name} does not match the original")
                journal["phase"] = "merging"
                write_json(journal_path, journal)
                log(f"installing {len(files)} files")
                resolver = CaseResolver()
                for staged, folder, below in files:
                    target = resolver.resolve(app / folder, below)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged, target)
                manifest = load_manifest(app)
                installed = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for name in selected:
                    manifest["components"][name] = {"installed": installed, **source}
                save_manifest(app, manifest)
            except BaseException:
                undo_install(app, journal)
                journal_path.unlink()
                raise
            journal_path.unlink()
        finally:
            remove_tree(staging)
        return selected


def export(app: Path, dest: Path, components: Optional[Sequence[str]] = None,
           is_running: Callable[[Path], bool] = game_running, log=print) -> List[str]:
    """Copy the installed pack's files out of the client to `dest/<folder>/<path below
    it>`, the layout of a pack archive, for install(from_dir=dest). Only files the
    pack added or changed are copied: a pack file identical to the original cannot be
    told apart from it. Every selected folder gets its `dest/<folder>`, empty if no file
    differs, so installing from `dest` brings back every component. Writes nothing
    outside `dest`; returns the folders exported."""
    with operation_lock(app):
        if pending(app):
            raise GfxError("an interrupted texture operation is pending; run 'textures recover' first")
        names = select(known_components(app), components, "an installed folder")
        if not names:
            raise GfxError("no texture pack is installed")
        states = {n: detect(app, n) for n in names}
        for name, state in states.items():
            if state not in (PACK_ACTIVE, ORIGINAL_ACTIVE):
                raise GfxError(f"{name}: {state}; cannot export it")
        refuse_if_running(app, is_running)
        dest = dest.expanduser().resolve()
        # By identity, not by name: on a case-insensitive volume .../App/x is inside .../app.
        if any(p.exists() and os.path.samefile(p, app) for p in (dest, *dest.parents)):
            raise GfxError(f"{dest} is inside the client folder; export to a folder outside it")
        if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
            raise GfxError(f"{dest} is not an empty folder; export to a new or empty one")
        dest.mkdir(parents=True, exist_ok=True)
        try:
            for name in names:
                if states[name] == PACK_ACTIVE:
                    pack, originals = app / name, app / (name + BACKUP)
                else:
                    pack, originals = app / (name + PACK), app / name
                (dest / name).mkdir()
                count = export_folder(pack, originals, dest / name)
                log(f"{name}: {count} pack files" if count
                    else f"{name}: no file differs from the originals; exported as an empty folder")
        except BaseException:
            for name in names:
                remove_tree(dest / name)
            raise
        return names


def export_folder(pack: Path, originals: Path, target: Path) -> int:
    """Copy each file of `pack` that `originals` lacks or holds other bytes for
    (paths compare without regard to case) to the same path below `target`."""
    resolver = CaseResolver()
    count = 0
    for dirpath, dirnames, names in os.walk(pack):
        here = Path(dirpath)
        dirnames.sort()
        for n in dirnames + names:
            p = here / n
            if p.is_symlink() or not (p.is_dir() or p.is_file()):
                raise GfxError(f"{p.relative_to(pack.parent)} is not a plain file or folder; cannot export it")
        below = here.relative_to(pack).parts
        changed = []
        for n in sorted(names):
            original = resolver.resolve(originals, below + (n,))
            if not (original.is_file() and filecmp.cmp(here / n, original, shallow=False)):
                changed.append(here / n)
        if changed:
            clone_files(changed, target.joinpath(*below))
            count += len(changed)
    return count


def undo_install(app: Path, journal: dict) -> None:
    for name in journal["components"]:
        backup = app / (name + BACKUP)
        if journal["phase"] == "backing-up":
            remove_tree(backup)  # the live folder was not touched yet
        elif backup.is_dir():
            if (app / name).exists():
                (app / name).rename(app / f"{name}{ROLLBACK}{uuid.uuid4().hex}")
            backup.rename(app / name)
        for leftover in app.glob(name + ROLLBACK + "*"):
            remove_tree(leftover)


def use(app: Path, variant: str, components: Optional[Sequence[str]] = None,
        is_running: Callable[[Path], bool] = game_running, log=print) -> List[str]:
    if variant not in ("pack", "original"):
        raise GfxError("choose 'pack' or 'original'")
    with operation_lock(app):
        names = select(known_components(app), components, "an installed folder")
        if not names:
            raise GfxError("no texture pack is installed")
        states = {n: detect(app, n) for n in names}
        for name, state in states.items():
            if state not in (PACK_ACTIVE, ORIGINAL_ACTIVE):
                raise GfxError(f"{name}: {state}; cannot switch it")
        todo = [n for n in names
                if states[n] == (ORIGINAL_ACTIVE if variant == "pack" else PACK_ACTIVE)]
        if not todo:
            log(f"already using the {variant} files")
            return []
        journal = {"action": "use", "components": todo, "starting": {n: states[n] for n in todo}}
        journal_path = begin(app, journal, is_running)
        try:
            for name in todo:
                log(f"{name}: switching to {variant} files")
                active, swap = app / name, app / (name + SWAP)
                came_from, goes_to = variant_paths(app, name, states[name])
                active.rename(swap)
                came_from.rename(active)
                swap.rename(goes_to)
        except BaseException:
            undo_use(app, journal)
            journal_path.unlink()
            raise
        journal_path.unlink()
        return todo


def variant_paths(app: Path, name: str, starting: str) -> Tuple[Path, Path]:
    """(inactive folder that becomes live, name the live folder moves to)."""
    if starting == PACK_ACTIVE:
        return app / (name + BACKUP), app / (name + PACK)
    return app / (name + PACK), app / (name + BACKUP)


def undo_use(app: Path, journal: dict) -> None:
    """Settle each folder caught between renames; finished switches stay switched."""
    for name in journal["components"]:
        active, swap = app / name, app / (name + SWAP)
        _, goes_to = variant_paths(app, name, journal["starting"][name])
        if not swap.exists():
            continue  # this folder was not touched, or its switch completed
        if active.exists():
            swap.rename(goes_to)  # only the last rename was missing: finish it
        else:
            swap.rename(active)  # back to the starting layout


def uninstall(app: Path, components: Optional[Sequence[str]] = None,
              is_running: Callable[[Path], bool] = game_running, log=print) -> List[str]:
    with operation_lock(app):
        names = select(known_components(app), components, "an installed folder")
        if not names:
            raise GfxError("no texture pack is installed")
        for name in names:
            state = detect(app, name)
            if state not in (PACK_ACTIVE, ORIGINAL_ACTIVE):
                raise GfxError(f"{name}: {state}; cannot uninstall it")
        journal = {"action": "uninstall", "phase": "restoring", "components": names}
        journal_path = begin(app, journal, is_running)
        finish_uninstall(app, journal, journal_path, log)
        return names


def finish_uninstall(app: Path, journal: dict, journal_path: Path, log) -> None:
    """Runs forward only: every step can be repeated after an interruption."""
    if journal["phase"] == "restoring":
        for name in journal["components"]:
            restore_originals(app, name)
            log(f"{name}: originals restored")
        journal["phase"] = "removing"
        write_json(journal_path, journal)
    for name in journal["components"]:
        remove_tree(app / (name + PACK))
    manifest = load_manifest(app)
    for name in journal["components"]:
        manifest["components"].pop(name, None)
    save_manifest(app, manifest)
    journal_path.unlink()


def restore_originals(app: Path, name: str) -> None:
    active, backup, pack = app / name, app / (name + BACKUP), app / (name + PACK)
    if active.exists() and backup.exists() and not pack.exists():
        active.rename(pack)
        backup.rename(active)
    elif not active.exists() and backup.exists():
        backup.rename(active)
    elif not (active.exists() and not backup.exists()):
        raise GfxError(f"cannot tell which {name} folder holds the originals; resolve it by hand")


def recover(app: Path, log=print) -> Optional[str]:
    with operation_lock(app):
        journal_path = state_dir(app) / JOURNAL
        journal = read_json(journal_path)
        if journal is None:
            return None
        action = journal["action"]
        if action == "install":
            undo_install(app, journal)
            journal_path.unlink()
        elif action == "use":
            undo_use(app, journal)
            journal_path.unlink()
        elif action == "uninstall":
            finish_uninstall(app, journal, journal_path, log)
        else:
            raise GfxError(f"unknown operation in {journal_path}: {action}")
        remove_tree(state_dir(app) / STAGING)
        return action


def status(app: Path) -> List[Tuple[str, str]]:
    return [(name, detect(app, name)) for name in known_components(app)]


def pending(app: Path) -> Optional[str]:
    journal = read_json(state_dir(app) / JOURNAL)
    return journal["action"] if journal else None
