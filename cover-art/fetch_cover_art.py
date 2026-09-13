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
# elsewhere, or aren't real ROM libraries at all - never fetched for
# either source. Steam used to be excluded on the assumption Pegasus's own
# steam provider art was good enough - it isn't reliably (see the
# header.jpg/grid landscape-crop fix in GameGrid.qml), so it's included now.
BASE_EXCLUDE_COLLECTIONS = {
    "desktop", "cloud", "remoteplay", "generic-applications",
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
                continue
            # A directory whose own name matches a declared extension is a
            # folder-based "rom" (e.g. RPCS3 dumps are directories named
            # "<Title>.ps3/") - it's one game unit, not a folder to search
            # inside of. Recursing into it would otherwise surface
            # thousands of the game's own internal asset files as if each
            # were a separate "rom".
            if extensions is not None and os.path.splitext(d)[1].lower() in extensions:
                results.append(full)
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


# SteamGridDB's API returns a bare 403 Forbidden to Python's default
# urllib User-Agent string ("Python-urllib/3.x") - confirmed via direct
# testing, not a documented policy. curl and a browser both work fine
# against the identical URL/key. Every outgoing request uses this header
# so nothing silently 403s again.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) bc250-cover-art-fetcher/1.0"


def http_json(url, headers=None, timeout=SGDB_TIMEOUT):
    all_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, headers=all_headers)
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
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=LIBRETRO_TIMEOUT) as resp:
            return resp.status == 200
    except Exception:
        return False


def download(url, dest_path):
    """urlretrieve() can't set custom headers, so this uses urlopen
    directly and streams the response to disk instead - needed for the
    same User-Agent reason as http_json()."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=SGDB_TIMEOUT) as resp, open(dest_path, "wb") as out:
            out.write(resp.read())
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


def mirror_asset(box2dfront_root, rel_path_noext, src_path):
    rel_dir = os.path.dirname(rel_path_noext)
    dest_dir = os.path.join(box2dfront_root, rel_dir) if rel_dir else box2dfront_root
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(rel_path_noext)
    ext = os.path.splitext(src_path)[1]
    dest = os.path.join(dest_dir, base + ext)
    if os.path.islink(dest) or os.path.exists(dest):
        os.remove(dest)
    os.symlink(os.path.abspath(src_path), dest)
    return dest


def try_steamgriddb_by_steam_appid(appid, dest_path, key):
    """Look up grid art directly by Steam AppID - more reliable than a
    fuzzy name search for games Steam itself knows about."""
    headers = {"Authorization": f"Bearer {key}"}
    grids_url = f"https://www.steamgriddb.com/api/v2/grids/steam/{appid}?dimensions=600x900&limit=1"
    try:
        gdata = http_json(grids_url, headers)
        if not gdata.get("success") or not gdata.get("data"):
            return False
        img_url = gdata["data"][0]["url"]
        return download(img_url, dest_path)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, IndexError):
        return False


def parse_acf_name(path):
    try:
        with open(path, "r", errors="ignore") as f:
            content = f.read()
        m = re.search(r'"name"\s*"([^"]*)"', content)
        return m.group(1) if m else None
    except OSError:
        return None


def find_owned_steam_games():
    """(appid, name) for every Steam-owned game with a local install manifest."""
    steamapps = os.path.expanduser("~/.local/share/Steam/steamapps")
    games = []
    if not os.path.isdir(steamapps):
        return games
    for f in os.listdir(steamapps):
        if f.startswith("appmanifest_") and f.endswith(".acf"):
            appid = f[len("appmanifest_"):-len(".acf")]
            name = parse_acf_name(os.path.join(steamapps, f))
            if name:
                games.append((appid, name))
    return games


def find_shortcut_games():
    """(userdata_id, appid, name) for every non-Steam shortcut, across every
    local Steam profile."""
    import vdf
    userdata = os.path.expanduser("~/.local/share/Steam/userdata")
    entries = []
    if not os.path.isdir(userdata):
        return entries
    for uid in os.listdir(userdata):
        sc_path = os.path.join(userdata, uid, "config", "shortcuts.vdf")
        if not os.path.isfile(sc_path):
            continue
        try:
            with open(sc_path, "rb") as f:
                data = vdf.binary_load(f)
        except Exception:
            continue
        for _, entry in data.get("shortcuts", {}).items():
            appid = entry.get("appid")
            name = entry.get("AppName") or entry.get("appname")
            if appid is None or not name:
                continue
            entries.append((uid, str(appid & 0xFFFFFFFF if appid < 0 else appid), name))
    return entries


def process_steam(sgdb_key, dry_run, not_found_log):
    """Steam-owned games and non-Steam shortcuts aren't files in a ROM
    folder, so they can't go through process_system()'s file-tree walk -
    read Steam's own data (install manifests, shortcuts.vdf) instead."""
    if not sgdb_key:
        return None

    library_cache = os.path.expanduser("~/.local/share/Steam/appcache/librarycache")
    userdata = os.path.expanduser("~/.local/share/Steam/userdata")

    stats = {"flat_cached": 0, "fetched_flat": 0, "used_3d_fallback": 0, "not_found": 0, "skipped_no_source": 0}
    to_fetch = []  # (kind, appid, name, dest_path)

    for appid, name in find_owned_steam_games():
        dest = os.path.join(library_cache, appid, "library_600x900.jpg")
        if os.path.exists(dest):
            stats["flat_cached"] += 1
            continue
        to_fetch.append(("owned", appid, name, dest))

    for uid, appid, name in find_shortcut_games():
        dest = os.path.join(userdata, uid, "config", "grid", f"{appid}p.png")
        if os.path.exists(dest):
            stats["flat_cached"] += 1
            continue
        to_fetch.append(("shortcut", appid, name, dest))

    if not to_fetch:
        return stats
    if dry_run:
        stats["fetched_flat"] = len(to_fetch)
        return stats

    total = len(to_fetch)
    done = 0
    write_status(True, f"Cover art: STEAM {done}/{total}", done, total)

    for kind, appid, name, dest in to_fetch:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        found = False
        if kind == "owned":
            found = try_steamgriddb_by_steam_appid(appid, dest, sgdb_key)
            time.sleep(SGDB_REQUEST_DELAY_SECONDS)
        if not found:
            found = try_steamgriddb(clean_title_for_search(name), dest, sgdb_key)
            time.sleep(SGDB_REQUEST_DELAY_SECONDS)

        if found:
            stats["fetched_flat"] += 1
        else:
            stats["not_found"] += 1
            not_found_log.write(f"steam\t{kind}:{appid}:{name}\n")

        done += 1
        if done % STATUS_WRITE_EVERY == 0:
            write_status(True, f"Cover art: STEAM {done}/{total}", done, total)

    return stats


def parse_explicit_games(metadata_path, collection_root):
    """(title, rel_path_noext) pairs from a metadata.txt that lists games
    explicitly (game:/file: pairs) instead of an extensions: line - used by
    collections where the real title doesn't match the launched file's
    basename (e.g. "A Hat in Time" launching Binaries/Win64/HatinTimeGame.exe),
    so matching by filename wouldn't work. Empty list means this metadata.txt
    uses the normal extensions:-based convention instead."""
    games = []
    try:
        with open(metadata_path, "r", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return games

    title = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("game:"):
            title = stripped[len("game:"):].strip()
        elif stripped.startswith("file:") and title:
            file_path = stripped[len("file:"):].strip()
            rel = os.path.relpath(file_path, collection_root)
            games.append((title, os.path.splitext(rel)[0]))
            title = None
    return games


def parse_game_dirs():
    path = os.path.expanduser("~/.config/pegasus-frontend/game_dirs.txt")
    dirs = []
    try:
        with open(path, "r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line:
                    dirs.append(line)
    except OSError:
        pass
    return dirs


def find_external_collections():
    """{shortname: collection_root} for every collection Pegasus scans that
    lives outside EmuDeck's roms/ tree (e.g. a custom PC-games folder) -
    discovered from Pegasus's own game_dirs.txt rather than a hardcoded
    path, so a new custom collection is picked up automatically instead of
    needing a code change here."""
    # game_dirs.txt lines can contain literal double slashes (EmuDeck's own
    # path templating leaves "Emulation//roms/..."), which would silently
    # fail a plain string-prefix check against ROMS_ROOT - normpath both
    # sides first so a real ROM system is never double-processed as an
    # "external" collection too.
    roms_root_norm = os.path.normpath(ROMS_ROOT)
    found = {}
    for d in parse_game_dirs():
        d = os.path.normpath(d)
        if d == roms_root_norm or d.startswith(roms_root_norm + os.sep):
            continue
        probe = d
        for _ in range(5):
            metadata_path = os.path.join(probe, "metadata.txt")
            if os.path.isfile(metadata_path):
                shortname = None
                try:
                    with open(metadata_path, "r", errors="ignore") as f:
                        for line in f:
                            if line.lower().startswith("shortname:"):
                                shortname = line.split(":", 1)[1].strip()
                                break
                except OSError:
                    pass
                if shortname:
                    found[shortname] = probe
                break
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
    return found


def process_system(sys_dir, shortname, box2dfront_root, sgdb_key, dry_run, not_found_log, fallback_log, opts):
    if not os.path.isdir(sys_dir) or os.path.islink(sys_dir) or shortname in EXCLUDE_COLLECTIONS:
        return None

    metadata_path = os.path.join(sys_dir, "metadata.txt")
    if not os.path.isfile(metadata_path):
        return None

    explicit_games = parse_explicit_games(metadata_path, sys_dir)
    if explicit_games:
        # (rel_noext, match_key) pairs - match_key is the declared title,
        # not a filename basename.
        entries = [(rel_noext, title) for title, rel_noext in explicit_games]
    else:
        extensions = read_extensions(metadata_path)
        rom_files = find_rom_files(sys_dir, extensions)
        if not rom_files:
            return None
        entries = []
        for rom_path in rom_files:
            rel = os.path.relpath(rom_path, sys_dir)
            rel_dir = os.path.dirname(rel)
            base = os.path.splitext(os.path.basename(rel))[0]
            rel_noext = os.path.join(rel_dir, base) if rel_dir else base
            entries.append((rel_noext, base))

    flat_dir = os.path.join(sys_dir, "boxart-flat")
    legacy_dir = os.path.join(sys_dir, "boxart")
    flat_map = find_art_map(flat_dir) if os.path.isdir(flat_dir) else {}
    legacy_map = find_art_map(legacy_dir) if (os.path.isdir(legacy_dir) and opts.allow_3d_fallback) else {}

    remote_system = LIBRETRO_SYSTEMS.get(shortname) if opts.use_libretro else None
    use_sgdb = remote_system is None and sgdb_key is not None and opts.use_sgdb

    stats = {"flat_cached": 0, "fetched_flat": 0, "used_3d_fallback": 0, "not_found": 0, "skipped_no_source": 0}

    # Entries still needing a fetch attempt: (rel_noext, base)
    to_fetch = []

    for rel_noext, base in entries:
        if base in flat_map:
            stats["flat_cached"] += 1
            if not dry_run:
                mirror_asset(box2dfront_root, rel_noext, flat_map[base])
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
        return f"Cover art: {shortname.upper()} {done_count}/{total_to_fetch}"

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
                        mirror_asset(box2dfront_root, rel_noext, dest)
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
                mirror_asset(box2dfront_root, rel_noext, dest)

        if not found:
            if base in legacy_map:
                stats["used_3d_fallback"] += 1
                mirror_asset(box2dfront_root, rel_noext, legacy_map[base])
                fallback_log.write(f"{shortname}\t{rel_noext}\n")
            else:
                stats["not_found"] += 1
                not_found_log.write(f"{shortname}\t{rel_noext}\n")

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

    run_all = args.system is None
    # "steam" is the one hardcoded special case (see below); anything else
    # named via --system is tried against both ROMS_ROOT and the
    # dynamically-discovered external collections, whichever actually
    # matches.
    systems = [] if args.system == "steam" else (
        [args.system] if args.system else sorted(os.listdir(ROMS_ROOT))
    )

    totals = {"flat_cached": 0, "fetched_flat": 0, "used_3d_fallback": 0, "not_found": 0, "skipped_no_source": 0}
    touched = 0

    def report(name, stats):
        nonlocal touched
        if stats is None or sum(stats.values()) == 0:
            return
        touched += 1
        for k, v in stats.items():
            totals[k] += v
        print(f"{name}: {stats['flat_cached']} already flat, +{stats['fetched_flat']} fetched flat, "
              f"{stats['used_3d_fallback']} fell back to 3D art, {stats['not_found']} not found at all",
              flush=True)

    if not args.dry_run:
        write_status(True, "Cover art: starting…")

    with open(LOG_FILE, "a") as not_found_log, open(FALLBACK_LOG_FILE, "a") as fallback_log:
        for system in systems:
            sys_dir = os.path.join(ROMS_ROOT, system)
            box2dfront_root = os.path.join(TOOLS_MEDIA, system, "box2dfront")
            if not args.dry_run:
                write_status(True, f"Cover art: scanning {system.upper()}…")
            report(system, process_system(sys_dir, system, box2dfront_root, sgdb_key,
                                           args.dry_run, not_found_log, fallback_log, opts))

        # Collections outside EmuDeck's roms/ tree (e.g. a custom PC-games
        # folder), discovered from Pegasus's own game_dirs.txt rather than
        # a hardcoded path - a new one added later gets picked up
        # automatically, no code change needed here.
        for shortname, collection_root in find_external_collections().items():
            if args.system and args.system != shortname:
                continue
            if shortname in EXCLUDE_COLLECTIONS:
                continue
            box2dfront_root = os.path.join(collection_root, "media", "box2dfront")
            if not args.dry_run:
                write_status(True, f"Cover art: scanning {shortname.upper()}…")
            report(shortname, process_system(collection_root, shortname, box2dfront_root, sgdb_key,
                                              args.dry_run, not_found_log, fallback_log, opts))

        # Steam-owned games and non-Steam shortcuts are the one true
        # special case left: their game list isn't declared in any
        # metadata.txt at all, it's Steam's own internal data (install
        # manifests, shortcuts.vdf) - no metadata-parsing generalization
        # can cover that.
        if run_all or args.system == "steam":
            if not args.dry_run:
                write_status(True, "Cover art: scanning STEAM…")
            report("steam", process_steam(sgdb_key, args.dry_run, not_found_log))

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
