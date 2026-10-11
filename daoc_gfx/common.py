"""Shared pieces: the error type, the per-client state folder, JSON state files,
the client-running guard and the operation lock."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

# Everything daoc-gfx records about one client lives in this folder inside the
# client's app directory, so the record moves and is deleted with the client.
STATE_DIR = ".daoc-gfx"


class GfxError(Exception):
    """A refusal or failure reported to the user as one line, exit status 1."""


def state_dir(app: Path) -> Path:
    path = app / STATE_DIR
    path.mkdir(exist_ok=True)
    return path


def check_app(app: Path) -> Path:
    app = app.resolve()
    if not (app / "game.dll").is_file():
        raise GfxError(f"{app} is not a DAoC client app folder (no game.dll)")
    return app


def read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    """Write via a temporary file and rename, so a crash leaves the old or the new file."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def game_running(app: Path) -> bool:
    """True when a game.dll process (or its launcher) uses this client folder.

    The client maps game.dll from the app folder and the launcher runs with the
    app folder as its working directory, so either shows in lsof. A client of
    another install does not count.
    """
    found = subprocess.run(["pgrep", "-f", r"game\.dll"], capture_output=True, text=True)
    root = str(app.resolve())
    for pid in found.stdout.split():
        files = subprocess.run(["lsof", "-n", "-P", "-p", pid, "-Fn"], capture_output=True,
                               text=True, errors="surrogateescape")
        for line in files.stdout.splitlines():
            if line.startswith("n") and (line[1:] == root or line[1:].startswith(root + "/")):
                return True
    return False


def refuse_if_running(app: Path, is_running) -> None:
    if is_running(app):
        raise GfxError("the client is running from this folder; quit it (/quit) first")


@contextmanager
def operation_lock(app: Path) -> Iterator[None]:
    """One changing daoc-gfx operation per client at a time."""
    with open(state_dir(app) / "lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GfxError("another daoc-gfx operation is running on this client") from None
        yield
