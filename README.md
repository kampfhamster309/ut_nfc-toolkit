# NFC Toolkit

Read, clone, write and emulate NFC tags on the Volla Phone Plinius Plus (Ubuntu Touch), with Flipper Zero `.nfc` file compatibility.

Two parts, same pattern as Agent Hub / BT Finder:

| Part | What it does |
|---|---|
| `backend/nfctoolkit.py` | systemd **user** service `nfc-toolkit` (Python + `dbus`/`gi`). Bridges `nfcd` (system D-Bus) to a token WebSocket on `127.0.0.1:8767`. |
| `app/` | Confined QML click `nfctoolkit.local`. Policy groups: `networking`, `content_exchange`, `keep-display-on`. |

## Prerequisite: NFC must work

The Plinius's NFC is dead out of the box — it needs the **patched `libncicore`** (see the vault note *Hardware - NFC* / `deploy_nfcfix.sh`). The backend warns at install time if `nfcd` isn't using it. Check: `cat /proc/$(pgrep -x nfcd)/maps | grep libncicore` must point at `/userdata/nfc-fix`.

## Build and install

```
scripts/deploy.sh phablet@<phone>                        # builds the click, copies the bundle
ssh -t phablet@<phone> '~/nfc-toolkit-install/install.sh'            # backend + app (sudo for the click)
ssh phablet@<phone> '~/nfc-toolkit-install/install.sh --backend-only'  # backend only, no sudo
```

## Features

- **Read:** hold a tag to the upper-middle back. Shows technology, protocol, type, UID, SAK, interfaces, NDEF records, and (for Type 2) a full page dump.
- **Save (.nfc):** writes a Flipper-compatible file to `~/Documents/NFC/`. NTAG/Ultralight dumps are fully interoperable with a Flipper Zero.
- **Clone:** read an NTAG/Ultralight, tap **Copy**, hold a blank tag of the same model, tap **Clone**. Writes pages 4..end (UID/lock pages are left alone). Also clone straight from a saved `.nfc` in the **Library**.
- **Write NDEF:** write a URL or text record to a Type 2 tag.
- **Emulate (HCE):** present a Type 4 NDEF tag (URL/text) to another reader. The UID is random — the hardware can't emulate a chosen UID.
- **APDU console:** send raw ISO-DEP APDUs to a Type 4 card and see the response, with SELECT presets. Uses `Tag.Transceive`.

## Limits (hardware / stack)

- **No MIFARE Classic cloning** — `nfcd` exposes no Crypto1/sector auth.
- **No 125 kHz LF** (EM4100/HID/Indala) — the phone has no LF radio (the Flipper does).
- **No UID impersonation** in emulation.
- **NFC-A and NFC-B only** — no FeliCa, no ISO15693.

## Protocol (WebSocket, JSON)

| Direction | Messages |
|---|---|
| app → backend | `hello{token,visible}`, `visible{v}`, `read`, `save`, `copy`, `clone{file?}`, `write_ndef{kind,value}`, `emulate{kind,value}`, `stop_emulate`, `apdu{hex}`, `library` |
| backend → app | `state{adapter,powered,emulating,error}`, `tag{...}`, `tag_gone`, `saved{name,path}`, `copied{pages,name}`, `cloned{pages}`, `ndef_written{bytes}`, `apdu_resp{hex}`, `library{list}`, `error{msg}` |

`kind` is `uri` or `text`.

## Development notes

- **Backend test (stdlib client):** `python3 ~/.local/share/nfc-toolkit/… ` — see `scripts`/the session's `nfc_test_client.py`. Backend logs: `journalctl --user -u nfc-toolkit -f`.
- **Restarting the backend over SSH:** never `pkill -f nfctoolkit` — the pattern matches your own ssh command and kills the session. Use `systemctl --user restart nfc-toolkit` or `fuser -k 8767/tcp`.
- **Headless QML:** `qmlscene` offscreen loads the app ("LOADED OK") but **segfaults on paint** (Lomiri `Button` in the software renderer) — a headless-only artifact; the app runs on-device. For a screenshot, drop local `Button.qml`/`Icon.qml` stubs in the qml dir.
- **nfcd API:** interfaces `Tag`/`TagType2`/`IsoDep`/`NDEF`/`Host` on `org.sailfishos.nfc.daemon` `/nfc0`; `Daemon.RequestMode` (reader `0x02`, CE `0x08`) is scoped to the caller's connection, so the long-lived service holds it. Emulation registers a `LocalHostService`; nfcd calls back `Process(host,CLA..Le)`.

## License

[MIT](LICENSE) © 2026 Felix Harenbrock. Free to use, modify and redistribute, including commercially, as long as the copyright notice and license text are kept.
