# OfflineDAoC-macOS-graphics

Optional graphical patches for the Dark Age of Camelot 1.127 client running
under Wine on macOS, as installed by
[OfflineDAoC-macOS](https://github.com/trozzelle/OfflineDAoC-macOS). That repo's
`graphics.sh` pulls a pinned commit of this one and runs it against its install;
`daoc-gfx` also works on its own against any such client.

DAoC is a property of Mythic Entertainment / Broadsword Online Games (Electronic Arts).
This is unaffiliated fan tooling. It ships no game files and no third-party art:
texture packs are archives you download yourself.

## Patches

Each patch is turned on and off on its own, from a checklist (`select`) or by
name (`enable` / `disable`). The catalog is `daoc_gfx/patches.json`.

| Patch | What it does | Off means |
|---|---|---|
| `retina` | Wine's Retina mode: render at the panel's physical resolution | The prefix's Retina setting from before it was turned on |
| `srgb` | [mtld3d](https://github.com/athei/mtld3d) `color.space = accurate` in `mtld3d.conf` | The key is gone (and the file, when no other patch needs it) |
| `textures` | A texture pack's files over the client's folders, originals kept | Original files live, the pack kept aside for an instant switch back |

Every change is recorded in `.daoc-gfx/` inside the client's app folder, and
every command that changes something refuses to run while the client is running
from that folder. Changes take effect at the next client launch.

## Requirements

- macOS on APFS (backups are APFS clones; on other file systems they are full copies)
- `python3` 3.9 or newer (the Xcode Command Line Tools one is enough); nothing to install
- For `retina`: the CrossOver-based Wine and the prefix the client runs in
  (`dist/wine` and `dist/prefix` of an OfflineDAoC-macOS install)

Archives are read with macOS's own `bsdtar`, which handles `.7z`, `.zip` and tar files.

## Usage

```bash
./daoc-gfx --app <client app folder> [--wine <wine folder> --prefix <wine prefix>] <command>
```

The app folder is the one holding `game.dll`
(`dist/OfflineDAoC/runtime/client-opendaoc/app` in an OfflineDAoC-macOS install).
From an OfflineDAoC-macOS checkout, `./graphics.sh <command>` fills in all three
paths, and `./graphics.sh` alone opens the checklist.

```bash
daoc-gfx … select                         # checklist: toggle by number, Enter applies
daoc-gfx … status                         # each patch and whether it is on
daoc-gfx … list                           # the patches and what each does
daoc-gfx … enable retina srgb
daoc-gfx … enable textures --archives A.7z B.7z   # archives only to install the pack
daoc-gfx … disable srgb textures
```

The checklist marks a patch `[x]` when it is on, and `[~]` when only some of
the texture pack's folders have their pack files live (left as they are unless
you toggle it). Turning `textures` on before a pack is installed asks for its
archives; drag every part into the terminal.

Texture-pack folders in detail:

```bash
daoc-gfx … textures inspect A.7z B.7z     # which client folders the pack replaces
daoc-gfx … textures install A.7z B.7z [-c zones …]
daoc-gfx … textures use original [-c zones …]   # originals live, pack kept aside
daoc-gfx … textures use pack
daoc-gfx … textures uninstall [-c zones …]      # originals back, pack files deleted
daoc-gfx … textures recover               # after an interrupted operation
daoc-gfx … textures status
```

### Display patches

- **`retina`.** Wine's Mac driver normally presents the screen at its point size, so
  on a 4K panel at the default "looks like 1920x1080" scaling the game renders 1920x1080
  and macOS upscales it. With `RetinaMode` set in the prefix
  (`HKCU\Software\Wine\Mac Driver`) the game sees the physical pixels and renders at
  3840x2160. The interface is drawn at the same pixel size, so it looks half as large.
  The client's own `ui_scale` is in `user.dat`; this tool does not change it.
- **`srgb`.** mtld3d tags its output as sRGB, so macOS colour-manages it onto the
  panel; the default (`color.space = passthrough`) shows the game's colours at the
  panel's native primaries, more saturated on a wide-gamut display. See mtld3d's
  [`mtld3d.conf`](https://github.com/athei/mtld3d/blob/main/mtld3d.conf).

`mtld3d.conf` is written next to `game.dll` with a first-line marker. A
`mtld3d.conf` of your own (no marker) is never overwritten or deleted: mtld3d
patches are refused instead, and `retina` still works.

### Texture packs

A pack is one or more archives whose top-level folders are client folders
(`zones/…`, `figures/…`, `items/…`). Folder names match regardless of case, as
the game's own file lookups do. `install` refuses a pack that has files outside
a folder, a folder the client does not have, the same file in two parts, or a
path that leaves the client folder; a refused pack changes nothing.

Installing a folder keeps its originals as `<folder>.backup` and writes the
pack's files over the live folder. Files the pack does not have stay as they
were. Switching renames folders, so it is instant:

| Live | `<folder>` | Kept aside |
|---|---|---|
| pack | pack files over the originals | `<folder>.backup` (originals) |
| originals | originals | `<folder>.texturepack` (pack files) |

The archives are unpacked to `.daoc-gfx/staging` first, so a damaged archive or a
full disk fails before any client folder changes. Each changing operation writes a
journal before its first change. If one is interrupted (crash, power loss),
every other operation refuses until `textures recover` has undone the install,
settled the switch, or finished the uninstall.

To install a different pack over an installed folder, uninstall it first.

#### Sqeak's DAoC Textures

[Sqeak's DAoC Textures](https://www.nexusmods.com/darkageofcamelot/mods/2) comes
as two archives (`Sqeaks-DAoC-Textures-v0.1-Part1.7z` and `-Part2.7z`) and a
Windows installer that this tool replaces on macOS. Download both archives
from Nexus Mods (it needs a Nexus account), then check and install both parts
at once (or turn `textures` on in `./graphics.sh` and drag them in):

```bash
./graphics.sh textures inspect ~/Downloads/Sqeaks-DAoC-Textures-v0.1-Part{1,2}.7z
./graphics.sh enable textures --archives ~/Downloads/Sqeaks-DAoC-Textures-v0.1-Part{1,2}.7z
```

The pack was made and tested for the Eden server's client; on this client it is
untested. Its author notes that a few textures (bedsheets) vanish at range.

#### Limits worth knowing

The client is a 32-bit program. Larger textures take more of its address space,
and mtld3d tells the game it has 1024 MB of video memory (`memory.vramBudgetMB`).
A pack that replaces many textures at a higher resolution can exhaust that space;
mtld3d's log (`mtld3d-logs/` next to `game.dll`) reports failed Metal command
buffers when it happens. Packs need DDS textures in DXT1/DXT3/DXT5 with full
mipmap chains, the formats the client already uses.

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests build small `.7z` packs with `bsdtar` and run the install, switch,
uninstall and recovery paths and the patch toggles and checklist on a
temporary client folder.

## Credits

The folder-variant scheme (`.backup` / `.texturepack`) follows Sqeak's
[Windows installer](https://github.com/pfisher99/Eden-DAoC-Texture-Installer).
mtld3d and the Wine build it runs on are [athei](https://github.com/athei)'s.

## License

GPL-3.0, like OfflineDAoC-macOS.
