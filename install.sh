#!/bin/bash
# NFC Toolkit installer: run ON THE PHONE as phablet from the bundle dir.
#   cd ~/nfc-toolkit-install && ./install.sh            (sudo for the click)
#   ./install.sh --backend-only                         (no sudo)
set -euo pipefail
cd "$(dirname "$0")"
SVC="$HOME/.local/share/nfc-toolkit"
UNIT_DIR="$HOME/.config/systemd/user"
echo "== backend -> $SVC"
mkdir -p "$SVC" "$UNIT_DIR"
cp backend/nfctoolkit.py "$SVC/"
cp backend/nfc-toolkit.service "$UNIT_DIR/"
systemctl --user daemon-reload
systemctl --user enable nfc-toolkit >/dev/null
systemctl --user restart nfc-toolkit
sleep 2
systemctl --user is-active nfc-toolkit
if [ ! -e /etc/systemd/system/nfcd.service.d/libncicore-fix.conf ] || \
   [ ! -e /userdata/nfc-fix/libncicore.so.1 ]; then
    echo "!! WARNING: the patched libncicore is not installed — NFC will not work."
    echo "   See vault 'Hardware - NFC' / deploy_nfcfix."
fi
if [ "${1:-}" = "--backend-only" ]; then exit 0; fi
CLICK=$(ls -1 nfctoolkit.local_*_all.click | tail -1)
echo "== app: $CLICK (sudo needed)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
sudo --preserve-env=DBUS_SESSION_BUS_ADDRESS,XDG_RUNTIME_DIR \
    click install --allow-unauthenticated --user=phablet "$CLICK"
click list | grep nfctoolkit
echo "Done. Open 'NFC Toolkit' from the app drawer."
