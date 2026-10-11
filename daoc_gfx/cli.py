"""daoc-gfx command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import anisotropy, display, patches, textures
from .common import GfxError, check_app


def registry_factory(args):
    """The prefix registry, opened only when a patch needs it (Retina mode)."""
    def get() -> display.WineRegistry:
        if not args.wine or not args.prefix:
            raise GfxError("Retina mode needs --wine <wine folder> and --prefix <wine prefix>")
        return display.WineRegistry(Path(args.wine), Path(args.prefix))
    return get


def show_status(app: Path, launcher: Optional[Path]) -> None:
    cat = patches.catalog()
    for p, state in patches.states(app, launcher).items():
        detail = f", {anisotropy.level(app)}x" if cat[p]["kind"] == "anisotropy" and state != "off" else ""
        print(f"{p:<10} {state:<14} {cat[p]['title']}{detail}")
    if display.conf_owner(app) == "your own":
        print("mtld3d.conf is your own file; mtld3d patches stay off while it is there")


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
    if args.command == "status":
        show_status(app, launcher)
    elif args.command == "list":
        for p, spec in patches.catalog().items():
            print(f"{p}\n    {spec['title']}. {spec['description']}")
    elif args.command in ("enable", "disable"):
        ids = set(args.patch)
        archives = [Path(a).expanduser() for a in (getattr(args, "archives", None) or [])]
        patches.change(app, ids if args.command == "enable" else set(),
                       ids if args.command == "disable" else set(), get_registry, archives,
                       launcher=launcher, level=getattr(args, "level", None))
        show_status(app, launcher)
        print("Changes take effect at the next client launch.")
    elif args.command == "select":
        if not sys.stdin.isatty():
            raise GfxError("select needs a terminal; use enable/disable <patch>...")
        patches.select(app, get_registry, launcher=launcher)
    elif args.command == "textures":
        archives = [Path(a).expanduser() for a in getattr(args, "archives", [])]
        if args.action == "inspect":
            for name, count in textures.inspect(app, archives).items():
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
    p.add_argument("--prefix", help="wine prefix the client runs in (Retina mode)")
    p.add_argument("--wine", help="wine folder with bin/wine (Retina mode)")
    p.add_argument("--launcher", help="OfflineDAoC-macOS launcher .app (anisotropy; default: "
                                      "DAoCLauncher.app beside the client's OfflineDAoC folder)")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="each patch and whether it is on")
    commands.add_parser("list", help="the patches and what each does")
    commands.add_parser("select", help="choose the patches to turn on from a checklist")
    on = commands.add_parser("enable", help="turn patches on")
    on.add_argument("patch", nargs="+")
    on.add_argument("--archives", nargs="+", metavar="ARCHIVE",
                    help="the texture pack's archives, every part (only to install it)")
    on.add_argument("--level", type=int, choices=anisotropy.LEVELS,
                    help="anisotropic filtering level (anisotropy only; default: the current one, else 16)")
    commands.add_parser("disable", help="turn patches off").add_argument("patch", nargs="+")

    t = commands.add_parser("textures", help="texture-pack folders in detail")
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
    except KeyboardInterrupt:
        print("\ndaoc-gfx: interrupted", file=sys.stderr)
        return 130
    return 0
