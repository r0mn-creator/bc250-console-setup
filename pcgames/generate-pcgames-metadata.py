#!/usr/bin/env python3
"""Generates a Pegasus metadata.txt for a folder of non-Steam Windows PC
games that each ship a EmuDeck-style .bat launcher (set "GAMENAME=...",
set "GAMEPATH=...", cd into it, run the exe).

Each game is launched via umu-run (https://github.com/Open-Wine-Components/umu-launcher)
+ a Proton build, completely independent of the Steam client - this avoids
Steam's single-session-per-account lock, which triggers the instant Steam
is running and logged in, even for a non-Steam game with zero Steam DRM.

Usage:
    generate-pcgames-metadata.py <pc-games-dir> <shared-wine-prefix> <proton-dir>

Writes <pc-games-dir>/metadata.txt. Point Pegasus at <pc-games-dir> by adding
it to ~/.config/pegasus-frontend/game_dirs.txt.

Games whose .bat file references a folder that no longer exists on disk are
skipped and reported - this is normal for a library where titles have been
uninstalled since the .bat files were created, not a bug in this script.
"""
import argparse
import glob
import os
import re
import sys


def find_real_exe(bat_path, games_dir):
    with open(bat_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    m_name = re.search(r'set "GAMENAME=(.+?)"', content)
    m_path = re.search(r'set "GAMEPATH=(.+?)"', content)
    if not m_name or not m_path:
        return None, "no GAMENAME/GAMEPATH in .bat"

    gamename = m_name.group(1)
    gamepath = m_path.group(1).replace("\\", "/").strip("/")
    exe_path = os.path.join(games_dir, gamepath, gamename)

    if not os.path.isfile(exe_path):
        return None, f"exe not found: {exe_path}"
    return exe_path, None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("games_dir", help="Folder containing the .bat-launcher-per-game PC games library")
    parser.add_argument("wine_prefix", help="Shared WINEPREFIX to run every game in")
    parser.add_argument("proton_dir", help="Path to a Proton build (e.g. Steam's compatibilitytools.d/Proton-GE)")
    parser.add_argument("--steam-path", default=os.path.expanduser("~/.local/share/Steam"),
                         help="STEAM_COMPAT_CLIENT_INSTALL_PATH - some Steamworks-integrated games need this set even though the Steam client itself is never launched (default: ~/.local/share/Steam)")
    args = parser.parse_args()

    games_dir = os.path.abspath(args.games_dir)
    out_path = os.path.join(games_dir, "metadata.txt")

    launch_cmd = (
        f'env GAMEID=umu-pcgames WINEPREFIX="{args.wine_prefix}" PROTONPATH="{args.proton_dir}" '
        f'STORE=none STEAM_COMPAT_CLIENT_INSTALL_PATH="{args.steam_path}" '
        'umu-run "{file.path}"'
    )

    entries = []
    skipped = []
    for bat in sorted(glob.glob(os.path.join(games_dir, "*.bat"))):
        title = os.path.splitext(os.path.basename(bat))[0]
        exe_path, err = find_real_exe(bat, games_dir)
        if err:
            skipped.append((title, err))
            continue
        entries.append((title, exe_path, os.path.dirname(exe_path)))

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("collection: PC Games\n")
        f.write("shortname: pcgames-proton\n")
        f.write(f"launch: {launch_cmd}\n")
        f.write("\n")
        for title, exe_path, workdir in entries:
            f.write(f"game: {title}\n")
            f.write(f"file: {exe_path}\n")
            f.write(f"workdir: {workdir}\n")
            f.write("\n")

    print(f"Wrote {out_path}")
    print(f"{len(entries)} games resolved, {len(skipped)} skipped")
    for title, reason in skipped:
        print(f"  SKIP: {title} - {reason}", file=sys.stderr)


if __name__ == "__main__":
    main()
