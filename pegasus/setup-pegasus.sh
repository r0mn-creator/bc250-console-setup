#!/usr/bin/env bash
# Installs and configures Pegasus Frontend (https://pegasus-frontend.org/) as
# a single unified launcher for EmuDeck's emulator library + your Steam
# library, on top of an existing EmuDeck install.
#
# Calls EmuDeck's OWN Pegasus install functions directly (pegasus_install,
# pegasus_init) rather than running EmuDeck's full setup.sh - that would
# re-run every other emulator's install/init step too and can overwrite any
# manual config changes already made to them. This only touches Pegasus.
#
# Prerequisite: EmuDeck already installed and set up (~/.config/EmuDeck/backend
# present, ROMs library configured).
set -euo pipefail

EMUDECK_BACKEND="$HOME/.config/EmuDeck/backend/"
if [ ! -d "$EMUDECK_BACKEND" ]; then
    echo "EmuDeck backend not found at $EMUDECK_BACKEND - install/run EmuDeck first." >&2
    exit 1
fi

echo "== Installing Pegasus via EmuDeck's own installer functions =="
bash -c '
    emudeckBackend="'"$EMUDECK_BACKEND"'"
    source "$emudeckBackend/functions/all.sh"
    pegasus_install "true"
    pegasus_init
'

PEGASUS_SETTINGS="$HOME/.config/pegasus-frontend/settings.txt"
if [ -f "$PEGASUS_SETTINGS" ]; then
    echo "== Enabling Pegasus's Steam library provider =="
    # EmuDeck's shipped default has this OFF, which is the opposite of what
    # "one launcher for everything including Steam" needs. Pegasus reads
    # Steam's own libraryfolders.vdf, so no separate path config is needed -
    # it just needs to be turned on.
    sed -i 's/^providers.steam.enabled: false$/providers.steam.enabled: true/' "$PEGASUS_SETTINGS"
    grep "providers.steam.enabled" "$PEGASUS_SETTINGS"
else
    echo "Warning: $PEGASUS_SETTINGS not found after install - check pegasus_init output above." >&2
fi

echo
echo "Done. Launch with: ~/Applications/pegasus-fe"
echo "A desktop entry and Steam Big Picture shortcut were also created by pegasus_init."
