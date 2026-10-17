"""Client options in user.dat: set one `[section] key=value` and put it back.

The client keeps its options in each Wine prefix at
drive_c/users/<user>/AppData/Roaming/Electronic Arts/Dark Age of Camelot/
<settings folder>/user.dat. The settings folder is the `settings=` value of the
app folder's paths.dat cut to 13 characters (OfflineDAoC035 -> OfflineDAoC03,
the folder the client creates); when paths.dat has none, every settings folder
under the prefix is used. Every user of every prefix given counts. A missing
user.dat is never created: `set` refuses when there is none at all.

`set` records the value from before (or that the key or section was absent) in
.daoc-gfx/userdat.json, once: setting the key again keeps the first original.
`restore` puts back only that key's value, or removes the key it added (and the
section, when it added that and nothing else is in it now). Every other line,
including options changed in game since, stays as it is; with nothing else
changed, the file ends byte-identical. Section and key names match without
regard to case; the file's line endings (CRLF from the client) are kept.
`holds` tells whether a value is in place, in a prefix's files or in those
`set` changed.

The client rewrites user.dat when it exits, so `set` and `restore` refuse while
it runs. The caller holds the client's operation lock.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .common import GfxError, game_running, read_json, refuse_if_running, state_dir, write_json

STATE = "userdat.json"
CAMELOT = Path("AppData") / "Roaming" / "Electronic Arts" / "Dark Age of Camelot"
# paths.dat's `settings=` cut to this many characters names the settings folder.
SETTINGS_NAME_CHARS = 13
_LINE = re.compile(rb"[^\n]*\n|[^\n]+$")


def settings_folder(app: Path) -> Optional[str]:
    try:
        text = (app / "paths.dat").read_text(encoding="latin-1")
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip().lower() == "settings" and value.strip():
            return value.strip()[:SETTINGS_NAME_CHARS]
    return None


def user_dats(app: Path, prefixes: Iterable[Path]) -> List[Path]:
    """The client's existing user.dat files in `prefixes` (see the module docstring)."""
    folder = settings_folder(app)
    found: List[Path] = []
    for prefix in prefixes:
        users = prefix / "drive_c" / "users"
        for user in sorted(users.iterdir()) if users.is_dir() else []:
            camelot = user / CAMELOT
            candidates = [camelot / folder / "user.dat"] if folder else sorted(camelot.glob("*/user.dat"))
            found += [p.resolve() for p in candidates if p.is_file()]
    return list(dict.fromkeys(found))  # a prefix given twice


def _entry_name(section: str, key: str) -> str:
    return f"[{section.lower()}]{key.lower()}"


def _eol(data: bytes) -> bytes:
    return b"\n" if b"\n" in data and b"\r\n" not in data else b"\r\n"


def _content(line: bytes) -> bytes:
    return line.rstrip(b"\r\n")


def _header(line: bytes) -> Optional[bytes]:
    text = _content(line).strip()
    return text[1:-1].strip().lower() if text.startswith(b"[") and text.endswith(b"]") else None


def _find(lines: List[bytes], section: bytes, key: bytes) -> Tuple[Optional[int], Optional[int], int]:
    """(header index, key line index, index after the section's last non-blank line)
    for the first section named `section`; names lowercased."""
    start = next((i for i, line in enumerate(lines) if _header(line) == section), None)
    if start is None:
        return None, None, len(lines)
    found, end = None, start + 1
    for i in range(start + 1, len(lines)):
        if _header(lines[i]) is not None:
            break
        content = _content(lines[i])
        if content.strip():
            end = i + 1
        name, eq, _ = content.partition(b"=")
        if found is None and eq and name.strip().lower() == key:
            found = i
    return start, found, end


def _with_raw(line: bytes, raw: bytes) -> bytes:
    """`line` with everything after its first '=' replaced by `raw`."""
    content = _content(line)
    return content.partition(b"=")[0] + b"=" + raw + line[len(content):]


def _with_value(line: bytes, value: bytes) -> bytes:
    """`line` set to `value`, keeping the spaces right after its '='."""
    raw = _content(line).partition(b"=")[2]
    return _with_raw(line, raw[:len(raw) - len(raw.lstrip(b" \t"))] + value)


def set_value(data: bytes, section: str, key: str, value: str) -> Tuple[bytes, dict]:
    """`data` with the key set, and the record entry that undoes it."""
    lines = _LINE.findall(data)
    eol = _eol(data)
    start, at, end = _find(lines, section.lower().encode("latin-1"), key.lower().encode("latin-1"))
    new = value.encode("latin-1")
    entry = {"original": None, "section_added": False, "eol_added": False}
    if at is not None:
        entry["original"] = _content(lines[at]).partition(b"=")[2].decode("latin-1")
        lines[at] = _with_value(lines[at], new)
        return b"".join(lines), entry
    added = [key.encode("latin-1") + b"=" + new + eol]
    if start is None:
        added.insert(0, b"[" + section.encode("latin-1") + b"]" + eol)
        entry["section_added"] = True
    if end > 0 and not lines[end - 1].endswith(b"\n"):  # only the file's last line
        lines[end - 1] += eol
        entry["eol_added"] = True
    lines[end:end] = added
    return b"".join(lines), entry


def restore_value(data: bytes, section: str, key: str, entry: dict) -> bytes:
    """`data` with the key's recorded original back, or what `set_value` added removed."""
    lines = _LINE.findall(data)
    start, at, _ = _find(lines, section.lower().encode("latin-1"), key.lower().encode("latin-1"))
    if at is None:  # the client dropped it since
        return data
    if entry["original"] is not None:
        lines[at] = _with_raw(lines[at], entry["original"].encode("latin-1"))
        return b"".join(lines)
    drop = [at]
    if entry["section_added"] and start is not None:
        rest = [i for i in range(start + 1, len(lines)) if i != at]
        nxt = next((i for i in rest if _header(lines[i]) is not None), len(lines))
        if not any(_content(lines[i]).strip() for i in rest if i < nxt):
            drop.append(start)
    at_end = len(lines) - 1 in drop
    lines = [line for i, line in enumerate(lines) if i not in drop]
    if entry["eol_added"] and at_end and lines:
        lines[-1] = _content(lines[-1])
    return b"".join(lines)


def get_value(data: bytes, section: str, key: str) -> Optional[str]:
    """The key's value in `data` without the spaces around it, or None when it is absent."""
    lines = _LINE.findall(data)
    _, at, _ = _find(lines, section.lower().encode("latin-1"), key.lower().encode("latin-1"))
    return None if at is None else _content(lines[at]).partition(b"=")[2].strip().decode("latin-1")


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".daoc-gfx.tmp")
    tmp.write_bytes(data)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


def _under(path: str, prefixes: Iterable[Path]) -> bool:
    return any(path.startswith(str(p.resolve()) + os.sep) for p in prefixes)


def existing_user_dats(app: Path, prefixes: Iterable[Path]) -> List[Path]:
    """`user_dats`, refused when there is none (so a caller can check before changing anything)."""
    prefixes = list(prefixes)
    files = user_dats(app, prefixes)
    if not files:
        raise GfxError(f"no user.dat of this client in {', '.join(str(p) for p in prefixes)}; "
                       "start the client once so it writes one")
    return files


def set(app: Path, prefixes: Iterable[Path], section: str, key: str, value: str,
        is_running=game_running, log=print) -> List[Path]:
    """Set `[section] key=value` in the client's user.dat files of `prefixes`; the paths changed."""
    refuse_if_running(app, is_running)
    files = existing_user_dats(app, prefixes)
    record_path = state_dir(app) / STATE
    record: Dict[str, Dict[str, dict]] = (read_json(record_path) or {}).get("files", {})
    name = _entry_name(section, key)
    for path in files:
        data = path.read_bytes()
        changed, entry = set_value(data, section, key, value)
        if name not in record.get(str(path), {}):
            record.setdefault(str(path), {})[name] = entry
            write_json(record_path, {"files": record})  # recorded before the change
        if changed != data:
            _write(path, changed)
        log(f"user.dat: [{section}] {key}={value} in {path}")
    return files


def restore(app: Path, prefixes: Iterable[Path], section: str, key: str,
            is_running=game_running, log=print) -> None:
    """Put `[section] key` back in the user.dat files of `prefixes` it was set in;
    files in other prefixes keep their record for a later restore."""
    prefixes = list(prefixes)
    refuse_if_running(app, is_running)
    record_path = state_dir(app) / STATE
    record: Dict[str, Dict[str, dict]] = (read_json(record_path) or {}).get("files", {})
    name = _entry_name(section, key)
    for path_name, entries in list(record.items()):
        if name not in entries or not _under(path_name, prefixes):
            continue
        path = Path(path_name)
        if path.is_file():
            data = path.read_bytes()
            restored = restore_value(data, section, key, entries[name])
            if restored != data:
                _write(path, restored)
            log(f"user.dat: [{section}] {key} restored in {path}")
        else:
            log(f"user.dat: {path} is gone; nothing to restore there")
        del entries[name]
        if not entries:
            del record[path_name]
    if record:
        write_json(record_path, {"files": record})
    elif record_path.exists():
        record_path.unlink()


def holds(app: Path, prefixes: Optional[Iterable[Path]], section: str, key: str, value: str) -> bool:
    """Whether every user.dat of `prefixes` has `[section] key=value`; for None, every
    file `set` changed it in. False when there is no such file."""
    if prefixes is None:
        record: Dict[str, Dict[str, dict]] = (read_json(state_dir(app) / STATE) or {}).get("files", {})
        files = [Path(p) for p, entries in record.items() if _entry_name(section, key) in entries]
    else:
        files = user_dats(app, prefixes)
    return bool(files) and all(p.is_file() and get_value(p.read_bytes(), section, key) == value for p in files)
