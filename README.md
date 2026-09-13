# BC-250 Console Setup

Turns an [EmuDeck](https://emudeck.com/)-based Linux gaming box (built for a
BC-250 board, but nothing here is BC-250-specific) into a single, unified
console-style experience:

- **[Pegasus Frontend](https://pegasus-frontend.org/)** as the *one* launcher
  for emulated games, your Steam library, and non-Steam Windows PC games -
  themeable, and nothing routes through the Steam client unless you
  deliberately launch an actual Steam game.
- **A guide-button overlay** (`guide-overlay/`) that turns a controller's
  guide/Xbox button into a real console-style quick menu (Resume, Exit Game,
  Home, Restart, Shut Down) - there's no SteamOS/gamescope session providing
  this for free on a normal desktop, so it's custom-built.

## Why not just add everything to Steam / use Steam Big Picture?

Steam enforces one logged-in session per account, account-wide. Launching
*anything* through the Steam client - even a non-Steam shortcut with zero
Steam DRM involved - counts as "Steam is running and logged in" and will kick
the same account off another device. Routing games through Pegasus instead
means Steam is only ever touched (and only then locks the account) when you
deliberately pick an actual Steam-library game.

## Prerequisites

- EmuDeck already installed and configured (ROM library set up, emulators
  installed) - see [emudeck.com](https://emudeck.com/).
- For non-Steam PC games specifically: [`umu-launcher`](https://github.com/Open-Wine-Components/umu-launcher)
  and a Proton build (e.g. GE-Proton, dropped into Steam's own
  `compatibilitytools.d/`, or anywhere else you point the script at).
- For the guide-overlay: `python3-pyside6`, `python3-evdev`, `python3-psutil`
  (Fedora/Bazzite package names - install via your distro's package manager;
  on an rpm-ostree system like Bazzite that's `rpm-ostree install ...` +
  reboot, or layer them with `ujust`/toolbox depending on your setup).

## 1. Set up Pegasus

```
pegasus/setup-pegasus.sh
```

Installs Pegasus via EmuDeck's own (normally-disabled) Pegasus support,
enables its Steam library provider (off by default in EmuDeck's shipped
config - the opposite of what you want here), and wires up your existing
EmuDeck ROM library and theme automatically through EmuDeck's own
`pegasus_init`.

## Themes

`pegasus/themes/Harbor */` are from-scratch Pegasus themes (not forks of an
existing one) built to match the navigation style of
[Harbor](https://github.com/r0mn-creator/Harbor), a console-style Android game
launcher: a horizontal row of console tabs up top (with decorative L1/R1
badges), a spaced-out box-art grid below, and a bottom button-legend bar
(`Y` Favorites / hold `A` to favorite / `A` Play). Two pseudo-categories are
pinned to the front of the tab list - "Recently Played" and "Favorites" -
each hidden until there's actually something in it.

Pegasus has no live "theme the theme" color picker, so each color palette is
its own copy of the same theme rather than a setting:

- **Harbor Neon** - the original hot-pink/deep-purple palette.
- **Harbor Mono** - grayscale, dark and light gray with a soft white accent.
- **Harbor Retro** - the original Super Nintendo's light warm-gray and
  dusty-purple two-tone, with colorful controller-accurate button badges
  (green A, yellow Y) instead of monochrome ones.
- **Harbor Synthwave** - 80s/outrun palette, deep purple with warm orange
  accents.

All four share identical navigation logic; only the `theme` color object at
the top of `theme.qml` differs, plus each has an `onAccent` color (the text
color used on top of accent-colored surfaces - dark text for Mono's light
accent and Retro's SNES gray, white for the other two). To make a new
palette, copy one of these folders, rename it, and edit that one object plus
`theme.cfg`'s `name:` line.

Per-button badge colors are a separate, optional override on top of that: add
a `buttons: { A: "#hex", B: "#hex", X: "#hex", Y: "#hex" }` object to a
theme's color palette (see Harbor Retro's `theme.qml`) and `buttonColor()`/
`buttonTextColor()` will use it for that letter's badge instead of the
theme's plain `accent`/`onAccent`; omit `buttons` entirely (as Neon, Mono,
and Synthwave do) to keep monochrome badges.

To install one, copy the folder into your Pegasus themes directory and select
it in Settings:

```
cp -r "pegasus/themes/Harbor Neon" "~/.config/pegasus-frontend/themes/Harbor Neon"
```

Then restart Pegasus (it only scans for themes at startup) and pick it from
Settings > General > Theme.

## 2. Add your non-Steam PC games (optional)

If you have a folder of Windows PC games that each ship an EmuDeck-style
`.bat` launcher (`set "GAMENAME=..."`, `set "GAMEPATH=..."`), generate a
Pegasus collection for them:

```
pcgames/generate-pcgames-metadata.py \
    "/path/to/PC Games" \
    ~/Games/proton-shared/pfx \
    ~/.local/share/Steam/compatibilitytools.d/Proton-GE
```

Then add that folder to `~/.config/pegasus-frontend/game_dirs.txt`. Each game
launches via `umu-run` + Proton, completely independent of the Steam client -
see the top of the script for why. Games get a shared Wine prefix by default;
particularly finicky titles may need their own (the same tradeoff Lutris/
Heroic users already live with).

## 3. Guide-button overlay

```
python3 guide-overlay/guide_daemon.py
```

Hold the controller's guide button (~0.5s) to bring up a horizontal,
controller-navigable icon menu (Resume / Exit Game / Home / Restart / Shut
Down) over whatever's running - works even while an emulator has grabbed
exclusive controller input, since it reads the gamepad directly via `evdev`
rather than through the desktop's normal input path.

"The current game" is found by walking Pegasus's own process tree and
picking whichever descendant has accumulated the most CPU time - no
window-manager integration needed, and it works identically for native
emulators and Proton-launched PC games.

Not yet wired up as an autostart service - run it manually for now, or set
up your own `systemd --user` unit / autostart entry once you're happy with
it.

## Cover art

`cover-art/fetch_cover_art.py` is an incremental, consistency-first cover
art fetcher for the EmuDeck ROM library. It exists because the built-in
scraper options each have a real limitation: ES-DE/RetroArch's own
ScreenScraper integration has a low daily quota on free accounts, and
EmuDeck's ROM folders can end up with a mix of flat 2D scans and 3D box
renders depending on where the art originally came from.

```
python3 cover-art/fetch_cover_art.py            # whole library
python3 cover-art/fetch_cover_art.py --system snes
python3 cover-art/fetch_cover_art.py --dry-run   # report counts, no downloads
```

For each ROM, in order: (1) already resolved with flat art in that
system's `boxart-flat/` cache - skip, no network call, which is what makes
reruns fast and safe to do any time; (2) fetch flat art from
[libretro-thumbnails](https://github.com/libretro-thumbnails/libretro-thumbnails)
(covers ~100 retro systems, no account needed) or
[SteamGridDB](https://www.steamgriddb.com/) (everything else - needs a
free API key, see below); (3) nothing flat available - fall back to
whatever's in the system's legacy `boxart/` folder (which may be a 3D
render) so the game has *something*, logged separately so these are easy
to find and re-run later once a flat source covers them.

Mirrors the resolved image into `media/box2dfront/`, matching whatever
subfolder structure (`roms/`, `roms/<region>/`, ...) the ROM itself has -
required because Pegasus's Skraper asset provider matches assets by full
relative path under the collection root, not just filename.

Flags: `--no-3d-fallback` (leave misses missing instead of using 3D art),
`--no-libretro`, `--no-steamgriddb` (skip a source entirely, even if
otherwise available).

**SteamGridDB key**: put it (just the raw key) in
`~/.config/cover-art-fetcher/steamgriddb.key`. A free account at
[steamgriddb.com/profile/preferences/api](https://www.steamgriddb.com/profile/preferences/api)
gets you one. Until that file exists, only libretro-covered systems get
fetched.

The `LIBRETRO_SYSTEMS` map at the top of the script was verified against
the actual libretro-thumbnails repo folder listing - some EmuDeck system
short names don't match the obvious guess (e.g. GX4000 has its own folder,
separate from Amstrad CPC; Amiga CD32's real folder is `Commodore - CD32`,
not `Commodore - Amiga CD32`). If a system you expect to be covered keeps
missing, check the real folder name there before assuming the game itself
isn't archived.

### Disabling systems you'll never use

`cover-art/disabled_systems.txt` is a shared list of EmuDeck system short
names (one per line) that are toggled off in both places at once:

- Removed from `~/.config/pegasus-frontend/game_dirs.txt`, so Pegasus
  doesn't scan them at all - no tab, no games indexed.
- Skipped by `fetch_cover_art.py` (loaded straight into its
  `EXCLUDE_COLLECTIONS`), so no scrape time is wasted on them either.

To disable more systems: add their short names (the folder name under
`roms/`, e.g. `saturn`, `psx`) to `disabled_systems.txt`, then re-run the
same removal step used originally - match each `game_dirs.txt` line's
`roms/<name>/...` segment against the list and drop matching lines. Keep
a backup of the full `game_dirs.txt` first (`game_dirs.txt.full-backup`
in this setup) since it's easy to want a system back later, and because
EmuDeck's own "Set up/Update Pegasus" action overwrites `game_dirs.txt`
with its full template again if you ever run it.

The current list disables real consoles/computers/handhelds with a
hardware release year before 2000, keeping NES/SNES and their family
(Famicom, Super Famicom, FDS, Satellaview, Sufami Turbo, Super Game Boy)
explicitly, and leaving arcade platforms, PC-related categories, and
fantasy/homebrew consoles untouched as a separate decision.

### Known open items

- Restart Console / Shut Down call `systemctl reboot`/`poweroff` directly -
  verify these work without a password prompt in your session before relying
  on them.
- The guide button relies on your controller reporting `BTN_MODE` over
  `evdev`. Most modern controllers do; a few older Xbox controllers on Linux
  via the stock `xpad` driver don't expose it as a distinct event depending
  on connection type (USB/Bluetooth/dongle) and kernel version. Check with:
  ```
  python3 -c "import evdev; [print(d.path, d.name, evdev.ecodes.BTN_MODE in d.capabilities().get(evdev.ecodes.EV_KEY, [])) for d in [evdev.InputDevice(p) for p in evdev.list_devices()]]"
  ```
