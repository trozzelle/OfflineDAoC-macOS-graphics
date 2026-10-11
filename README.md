# OfflineDAoC-macOS-graphics

Optional graphical patches for the Dark Age of Camelot 1.127 client running
under Wine on macOS, as installed by
[OfflineDAoC-macOS](https://github.com/trozzelle/OfflineDAoC-macOS). That repo's
`graphics.sh` pulls a pinned commit of this one and runs it against its install;
`daoc-gfx` also works on its own against any such client.

DAoC is a property of Mythic Entertainment / Broadsword Online Games (Electronic Arts).
This is unaffiliated fan tooling. It ships no game files and no third-party art:
texture packs are archives you download yourself.

## What it patches

| Area | What it does | Undo |
|---|---|---|
| Display presets | Wine's Retina mode (render at the panel's physical resolution) and [mtld3d](https://github.com/athei/mtld3d) settings in `mtld3d.conf` | `display revert` |
| Texture packs | Installs a pack's folders over the client's, keeping the originals; switches between the two at any time | `textures uninstall` |

Every change is recorded in `.daoc-gfx/` inside the client's app folder, and
every command that changes something refuses to run while the client is running
from that folder.

## Requirements

- macOS on APFS (backups are APFS clones; on other file systems they are full copies)
- `python3` 3.9 or newer (the Xcode Command Line Tools one is enough); nothing to install
- For display presets: the CrossOver-based Wine and the prefix the client runs in
  (`dist/wine` and `dist/prefix` of an OfflineDAoC-macOS install)

Archives are read with macOS's own `bsdtar`, which handles `.7z`, `.zip` and tar files.

## Usage

```bash
./daoc-gfx --app <client app folder> [--wine <wine folder> --prefix <wine prefix>] <command>
```

The app folder is the one holding `game.dll`
(`dist/OfflineDAoC/runtime/client-opendaoc/app` in an OfflineDAoC-macOS install).
From an OfflineDAoC-macOS checkout, `./graphics.sh <command>` fills in all three paths.

```bash
daoc-gfx … status                         # what is applied to this client

daoc-gfx … display list                   # presets and what each does
daoc-gfx … display apply retina-srgb      # takes effect at the next launch
daoc-gfx … display status
daoc-gfx … display revert                 # back to the settings before the first preset

daoc-gfx … textures inspect A.7z B.7z     # which client folders the pack replaces
daoc-gfx … textures install A.7z B.7z [-c zones …]
daoc-gfx … textures use original [-c zones …]   # originals live, pack kept aside
daoc-gfx … textures use pack
daoc-gfx … textures uninstall [-c zones …]      # originals back, pack files deleted
daoc-gfx … textures recover               # after an interrupted operation
```

### Display presets

| Preset | Retina mode | mtld3d.conf |
|---|---|---|
| `retina` | on | (none) |
| `srgb` | as before the first preset | `color.space = accurate` |
| `retina-srgb` | on | `color.space = accurate` |

- **Retina mode.** Wine's Mac driver normally presents the screen at its point size, so
  on a 4K panel at the default "looks like 1920x1080" scaling the game renders 1920x1080
  and macOS upscales it. With `RetinaMode` set in the prefix
  (`HKCU\Software\Wine\Mac Driver`) the game sees the physical pixels and renders at
  3840x2160. The interface is drawn at the same pixel size, so it looks half as large.
  The client's own `ui_scale` is in `user.dat`; this tool does not change it.
- **`color.space = accurate`.** mtld3d tags its output as sRGB, so macOS colour-manages
  it onto the panel; the default (`passthrough`) shows the game's colours at the panel's
  native primaries, more saturated on a wide-gamut display. See mtld3d's
  [`mtld3d.conf`](https://github.com/athei/mtld3d/blob/main/mtld3d.conf).

`mtld3d.conf` is written next to `game.dll` with a first-line marker. A
`mtld3d.conf` of your own (no marker) is never overwritten or deleted: the
preset is refused instead.

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
from Nexus Mods (it needs a Nexus account), then pass both parts at once:

```bash
./graphics.sh textures inspect ~/Downloads/Sqeaks-DAoC-Textures-v0.1-Part{1,2}.7z
./graphics.sh textures install ~/Downloads/Sqeaks-DAoC-Textures-v0.1-Part{1,2}.7z
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
uninstall and recovery paths on a temporary client folder.

## Credits

The folder-variant scheme (`.backup` / `.texturepack`) follows Sqeak's
[Windows installer](https://github.com/pfisher99/Eden-DAoC-Texture-Installer).
mtld3d and the Wine build it runs on are [athei](https://github.com/athei)'s.

## License

GPL-3.0, like OfflineDAoC-macOS.
