#!/usr/bin/env python3
"""
Incremental, consistency-first cover art fetcher for the BC-250 EmuDeck/
Pegasus library.

The goal is a single flat-2D look everywhere, not a mix of flat scans and
3D box renders. So instead of treating "already has some art" as good
enough, flat art (libretro-thumbnails / SteamGridDB) is always preferred
over whatever's in the legacy per-system boxart/ folder (which contains a
mix of flat and 3D-rendered images from an old scrape). Resolution order
per ROM:

  1. Already in this tool's own flat-art cache (boxart-flat/)? Use it -
     no network call. This is what makes reruns fast and safe: once a
     game is resolved with flat art, it's never re-fetched.
  2. Try to fetch flat art: libretro-thumbnails for systems it covers (no
     account needed), SteamGridDB for everything else (needs a free API
     key - see below). Save into boxart-flat/ so future runs skip it.
  3. Nothing flat available? Fall back to the legacy boxart/ folder (may
     be a 3D render) so the game still has *something* rather than
     nothing - logged separately so these are easy to find later.

Mirroring into media/box2dfront/ (from whichever tier resolved) is
required because Pegasus's Skraper asset provider matches by the asset's
full relative path under the collection root, not just filename - it has
to mirror whatever subfolder structure (roms/, roms/<region>/, ...) the
ROM itself has.

SteamGridDB key: put it (just the raw key, nothing else) in
~/.config/cover-art-fetcher/steamgriddb.key - a free account at
https://www.steamgriddb.com/profile/preferences/api gets you one. Until
that file exists, SteamGridDB lookups are skipped.
"""
import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

EMU = "/var/mnt/wwn-0x5000cca2dbc72ae7-part1/EmuDeck/Emulation"
ROMS_ROOT = os.path.join(EMU, "roms")
TOOLS_MEDIA = os.path.join(EMU, "tools", "downloaded_media")

CONFIG_DIR = os.path.expanduser("~/.config/cover-art-fetcher")
SGDB_KEY_FILE = os.path.join(CONFIG_DIR, "steamgriddb.key")
LOG_FILE = os.path.join(CONFIG_DIR, "not_found.log")
FALLBACK_LOG_FILE = os.path.join(CONFIG_DIR, "used_3d_fallback.log")

# Generic message area in Harbor's bottom bar, not owned by this tool - any
# script can write {active, message, done, total} here and it'll show up
# the same way. See bc250-console-setup/README.md "Harbor status messages".
HARBOR_STATUS_FILE = os.path.expanduser("~/.config/pegasus-frontend/harbor_status.json")

# How often (in completed items) the in-progress status file gets rewritten
# during a big system - frequent enough to feel live, not so frequent it
# hammers the disk.
STATUS_WRITE_EVERY = 5


def write_status(active, message="", done=0, total=0):
    """Atomic write (temp file + rename) so a reader never sees a half
    written file. A reader should treat the status as stale/finished if
    updated_at is more than a few seconds old - covers the process being
    killed rather than exiting cleanly."""
    payload = {"active": active, "message": message, "done": done, "total": total, "updated_at": time.time()}
    tmp = HARBOR_STATUS_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(HARBOR_STATUS_FILE), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, HARBOR_STATUS_FILE)
    except OSError:
        pass

SKIP_DIR_NAMES = {
    "media", "boxart", "boxart-flat", "screenshot", "screenshots", "video",
    "videos", "wheel", "marquees", "covers", "images", "manuals", ".git",
}
IMG_EXTS = (".png", ".jpg", ".jpeg")

# Collections that aren't real ROM libraries, or that already get art from
# elsewhere (Steam's own store art via Pegasus's steam provider) - never
# fetched for either source.
BASE_EXCLUDE_COLLECTIONS = {
    "steam", "desktop", "cloud", "remoteplay", "generic-applications",
    "lutris", "epic", "kodi", "moonlight", "ports", "emulators", "scripts",
}

# Systems toggled off in the Pegasus launcher (see disabled_systems.txt,
# shared with game_dirs.txt so a system removed from one is skipped by the
# other too) - no point spending scrape time on something that isn't shown.
DISABLED_SYSTEMS_FILE = os.path.join(CONFIG_DIR, "disabled_systems.txt")


def load_disabled_systems():
    try:
        with open(DISABLED_SYSTEMS_FILE, "r") as f:
            return {line.strip() for line in f if line.strip() and not line.startswith("#")}
    except FileNotFoundError:
        return set()


EXCLUDE_COLLECTIONS = BASE_EXCLUDE_COLLECTIONS | load_disabled_systems()

LIBRETRO_WORKERS = 12
LIBRETRO_TIMEOUT = 10
SGDB_REQUEST_DELAY_SECONDS = 1.0  # SteamGridDB is a real rate-limited API
SGDB_TIMEOUT = 12

# Verified 2026-09-13 against the actual libretro-thumbnails repo folder
# listing (https://github.com/libretro-thumbnails/libretro-thumbnails) - do
# not guess folder names, they often don't match the obvious short name
# (e.g. GX4000 has its own folder, separate from Amstrad CPC; Amiga CD32's
# real folder is "Commodore - CD32", not "Commodore - Amiga CD32").
# Systems NOT in this map fall through to SteamGridDB automatically.
LIBRETRO_SYSTEMS = {
    "atari2600": "Atari - 2600", "atari5200": "Atari - 5200",
    "atari7800": "Atari - 7800", "atari800": "Atari - 8-bit Family",
    "atarixe": "Atari - 8-bit Family",
    "lynx": "Atari - Lynx", "atarilynx": "Atari - Lynx",
    "jaguar": "Atari - Jaguar", "atarijaguar": "Atari - Jaguar",
    "atarist": "Atari - ST", "doom": "DOOM", "dos": "DOS",
    "fbneo": "FBNeo - Arcade Games", "fba": "FBNeo - Arcade Games",
    "pcengine": "NEC - PC Engine - TurboGrafx 16",
    "turbografx16": "NEC - PC Engine - TurboGrafx 16", "tg16": "NEC - PC Engine - TurboGrafx 16",
    "pcenginecd": "NEC - PC Engine CD - TurboGrafx-CD",
    "turbografxcd": "NEC - PC Engine CD - TurboGrafx-CD", "tg-cd": "NEC - PC Engine CD - TurboGrafx-CD",
    "supergrafx": "NEC - PC Engine SuperGrafx",
    "pc88": "NEC - PC-88", "pc98": "NEC - PC-98", "pcfx": "NEC - PC-FX",
    "gb": "Nintendo - Game Boy", "gba": "Nintendo - Game Boy Advance",
    "gbc": "Nintendo - Game Boy Color", "gc": "Nintendo - GameCube",
    "3ds": "Nintendo - Nintendo 3DS", "n3ds": "Nintendo - Nintendo 3DS",
    "n64": "Nintendo - Nintendo 64", "n64dd": "Nintendo - Nintendo 64DD",
    "nds": "Nintendo - Nintendo DS",
    "nes": "Nintendo - Nintendo Entertainment System",
    "famicom": "Nintendo - Nintendo Entertainment System",
    "fds": "Nintendo - Family Computer Disk System",
    "pokemini": "Nintendo - Pokemon Mini",
    "snes": "Nintendo - Super Nintendo Entertainment System",
    "sfc": "Nintendo - Super Nintendo Entertainment System",
    "satellaview": "Nintendo - Satellaview", "sufami": "Nintendo - Sufami Turbo",
    "virtualboy": "Nintendo - Virtual Boy",
    "wii": "Nintendo - Wii", "wiiu": "Nintendo - Wii U",
    "xbox": "Microsoft - Xbox", "xbox360": "Microsoft - Xbox 360",
    "neogeo": "SNK - Neo Geo", "neogeocd": "SNK - Neo Geo CD",
    "ngp": "SNK - Neo Geo Pocket", "ngpc": "SNK - Neo Geo Pocket Color",
    "scummvm": "ScummVM", "sega32x": "Sega - 32X",
    "dreamcast": "Sega - Dreamcast", "gamegear": "Sega - Game Gear",
    "mastersystem": "Sega - Master System - Mark III",
    "genesis": "Sega - Mega Drive - Genesis",
    "genesiswide": "Sega - Mega Drive - Genesis",
    "megadrive": "Sega - Mega Drive - Genesis",
    "segacd": "Sega - Mega-CD - Sega CD", "megacd": "Sega - Mega-CD - Sega CD",
    "saturn": "Sega - Saturn", "naomi": "Sega - Naomi", "naomigd": "Sega - Naomi 2",
    "sg1000": "Sega - SG-1000", "sg-1000": "Sega - SG-1000",
    "psx": "Sony - PlayStation", "ps2": "Sony - PlayStation 2",
    "ps3": "Sony - PlayStation 3", "ps4": "Sony - PlayStation 4",
    "psp": "Sony - PlayStation Portable", "psvita": "Sony - PlayStation Vita",
    "3do": "The 3DO Company - 3DO",
    "amstradcpc": "Amstrad - CPC", "gx4000": "Amstrad - GX4000",
    "colecovision": "Coleco - ColecoVision", "coleco": "Coleco - ColecoVision",
    "intellivision": "Mattel - Intellivision", "lutro": "Lutro",
    "msx": "Microsoft - MSX", "msx1": "Microsoft - MSX",
    "msx2": "Microsoft - MSX2", "tic80": "TIC-80",
    "vectrex": "GCE - Vectrex", "zxspectrum": "Sinclair - ZX Spectrum",
    "zx81": "Sinclair - ZX 81",
    "wswan": "Bandai - WonderSwan", "wswanc": "Bandai - WonderSwan Color",
    "channelf": "Fairchild - Channel F",
    "c64": "Commodore - 64", "c16": "Commodore - Plus-4",
    "vic20": "Commodore - VIC-20",
    "amiga": "Commodore - Amiga", "amiga600": "Commodore - Amiga",
    "amiga1200": "Commodore - Amiga",
    "amigacd32": "Commodore - CD32", "amigacdtv": "Commodore - CDTV",
    "cdi": "Philips - CD-i", "videopac": "Philips - Videopac+",
    "odyssey2": "Magnavox - Odyssey2",
    "supervision": "Watara - Supervision",
    "x1": "Sharp - X1", "x68000": "Sharp - X68000",
    "moto": "Thomson - MOTO", "to8": "Thomson - MOTO",
    "gamecom": "Tiger - Game.com", "vsmile": "VTech - V.Smile",
    "gp32": "GamePark - GP32",
}


def load_sgdb_key():
    try:
        with open(SGDB_KEY_FILE, "r") as f:
            key = f.read().strip()
            return key or None
    except FileNotFoundError:
        return None


def read_extensions(metadata_path):
    try:
        with open(metadata_path, "r", errors="ignore") as f:
            for line in f:
                if line.lower().startswith("extensions:"):
                    exts = [e.strip().lower() for e in line.split(":", 1)[1].split(",")]
                    return {e if e.startswith(".") else "." + e for e in exts if e}
    except FileNotFoundError:
        pass
    return None


def find_rom_files(sys_dir, extensions):
    results = []
    for dirpath, dirnames, filenames in os.walk(sys_dir, followlinks=False):
        for d in list(dirnames):
            full = os.path.join(dirpath, d)
            if d in SKIP_DIR_NAMES or os.path.islink(full):
                dirnames.remove(d)
        for f in filenames:
            if extensions is None or os.path.splitext(f)[1].lower() in extensions:
                results.append(os.path.join(dirpath, f))
    return results


def find_art_map(art_dir):
    m = {}
    for dirpath, dirnames, filenames in os.walk(art_dir, followlinks=False):
        for f in filenames:
            base, ext = os.path.splitext(f)
            if ext.lower() in IMG_EXTS:
                m.setdefault(base, os.path.join(dirpath, f))
    return m


def clean_title_for_search(basename):
    t = re.sub(r"\([^)]*\)", "", basename)
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or basename


def http_json(url, headers=None, timeout=SGDB_TIMEOUT):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def libretro_url(remote_system, title):
    encoded_title = urllib.parse.quote(title + ".png")
    encoded_system = urllib.parse.quote(remote_system)
    return f"http://thumbnails.libretro.com/{encoded_system}/Named_Boxarts/{encoded_title}"


def check_libretro_exists(remote_system, title):
    """HEAD-only check, meant to run concurrently across many games."""
    url = libretro_url(remote_system, title)
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=LIBRETRO_TIMEOUT) as resp:
            return resp.status == 200
    except Exception:
        return False


def download(url, dest_path):
    try:
        urllib.request.urlretrieve(url, dest_path)
        return True
    except Exception:
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def try_steamgriddb(title, dest_path, key):
    headers = {"Authorization": f"Bearer {key}"}
    search_url = "https://www.steamgriddb.com/api/v2/search/autocomplete/" + urllib.parse.quote(title)
    try:
        data = http_json(search_url, headers)
        if not data.get("success") or not data.get("data"):
            return False
        game_id = data["data"][0]["id"]
        grids_url = f"https://www.steamgriddb.com/api/v2/grids/game/{game_id}?dimensions=600x900&limit=1"
        gdata = http_json(grids_url, headers)
        if not gdata.get("success") or not gdata.get("data"):
            return False
        img_url = gdata["data"][0]["url"]
        return download(img_url, dest_path)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, IndexError):
        return False


def mirror_to_box2dfront(system, rel_path_noext, src_path):
    real_box2dfront = os.path.join(TOOLS_MEDIA, system, "box2dfront")
    rel_dir = os.path.dirname(rel_path_noext)
    dest_dir = os.path.join(real_box2dfront, rel_dir) if rel_dir else real_box2dfront
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(rel_path_noext)
    ext = os.path.splitext(src_path)[1]
    dest = os.path.join(dest_dir, base + ext)
    if os.path.islink(dest) or os.path.exists(dest):
        os.remove(dest)
    os.symlink(os.path.abspath(src_path), dest)
    return dest


def process_system(system, sgdb_key, dry_run, not_found_log, fallback_log, opts):
    sys_dir = os.path.join(ROMS_ROOT, system)
    if not os.path.isdir(sys_dir) or os.path.islink(sys_dir) or system in EXCLUDE_COLLECTIONS:
        return None

    metadata_path = os.path.join(sys_dir, "metadata.txt")
    if not os.path.isfile(metadata_path):
        return None
    extensions = read_extensions(metadata_path)

    roms = find_rom_files(sys_dir, extensions)
    if not roms:
        return None

    flat_dir = os.path.join(sys_dir, "boxart-flat")
    legacy_dir = os.path.join(sys_dir, "boxart")
    flat_map = find_art_map(flat_dir) if os.path.isdir(flat_dir) else {}
    legacy_map = find_art_map(legacy_dir) if (os.path.isdir(legacy_dir) and opts.allow_3d_fallback) else {}

    remote_system = LIBRETRO_SYSTEMS.get(system) if opts.use_libretro else None
    use_sgdb = remote_system is None and sgdb_key is not None and opts.use_sgdb

    stats = {"flat_cached": 0, "fetched_flat": 0, "used_3d_fallback": 0, "not_found": 0, "skipped_no_source": 0}

    # Entries still needing a fetch attempt: (rom_path, rel_noext, base)
    to_fetch = []

    for rom_path in roms:
        rel = os.path.relpath(rom_path, sys_dir)
        rel_dir = os.path.dirname(rel)
        base = os.path.splitext(os.path.basename(rel))[0]
        rel_noext = os.path.join(rel_dir, base) if rel_dir else base

        if base in flat_map:
            stats["flat_cached"] += 1
            if not dry_run:
                mirror_to_box2dfront(system, rel_noext, flat_map[base])
            continue

        to_fetch.append((rel_noext, base))

    if not to_fetch:
        return stats

    if dry_run:
        for rel_noext, base in to_fetch:
            if remote_system is not None or use_sgdb:
                stats["fetched_flat"] += 1
            elif base in legacy_map:
                stats["used_3d_fallback"] += 1
            else:
                stats["not_found"] += 1
        return stats

    os.makedirs(flat_dir, exist_ok=True)

    total_to_fetch = len(to_fetch)
    done_count = 0

    def fetching_message():
        return f"Cover art: {system.upper()} {done_count}/{total_to_fetch}"

    write_status(True, fetching_message(), done_count, total_to_fetch)

    if remote_system is not None:
        # Concurrent HEAD checks against the static libretro CDN.
        with ThreadPoolExecutor(max_workers=LIBRETRO_WORKERS) as pool:
            futures = {
                pool.submit(check_libretro_exists, remote_system, base): (rel_noext, base)
                for rel_noext, base in to_fetch
            }
            still_missing = []
            for fut in as_completed(futures):
                rel_noext, base = futures[fut]
                if fut.result():
                    dest = os.path.join(flat_dir, base + ".png")
                    if download(libretro_url(remote_system, base), dest):
                        stats["fetched_flat"] += 1
                        mirror_to_box2dfront(system, rel_noext, dest)
                        done_count += 1
                        if done_count % STATUS_WRITE_EVERY == 0:
                            write_status(True, fetching_message(), done_count, total_to_fetch)
                        continue
                still_missing.append((rel_noext, base))
                done_count += 1
                if done_count % STATUS_WRITE_EVERY == 0:
                    write_status(True, fetching_message(), done_count, total_to_fetch)
        to_fetch = still_missing

    for rel_noext, base in to_fetch:
        found = False
        if use_sgdb:
            dest = os.path.join(flat_dir, base + ".png")
            found = try_steamgriddb(clean_title_for_search(base), dest, sgdb_key)
            time.sleep(SGDB_REQUEST_DELAY_SECONDS)
            if found:
                stats["fetched_flat"] += 1
                mirror_to_box2dfront(system, rel_noext, dest)

        if not found:
            if base in legacy_map:
                stats["used_3d_fallback"] += 1
                mirror_to_box2dfront(system, rel_noext, legacy_map[base])
                fallback_log.write(f"{system}\t{rel_noext}\n")
            else:
                stats["not_found"] += 1
                not_found_log.write(f"{system}\t{rel_noext}\n")

        done_count += 1
        if done_count % STATUS_WRITE_EVERY == 0:
            write_status(True, fetching_message(), done_count, total_to_fetch)

    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--system", help="Only process this one system (short name, e.g. gc, snes)")
    parser.add_argument("--dry-run", action="store_true", help="Report counts without downloading or writing anything")
    parser.add_argument("--no-3d-fallback", action="store_true",
                         help="Never use the legacy boxart/ folder (may contain 3D box renders) as a last resort - "
                              "leave those games with no flat art logged as not-found instead")
    parser.add_argument("--no-libretro", action="store_true", help="Skip libretro-thumbnails entirely")
    parser.add_argument("--no-steamgriddb", action="store_true", help="Skip SteamGridDB entirely, even if a key is configured")
    args = parser.parse_args()

    class Opts:
        allow_3d_fallback = not args.no_3d_fallback
        use_libretro = not args.no_libretro
        use_sgdb = not args.no_steamgriddb

    opts = Opts()

    os.makedirs(CONFIG_DIR, exist_ok=True)
    sgdb_key = load_sgdb_key() if opts.use_sgdb else None
    if sgdb_key:
        print("SteamGridDB: key found, will be used for non-libretro systems.")
    elif opts.use_sgdb:
        print(f"SteamGridDB: no key at {SGDB_KEY_FILE}, running libretro-only.")
    else:
        print("SteamGridDB: disabled for this run (--no-steamgriddb).")
    if not opts.use_libretro:
        print("libretro-thumbnails: disabled for this run (--no-libretro).")
    if not opts.allow_3d_fallback:
        print("3D-art fallback: disabled for this run (--no-3d-fallback) - misses stay missing instead.")
    print()

    systems = [args.system] if args.system else sorted(os.listdir(ROMS_ROOT))

    totals = {"flat_cached": 0, "fetched_flat": 0, "used_3d_fallback": 0, "not_found": 0, "skipped_no_source": 0}
    touched = 0

    if not args.dry_run:
        write_status(True, "Cover art: starting…")

    with open(LOG_FILE, "a") as not_found_log, open(FALLBACK_LOG_FILE, "a") as fallback_log:
        for system in systems:
            if not args.dry_run:
                write_status(True, f"Cover art: scanning {system.upper()}…")
            stats = process_system(system, sgdb_key, args.dry_run, not_found_log, fallback_log, opts)
            if stats is None:
                continue
            if sum(stats.values()) == 0:
                continue
            touched += 1
            for k, v in stats.items():
                totals[k] += v
            print(f"{system}: {stats['flat_cached']} already flat, +{stats['fetched_flat']} fetched flat, "
                  f"{stats['used_3d_fallback']} fell back to 3D art, {stats['not_found']} not found at all",
                  flush=True)

    print()
    mode = "[DRY RUN] " if args.dry_run else ""
    print(f"{mode}TOTAL across {touched} systems: "
          f"{totals['flat_cached']} already flat, +{totals['fetched_flat']} fetched flat, "
          f"{totals['used_3d_fallback']} fell back to 3D art, {totals['not_found']} not found at all")
    if totals["used_3d_fallback"]:
        print(f"3D-fallback games logged to {FALLBACK_LOG_FILE} (re-run later once a flat source covers them)")
    if totals["not_found"]:
        print(f"Complete misses logged to {LOG_FILE}")

    if not args.dry_run:
        write_status(False)


if __name__ == "__main__":
    main()
