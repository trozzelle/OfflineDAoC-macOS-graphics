"""daoc-gfx command line."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import anisotropy, display, patches, reshade, textures
from .common import GfxError, check_app


def registry_factory(args):
    """The prefix registry, opened only when a patch needs it (Retina mode, reshade)."""
    def get() -> display.WineRegistry:
        if not args.wine or not args.prefix:
            raise GfxError("Retina mode and reshade need --wine <wine folder> and --prefix <wine prefix>")
        return display.WineRegistry(Path(args.wine), Path(args.prefix))
    return get


def show_status(app: Path, launcher: Optional[Path], prefix: Optional[Path]) -> None:
    cat = patches.catalog()
    for p, state in patches.states(app, launcher, prefix).items():
        detail = ""
        if cat[p]["kind"] == "anisotropy" and state != "off":
            detail = f", {anisotropy.level(app)}x"
        elif cat[p]["kind"] == "reshade" and state != "off":
            detail = f", ReShade {reshade.SOURCES['reshade']['version']}"
        print(f"{p:<10} {state:<14} {cat[p]['title']}{detail}")
    if display.conf_owner(app) == display.YOUR_CONF:
        print("mtld3d.conf is your own file; mtld3d patches stay off while it is there")


# Schema 1's values, fixed here instead of passing on the strings the modules print,
# so rewording a message cannot change the JSON. A state missing here raises KeyError:
# give it a value, and bump `schema` if old readers would misread it.
PATCH_STATE_JSON = {
    patches.ON: "on", patches.OFF: "off", patches.PARTLY_ON: "partly on",
    anisotropy.ON: "on", anisotropy.OFF: "off", anisotropy.PARTLY_ON: "partly on",
    patches.NOT_INSTALLED: "not installed", patches.NEEDS_RECOVER: "needs 'textures recover'",
}
COMPONENT_STATE_JSON = {
    textures.NOT_INSTALLED: "not installed", textures.PACK_ACTIVE: "pack active",
    textures.ORIGINAL_ACTIVE: "originals active", textures.MISSING: "missing", textures.CONFLICT: "conflict",
}
CONF_OWNER_JSON = {display.NO_CONF: "none", display.OUR_CONF: "daoc-gfx", display.YOUR_CONF: "your own"}


def status_json(app: Path, launcher: Optional[Path], prefix: Optional[Path]) -> dict:
    """The machine-readable status (schema 1; a breaking change bumps `schema`)."""
    cat = patches.catalog()
    states = patches.states(app, launcher, prefix)
    aniso_on = any(cat[p]["kind"] == "anisotropy" and state != anisotropy.OFF for p, state in states.items())
    return {
        "schema": 1,
        "patches": {p: PATCH_STATE_JSON[state] for p, state in states.items()},
        "anisotropy_level": anisotropy.level(app) if aniso_on else None,
        "mtld3d_conf": CONF_OWNER_JSON[display.conf_owner(app)],
        "textures": {
            "components": [{"name": name, "state": COMPONENT_STATE_JSON[state]}
                           for name, state in textures.status(app)],
            "pending": textures.pending(app),
        },
    }


def show_textures(app: Path) -> None:
    rows = textures.status(app)
    if not rows:
        print("texture packs  : none")
    for name, state in rows:
        print(f"texture pack   : {name:<16} {state}")
    pending = textures.pending(app)
    if pending:
        print(f"PENDING        : an interrupted '{pending}' operation; run 'textures recover'")


def run(args) -> None:
    app = check_app(Path(args.app))
    get_registry = registry_factory(args)
    launcher = Path(args.launcher) if args.launcher else None
    prefix = Path(args.prefix) if args.prefix else None
    if args.command == "status":
        if args.json:
            print(json.dumps(status_json(app, launcher, prefix), indent=2))
        else:
            show_status(app, launcher, prefix)
    elif args.command == "list":
        for p, spec in patches.catalog().items():
            print(f"{p}\n    {spec['title']}. {spec['description']}")
    elif args.command in ("enable", "disable"):
        ids = set(args.patch)
        archives = [Path(a).expanduser() for a in (getattr(args, "archives", None) or [])]
        from_dir = getattr(args, "from_dir", None)
        patches.change(app, ids if args.command == "enable" else set(),
                       ids if args.command == "disable" else set(), get_registry, archives,
                       launcher=launcher, level=getattr(args, "level", None),
                       from_dir=Path(from_dir).expanduser() if from_dir else None)
        show_status(app, launcher, prefix)
        print("Changes take effect at the next client launch.")
    elif args.command == "select":
        if not sys.stdin.isatty():
            raise GfxError("select needs a terminal; use enable/disable <patch>...")
        patches.select(app, get_registry, launcher=launcher, prefix=prefix)
    elif args.command == "textures":
        archives = [Path(a).expanduser() for a in getattr(args, "archives", [])]
        if args.action == "inspect":
            for name, count in textures.inspect(app, archives).items():
                print(f"{name:<16} {count:>7} files   now: {textures.detect(app, name)}")
        elif args.action == "install":
            from_dir = Path(args.from_dir).expanduser() if args.from_dir else None
            done = textures.install(app, archives, args.component, from_dir=from_dir)
            print(f"installed: {', '.join(done)} (pack files active)")
        elif args.action == "export":
            done = textures.export(app, Path(args.dir).expanduser(), args.component)
            print(f"exported: {', '.join(done)}")
        elif args.action == "use":
            done = textures.use(app, args.variant, args.component)
            if done:
                print(f"now using the {args.variant} files for: {', '.join(done)}")
        elif args.action == "uninstall":
            done = textures.uninstall(app, args.component)
            print(f"uninstalled: {', '.join(done)} (originals restored)")
        elif args.action == "recover":
            action = textures.recover(app)
            print(f"recovered an interrupted '{action}'" if action else "nothing to recover")
        elif args.action == "status":
            show_textures(app)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="daoc-gfx", description="Graphical patches for the DAoC 1.127 client "
                                "under Wine on macOS (OfflineDAoC-macOS).")
    p.add_argument("--app", required=True, help="client app folder (the one holding game.dll)")
    p.add_argument("--prefix", help="wine prefix the client runs in (Retina mode, reshade)")
    p.add_argument("--wine", help="wine folder with bin/wine (Retina mode, reshade)")
    p.add_argument("--launcher", help="OfflineDAoC-macOS launcher .app (anisotropy, reshade; default: "
                                      "DAoCLauncher.app beside the client's OfflineDAoC folder)")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="each patch and whether it is on").add_argument(
        "--json", action="store_true", help="the whole state as one JSON object (schema 1)")
    commands.add_parser("list", help="the patches and what each does")
    commands.add_parser("select", help="choose the patches to turn on from a checklist")
    on = commands.add_parser("enable", help="turn patches on")
    on.add_argument("patch", nargs="+")
    on.add_argument("--archives", nargs="+", metavar="ARCHIVE",
                    help="the texture pack's archives, every part (only to install it)")
    on.add_argument("--from-dir", metavar="DIR",
                    help="a folder 'textures export' wrote (only to install the texture pack)")
    on.add_argument("--level", type=int, choices=anisotropy.LEVELS,
                    help="anisotropic filtering level (anisotropy only; default: the current one, else 16)")
    commands.add_parser("disable", help="turn patches off").add_argument("patch", nargs="+")

    t = commands.add_parser("textures", help="texture-pack folders in detail")
    ta = t.add_subparsers(dest="action", required=True)
    ins = ta.add_parser("inspect", help="list the client folders an archive set replaces")
    ins.add_argument("archives", nargs="+")
    inst = ta.add_parser("install", help="back up the folders and install the pack files")
    inst.add_argument("archives", nargs="*", help="every part of the pack (e.g. both Sqeak's .7z parts)")
    inst.add_argument("--from-dir", metavar="DIR", help="instead of archives: a folder 'textures export' wrote")
    exp = ta.add_parser("export", help="copy the installed pack files out of the client into a folder")
    exp.add_argument("dir", help="a new or empty folder outside the client")
    use = ta.add_parser("use", help="switch installed folders between pack and original files")
    use.add_argument("variant", choices=["pack", "original"])
    uni = ta.add_parser("uninstall", help="restore the originals and delete the pack files")
    for sub in (inst, exp, use, uni):
        sub.add_argument("-c", "--component", action="append",
                         help="only this client folder (repeatable; default: all)")
    ta.add_parser("recover", help="finish or undo an interrupted operation")
    ta.add_parser("status", help="installed folders and which files are live")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    try:
        run(args)
    except GfxError as e:
        print(f"daoc-gfx: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ndaoc-gfx: interrupted", file=sys.stderr)
        return 130
    return 0
