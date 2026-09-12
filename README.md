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
