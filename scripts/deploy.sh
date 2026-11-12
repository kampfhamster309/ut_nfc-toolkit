#!/bin/bash
# Build the click and copy the install bundle to the phone.
#   scripts/deploy.sh <ssh-target> [--no-build]
set -euo pipefail
TARGET="${1:?usage: $0 <ssh-target> [--no-build]}"
cd "$(dirname "$0")/.."
if [ "${2:-}" != "--no-build" ]; then
    (cd app && clickable build)
fi
VERSION=$(python3 -c 'import json; print(json.load(open("app/manifest.json"))["version"])')
CLICK="app/build/all/app/nfctoolkit.local_${VERSION}_all.click"
[ -f "$CLICK" ] || { echo "missing $CLICK; build first" >&2; exit 1; }
ssh "$TARGET" 'rm -rf ~/nfc-toolkit-install && mkdir -p ~/nfc-toolkit-install/backend'
scp -q install.sh "$CLICK" "$TARGET:nfc-toolkit-install/"
scp -q backend/nfctoolkit.py backend/nfc-toolkit.service "$TARGET:nfc-toolkit-install/backend/"
echo "Bundle for $VERSION copied. Install with:"
echo "  ssh -t $TARGET '~/nfc-toolkit-install/install.sh'"
