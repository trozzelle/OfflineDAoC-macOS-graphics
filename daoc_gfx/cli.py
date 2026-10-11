"""daoc-gfx command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import display, textures
from .common import GfxError, check_app


def registry(args) -> display.WineRegistry:
    if not args.wine or not args.prefix:
        raise GfxError("display commands need --wine <wine folder> and --prefix <wine prefix>")
    return display.WineRegistry(Path(args.wine), Path(args.prefix))


def show_textures(app: Path) -> None:
    rows = textures.status(app)
    if not rows:
        print("texture packs  : none")
    for name, state in rows:
        print(f"texture pack   : {name:<16} {state}")
    pending = textures.pending(app)
    if pending:
        print(f"PENDING        : an interrupted '{pending}' operation; run 'textures recover'")


def show_status(app: Path) -> None:
    shown = display.status(app)
    print(f"display preset : {shown['preset'] or 'none'}   (mtld3d.conf: {shown['conf']})")
    show_textures(app)


def run(args) -> None:
    app = check_app(Path(args.app))
    if args.area == "status":
        show_status(app)
    elif args.area == "display":
        if args.action == "list":
            for name, preset in display.presets().items():
                print(f"{name}\n    {preset['description']}")
        elif args.action == "apply":
            display.apply(app, registry(args), args.preset)
            print(f"display preset '{args.preset}' applied; it takes effect at the next client launch")
        elif args.action == "revert":
            name = display.revert(app, registry(args))
            print(f"display preset '{name}' reverted" if name else "no display preset applied")
        elif args.action == "status":
            shown = display.status(app)
            print(f"preset      : {shown['preset'] or 'none'}")
            print(f"mtld3d.conf : {shown['conf']}")
            if args.wine and args.prefix:
                print(f"RetinaMode  : {registry(args).retina() or 'not set'}")
    elif args.area == "textures":
        archives = [Path(a).expanduser() for a in getattr(args, "archives", [])]
        if args.action == "inspect":
            inventory = textures.inspect(app, archives)
            for name, count in inventory.items():
                print(f"{name:<16} {count:>7} files   now: {textures.detect(app, name)}")
        elif args.action == "install":
            done = textures.install(app, archives, args.component)
            print(f"installed: {', '.join(done)} (pack files active)")
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
    p.add_argument("--prefix", help="wine prefix the client runs in (display commands)")
    p.add_argument("--wine", help="wine folder with bin/wine (display commands)")
    areas = p.add_subparsers(dest="area", required=True)
    areas.add_parser("status", help="what is applied to this client")

    d = areas.add_parser("display", help="display presets (Retina mode, mtld3d.conf)")
    da = d.add_subparsers(dest="action", required=True)
    da.add_parser("list", help="the presets and what each does")
    da.add_parser("apply", help="apply a preset").add_argument("preset")
    da.add_parser("revert", help="restore the settings from before the first preset")
    da.add_parser("status", help="the applied preset and the current Retina mode")

    t = areas.add_parser("textures", help="texture packs from archives you downloaded")
    ta = t.add_subparsers(dest="action", required=True)
    ins = ta.add_parser("inspect", help="list the client folders an archive set replaces")
    ins.add_argument("archives", nargs="+")
    inst = ta.add_parser("install", help="back up the folders and install the pack files")
    inst.add_argument("archives", nargs="+", help="every part of the pack (e.g. both Sqeak's .7z parts)")
    use = ta.add_parser("use", help="switch installed folders between pack and original files")
    use.add_argument("variant", choices=["pack", "original"])
    uni = ta.add_parser("uninstall", help="restore the originals and delete the pack files")
    for sub in (inst, use, uni):
        sub.add_argument("-c", "--component", action="append",
                         help="only this client folder (repeatable; default: all)")
    ta.add_parser("recover", help="finish or undo an interrupted operation")
    ta.add_parser("status", help="installed folders and which files are live")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        run(args)
    except GfxError as e:
        print(f"daoc-gfx: {e}", file=sys.stderr)
        return 1
    return 0
