#!/usr/bin/env python3
"""NFC Toolkit backend: nfcd bridge for the NFC Toolkit app.

Runs as a systemd user service (phablet). Talks to nfcd over the system D-Bus and
serves a token WebSocket on 127.0.0.1 for the confined QML app.

Needs the patched libncicore (see vault 'Hardware - NFC') or nfcd never powers the field.

Capabilities exposed: read any A/B tag, clone Type 2 (NTAG/Ultralight), save/load Flipper
.nfc files, write NDEF to Type 2, emulate a Type 4 NDEF tag (HCE), and a raw APDU console.
"""
import base64
import binascii
import hashlib
import json
import os
import secrets
import socket
import struct
import time

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

HOST, PORT = "127.0.0.1", int(os.environ.get("NFCTOOLKIT_PORT", "8767"))
APP_DIR = os.path.expanduser("~/.local/share/nfctoolkit.local")
TOKEN_FILE = os.path.join(APP_DIR, "token")
LIBRARY_DIR = os.path.expanduser("~/Documents/NFC")

BLUEZ = None  # (unused; kept layout parallel to bt-finder)
NFCD = "org.sailfishos.nfc.daemon"
DAEMON_IF = "org.sailfishos.nfc.Daemon"
ADAPTER_IF = "org.sailfishos.nfc.Adapter"
TAG_IF = "org.sailfishos.nfc.Tag"
T2_IF = "org.sailfishos.nfc.TagType2"
ISODEP_IF = "org.sailfishos.nfc.IsoDep"
NDEF_IF = "org.sailfishos.nfc.NDEF"
LHS_IF = "org.sailfishos.nfc.LocalHostService"
OM_IF = "org.freedesktop.DBus.ObjectManager"

# NFC enums (nfcd nfc_types.h)
MODE_READER = 0x02
MODE_CE = 0x08
TECH = {0: "unknown", 1: "A", 2: "B", 4: "F"}
PROTO = {0: "unknown", 1: "Type 1", 2: "Type 2", 4: "Type 3",
         8: "ISO-DEP (Type 4A)", 16: "ISO-DEP (Type 4B)", 32: "NFC-DEP"}
TAGTYPE = {0: "unknown", 1: "FeliCa", 2: "MIFARE Classic", 4: "MIFARE Ultralight / NTAG"}

HCE_PATH = "/nfctoolkit/ce"
POLL_MS = 400


def log(*a):
    print(*a, flush=True)


def hexstr(b, sep=" "):
    return sep.join("%02X" % x for x in bytearray(b))


def ay(data):
    return dbus.Array([dbus.Byte(x) for x in bytearray(data)], signature="y")


# --------------------------------------------------------------------- NTAG/Ultralight ID

def ntag_model(data_size, version):
    """Best-effort model name from total byte size and GET_VERSION (8 bytes)."""
    if version and len(version) >= 7:
        prod, storage = version[2], version[6]
        table = {0x0F: "NTAG213", 0x11: "NTAG215", 0x13: "NTAG216"}
        if version[1] == 0x04 and storage in table:
            return table[storage]
        if version[1] == 0x03:  # MIFARE Ultralight family
            return {0x0B: "Ultralight EV1 (MF0UL11)", 0x0E: "Ultralight EV1 (MF0UL21)"}.get(storage, "Ultralight EV1")
    by_size = {45 * 4: "NTAG213", 135 * 4: "NTAG215", 231 * 4: "NTAG216", 16 * 4: "Ultralight", 20 * 4: "Ultralight C"}
    return by_size.get(data_size, "Ultralight / NTAG")


# --------------------------------------------------------------------- Flipper .nfc

def to_flipper_nfc(tag):
    """Serialise a read tag dict to a Flipper-compatible .nfc file (text)."""
    L = ["Filetype: Flipper NFC device", "Version: 4"]
    uid = tag.get("uid", "")
    atqa = tag.get("atqa")      # 2 bytes, Flipper stores little-endian pair as given
    sak = tag.get("sak")
    if tag.get("t2"):
        L += ["Device type: NTAG/Ultralight",
              "# UID is common for all formats",
              "UID: " + uid,
              "ATQA: " + (atqa or "00 44"),
              "SAK: %02X" % (sak if sak is not None else 0),
              "# Data format version",
              "Data format version: 2",
              "NTAG/Ultralight type: " + tag.get("model", "Unknown"),
              "Signature: " + (tag.get("signature") or "00 " * 32).strip(),
              "Mifare version: " + (tag.get("version_bytes") or "00 00 00 00 00 00 00 00"),
              "Counter 0: 0", "Tearing 0: 00",
              "Counter 1: 0", "Tearing 1: 00",
              "Counter 2: 0", "Tearing 2: 00"]
        pages = tag["t2"]["pages"]
        L += ["Pages total: %d" % len(pages), "Pages read: %d" % len(pages)]
        for i, p in enumerate(pages):
            L.append("Page %d: %s" % (i, hexstr(p)))
    else:
        # ISO14443-4 (Type 4) — UID/ATQA/SAK/ATS only; contents are app-specific
        is_b = "B" in (tag.get("tech") or "")
        L += ["Device type: ISO14443-4%s" % ("B" if is_b else "A"),
              "UID: " + uid,
              "ATQA: " + (atqa or "00 00"),
              "SAK: %02X" % (sak if sak is not None else 0)]
        if tag.get("ats"):
            L.append("ATS: " + tag["ats"])
    return "\n".join(L) + "\n"


def parse_flipper_nfc(text):
    """Parse a Flipper .nfc file into a tag dict usable for clone/emulate."""
    d, pages = {}, {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if k.startswith("Page "):
            pages[int(k[5:])] = bytes(int(x, 16) for x in v.split())
        else:
            d[k] = v
    out = {"device_type": d.get("Device type", ""), "uid": d.get("UID", ""),
           "model": d.get("NTAG/Ultralight type", ""), "atqa": d.get("ATQA"),
           "sak": int(d.get("SAK", "0"), 16) if d.get("SAK") else None}
    if pages:
        out["pages"] = [pages[i] for i in sorted(pages)]
    if "NDEF" in d.get("Device type", "") or "Ultralight" in d.get("Device type", ""):
        out["t2"] = True
    return out


# --------------------------------------------------------------------- NDEF build

def ndef_record(tnf, rtype, payload, rid=b""):
    # single short record: MB | ME | SR | (IL) | TNF
    flags = 0xC0 | 0x10 | (0x08 if rid else 0) | tnf
    rec = bytes([flags, len(rtype), len(payload) & 0xFF])
    if rid:
        rec += bytes([len(rid)])
    rec += rtype + rid + payload
    return rec


def ndef_uri(uri):
    prefixes = ["", "http://www.", "https://www.", "http://", "https://", "tel:", "mailto:"]
    code, rest = 0, uri
    for i, p in enumerate(prefixes[1:], 1):
        if uri.startswith(p):
            code, rest = i, uri[len(p):]
            break
    return ndef_record(0x01, b"U", bytes([code]) + rest.encode())


def ndef_text(text, lang="en"):
    payload = bytes([len(lang)]) + lang.encode() + text.encode()
    return ndef_record(0x01, b"T", payload)


def ndef_message(kind, value):
    if kind == "uri":
        return ndef_uri(value)
    if kind == "text":
        return ndef_text(value)
    raise ValueError("unknown NDEF kind")


def t2_tlv_wrap(ndef_msg):
    """Wrap an NDEF message in a Type 2 TLV block (0x03 len ... 0xFE)."""
    n = len(ndef_msg)
    if n < 255:
        tlv = bytes([0x03, n]) + ndef_msg + bytes([0xFE])
    else:
        tlv = bytes([0x03, 0xFF, n >> 8, n & 0xFF]) + ndef_msg + bytes([0xFE])
    # pad to 4-byte page boundary
    if len(tlv) % 4:
        tlv += b"\x00" * (4 - len(tlv) % 4)
    return tlv


# --------------------------------------------------------------------- Type 4 emulation

class NdefHostService(dbus.service.Object):
    """Emulate a Type 4 NDEF tag. nfcd calls Process() with each reader APDU."""
    SELECT_APP = bytes.fromhex("00A4040007D276000085010100")
    AID = bytes.fromhex("D2760000850101")

    def __init__(self, bus, path, get_ndef):
        super().__init__(bus, path)
        self.get_ndef = get_ndef          # callable -> ndef message bytes
        self.selected = None               # 'cc' | 'ndef' | None

    def _cc_file(self):
        # 15-byte Capability Container: CCLEN, mapping 2.0, MLe, MLc,
        # NDEF-file control TLV (file E104, max size, read free, write denied)
        mx = 0x0400
        return bytes([0x00, 0x0F, 0x20, 0x00, 0xFB, 0x00, 0xFB,
                      0x04, 0x06, 0xE1, 0x04, mx >> 8, mx & 0xFF, 0x00, 0xFF])

    def _ndef_file(self):
        msg = self.get_ndef() or b""
        return bytes([len(msg) >> 8, len(msg) & 0xFF]) + msg

    @dbus.service.method(LHS_IF, in_signature="o")
    def Start(self, host):
        self.selected = None
        log("HCE start")

    @dbus.service.method(LHS_IF, in_signature="o")
    def Restart(self, host):
        self.selected = None

    @dbus.service.method(LHS_IF, in_signature="o")
    def Stop(self, path):
        self.selected = None
        log("HCE stop")

    @dbus.service.method(LHS_IF, in_signature="oyyyyayu", out_signature="ayyyu")
    def Process(self, host, cla, ins, p1, p2, data, le):
        apdu = bytes([cla, ins, p1, p2]) + bytes(bytearray(data))
        resp = b""
        sw1, sw2 = 0x6A, 0x00  # default: wrong params
        try:
            if ins == 0xA4:  # SELECT
                body = bytes(bytearray(data))
                if p1 == 0x04 and body[:len(self.AID)] == self.AID:
                    sw1, sw2 = 0x90, 0x00
                elif p1 == 0x00 and body == b"\xE1\x03":
                    self.selected = "cc"; sw1, sw2 = 0x90, 0x00
                elif p1 == 0x00 and body == b"\xE1\x04":
                    self.selected = "ndef"; sw1, sw2 = 0x90, 0x00
            elif ins == 0xB0:  # READ BINARY
                off = (p1 << 8) | p2
                src = self._cc_file() if self.selected == "cc" else (self._ndef_file() if self.selected == "ndef" else b"")
                nb = le or len(src) - off
                resp = src[off:off + nb]
                sw1, sw2 = 0x90, 0x00
        except Exception as e:
            log("HCE process error", e)
            sw1, sw2 = 0x6F, 0x00
        return (ay(resp), dbus.Byte(sw1), dbus.Byte(sw2), dbus.UInt32(0))


# --------------------------------------------------------------------- NFC core

class Nfc:
    def __init__(self, hub):
        self.hub = hub
        self.bus = dbus.SystemBus()
        self.adapter_path = None
        self.powered = False
        self.error = ""
        self.want_reader = False
        self.reader_id = None
        self.ce_id = None
        self.ce_service = None
        self.ce_ndef = None
        self.cur_tag = None          # object path of current tag
        self.cur_info = None         # last read info dict
        self.absent = 0
        self.clone_source = None     # remembered {pages, name} for copy->paste cloning
        self.busy = False
        self._load()
        self.bus.add_signal_receiver(self._owner_changed, "NameOwnerChanged",
                                     "org.freedesktop.DBus", arg0=NFCD)
        GLib.timeout_add(POLL_MS, self._poll)

    def _daemon(self):
        return dbus.Interface(self.bus.get_object(NFCD, "/"), DAEMON_IF)

    def _adapter(self, iface=ADAPTER_IF):
        return dbus.Interface(self.bus.get_object(NFCD, self.adapter_path), iface)

    def _load(self):
        try:
            adapters = self._daemon().GetAdapters()
        except dbus.DBusException as e:
            self.set_error("nfcd not reachable: %s" % e.get_dbus_name())
            self.adapter_path = None
            return
        self.adapter_path = str(adapters[0]) if adapters else None
        if not self.adapter_path:
            self.set_error("No NFC adapter")
        else:
            self.error = ""
            try:
                self.powered = bool(self._adapter().GetPowered())
            except dbus.DBusException:
                pass
        log("adapter", self.adapter_path)

    def _owner_changed(self, name, old, new):
        if name == NFCD:
            log("nfcd", "up" if new else "down")
            if new:
                GLib.timeout_add(1000, lambda: (self._load(), self.update_mode(), self.hub.push_state())[-1] and False)
            else:
                self.adapter_path = None
                self.set_error("nfcd restarting…")
                self.hub.push_state()

    def set_error(self, msg):
        if msg != self.error:
            self.error = msg
            log("error:", msg)

    # ---- mode arbitration
    def update_mode(self):
        if not self.adapter_path:
            return
        d = self._daemon()
        want = self.want_reader and self.ce_id is None
        if want and self.reader_id is None:
            try:
                self.reader_id = int(d.RequestMode(MODE_READER, 0))
                log("reader mode on")
            except dbus.DBusException as e:
                self.set_error("reader mode failed: %s" % e.get_dbus_message())
        elif not want and self.reader_id is not None:
            try:
                d.ReleaseMode(dbus.UInt32(self.reader_id))
            except dbus.DBusException:
                pass
            self.reader_id = None
            log("reader mode off")

    # ---- polling for tags
    def _poll(self):
        if not self.adapter_path or self.busy:
            return True
        try:
            powered = bool(self._adapter().GetPowered())
            if powered != self.powered:
                self.powered = powered
                self.hub.push_state()
            if self.ce_id is not None:
                return True
            tags = [str(t) for t in self._adapter().GetTags()]
        except dbus.DBusException:
            return True
        if tags:
            self.absent = 0
            if tags[0] != self.cur_tag:
                # nfcd rotates the tag object path on each re-poll even while the
                # physical tag stays put; only re-read/announce when the UID changes.
                prev_uid = self.cur_info.get("uid") if self.cur_info else None
                self.cur_tag = tags[0]
                info = self.read_current(silent=True)
                if info is not None and info.get("uid") != prev_uid:
                    self.hub.broadcast({"t": "tag", "tag": info})
        else:
            self.absent = getattr(self, "absent", 0) + 1
            if self.cur_tag is not None and self.absent >= 5:   # ~2 s of real absence
                self.cur_tag = None
                self.cur_info = None
                self.hub.broadcast({"t": "tag_gone"})
        return True

    # ---- reading
    def read_current(self, silent=False):
        if not self.cur_tag:
            return None
        self.busy = True
        try:
            info = self._read_tag(self.cur_tag)
            self.cur_info = info
            if not silent:
                self.hub.broadcast({"t": "tag", "tag": info})
            return info
        except dbus.DBusException as e:
            # the tag path often vanishes mid-read as nfcd re-polls; that's not a real error
            msg = e.get_dbus_message()
            if not silent and "does not exist" not in msg and "UnknownObject" not in e.get_dbus_name():
                self.hub.broadcast({"t": "read_error", "msg": msg})
            return None
        finally:
            self.busy = False

    def _read_tag(self, path):
        tag = dbus.Interface(self.bus.get_object(NFCD, path), TAG_IF)
        ver, present, tech, proto, typ, ifaces, ndef, pp = tag.GetAll3()
        ifaces = [str(i) for i in ifaces]
        info = {
            "tech": TECH.get(int(tech), str(int(tech))),
            "protocol": PROTO.get(int(proto), str(int(proto))),
            "type": TAGTYPE.get(int(typ), str(int(typ))),
            "interfaces": ifaces,
            "uid": "", "sak": None, "atqa": None,
            "ndef": [], "t2": None, "model": "", "cloneable": False,
        }
        for k, v in pp.items():
            if k in ("NFCID1", "NFCID0"):
                info["uid"] = hexstr(v)
            elif k == "SEL_RES":
                info["sak"] = int(v)
            elif k == "SENS_RES":
                info["atqa"] = hexstr(v)
        # NDEF records
        for nd in ndef:
            try:
                n = dbus.Interface(self.bus.get_object(NFCD, str(nd)), NDEF_IF)
                raw = bytes(bytearray(n.GetRawData()))
                v2, flags, tnf, nifaces, ntype, nid, payload = n.GetAll()
                info["ndef"].append({
                    "tnf": int(tnf), "type": bytes(bytearray(ntype)).decode("latin-1"),
                    "payload_hex": hexstr(payload, ""), "raw_hex": hexstr(raw, ""),
                    "text": self._decode_ndef(int(tnf), bytes(bytearray(ntype)), bytes(bytearray(payload))),
                })
            except dbus.DBusException:
                pass
        # Type 2 full dump
        if T2_IF in ifaces:
            t2 = dbus.Interface(self.bus.get_object(NFCD, path), T2_IF)
            block_size = int(t2.GetBlockSize())
            data_size = int(t2.GetDataSize())
            try:
                data = bytes(bytearray(t2.ReadAllData()))
            except dbus.DBusException:
                data = b""
            version = self._t2_version(tag)
            pages = [data[i:i + 4] for i in range(0, len(data), 4)]
            info["t2"] = {"block_size": block_size, "data_size": data_size,
                          "pages": pages, "n_pages": len(pages),
                          "dump_hex": hexstr(data, "")}
            info["version_bytes"] = hexstr(version) if version else None
            info["model"] = ntag_model(data_size, version)
            info["type"] = "MIFARE Ultralight / NTAG"
            info["cloneable"] = True
        # ATS for ISO-DEP
        if ISODEP_IF in ifaces:
            try:
                iso = dbus.Interface(self.bus.get_object(NFCD, path), ISODEP_IF)
                v2, params = iso.GetAll2()
                if "T0" in params or "ATS" in params:
                    pass
                info["ats"] = hexstr(params["ATS"]) if "ATS" in params else None
            except dbus.DBusException:
                pass
        return info

    def _t2_version(self, tag):
        """GET_VERSION (0x60) via raw transceive; None if unsupported."""
        try:
            tag.Acquire(False)
        except dbus.DBusException:
            return None
        try:
            resp = bytes(bytearray(tag.Transceive(ay(b"\x60"))))
            return resp if len(resp) >= 7 else None
        except dbus.DBusException:
            return None
        finally:
            try:
                tag.Release()
            except dbus.DBusException:
                pass

    def _decode_ndef(self, tnf, rtype, payload):
        try:
            if tnf == 0x01 and rtype == b"U":
                prefixes = ["", "http://www.", "https://www.", "http://", "https://",
                            "tel:", "mailto:", "ftp://anonymous:anonymous@", "ftp://ftp.",
                            "ftps://", "sftp://", "smb://", "nfs://", "ftp://", "dav://",
                            "news:", "telnet://", "imap:", "rtsp://", "urn:", "pop:",
                            "sip:", "sips:", "tftp:", "btspp://"]
                p = prefixes[payload[0]] if payload[0] < len(prefixes) else ""
                return p + payload[1:].decode("utf-8", "replace")
            if tnf == 0x01 and rtype == b"T":
                ln = payload[0] & 0x3F
                return payload[1 + ln:].decode("utf-8", "replace")
        except Exception:
            pass
        return None

    # ---- actions
    def clone_to_current(self, pages):
        """Write page data (list of 4-byte bytes) to the current Type 2 tag."""
        if not self.cur_tag or not self.cur_info or not self.cur_info.get("t2"):
            raise RuntimeError("No writable Type 2 tag present")
        t2 = dbus.Interface(self.bus.get_object(NFCD, self.cur_tag), T2_IF)
        target_pages = self.cur_info["t2"]["n_pages"]
        written = 0
        # Pages 0-3 are UID/lock/CC on NTAG — skip (read-only / locking). Start at 4.
        for i in range(4, min(len(pages), target_pages)):
            blk = pages[i]
            if len(blk) != 4:
                continue
            try:
                t2.Write(0, i, ay(blk))
                written += 1
            except dbus.DBusException as e:
                raise RuntimeError("Write failed at page %d: %s" % (i, e.get_dbus_message()))
        return written

    def write_ndef_current(self, kind, value):
        if not self.cur_tag or not self.cur_info or not self.cur_info.get("t2"):
            raise RuntimeError("Need a writable Type 2 tag")
        msg = ndef_message(kind, value)
        tlv = t2_tlv_wrap(msg)
        t2 = dbus.Interface(self.bus.get_object(NFCD, self.cur_tag), T2_IF)
        t2.WriteData(dbus.UInt32(16), ay(tlv))   # user memory starts at byte 16 (page 4)
        return len(tlv)

    def apdu(self, data):
        if not self.cur_tag:
            raise RuntimeError("No tag present")
        tag = dbus.Interface(self.bus.get_object(NFCD, self.cur_tag), TAG_IF)
        try:
            tag.Acquire(False)
        except dbus.DBusException:
            pass
        try:
            resp = bytes(bytearray(tag.Transceive(ay(data))))
            return resp
        finally:
            try:
                tag.Release()
            except dbus.DBusException:
                pass

    # ---- emulation
    def start_emulation(self, kind, value):
        if self.ce_id is not None:
            self.stop_emulation()
        self.ce_ndef = ndef_message(kind, value)
        if self.ce_service is None:
            self.ce_service = NdefHostService(self.bus, HCE_PATH, lambda: self.ce_ndef)
        d = self._daemon()
        d.RegisterLocalHostService(dbus.ObjectPath(HCE_PATH), "nfctoolkit-ndef")
        # release reader, request card emulation
        self.update_mode()  # drops reader since ce pending below
        self.ce_id = int(d.RequestMode(MODE_CE, MODE_READER))
        if self.reader_id is not None:
            try:
                d.ReleaseMode(dbus.UInt32(self.reader_id))
            except dbus.DBusException:
                pass
            self.reader_id = None
        log("emulation on")

    def stop_emulation(self):
        if self.ce_id is None:
            return
        d = self._daemon()
        try:
            d.ReleaseMode(dbus.UInt32(self.ce_id))
        except dbus.DBusException:
            pass
        self.ce_id = None
        try:
            d.UnregisterLocalHostService(dbus.ObjectPath(HCE_PATH))
        except dbus.DBusException:
            pass
        log("emulation off")
        self.update_mode()

    def copy_current(self):
        if not self.cur_info or not self.cur_info.get("t2"):
            raise RuntimeError("Copy needs a Type 2 tag (NTAG/Ultralight)")
        self.clone_source = {"pages": self.cur_info["t2"]["pages"],
                             "name": self.cur_info.get("model") or "tag",
                             "uid": self.cur_info.get("uid")}
        return len(self.clone_source["pages"])

    def save_library(self, info):
        os.makedirs(LIBRARY_DIR, exist_ok=True)
        name = self.hub._suggest_name(info)
        path = os.path.join(LIBRARY_DIR, name)
        with open(path, "w") as f:
            f.write(to_flipper_nfc(info))
        return {"name": name, "path": path}

    def list_library(self):
        out = []
        try:
            for fn in sorted(os.listdir(LIBRARY_DIR)):
                if fn.endswith(".nfc"):
                    try:
                        with open(os.path.join(LIBRARY_DIR, fn)) as f:
                            d = parse_flipper_nfc(f.read())
                        out.append({"name": fn, "uid": d.get("uid"), "model": d.get("model"),
                                    "device_type": d.get("device_type"),
                                    "pages": len(d.get("pages", []))})
                    except Exception:
                        out.append({"name": fn})
        except OSError:
            pass
        return out

    def read_library(self, name):
        with open(os.path.join(LIBRARY_DIR, os.path.basename(name))) as f:
            return parse_flipper_nfc(f.read())

    def state(self):
        return {"t": "state", "adapter": self.adapter_path is not None,
                "powered": self.powered, "emulating": self.ce_id is not None,
                "error": self.error}


# --------------------------------------------------------------------- websocket (from bt-finder)

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class Client:
    def __init__(self, hub, sock):
        self.hub, self.sock = hub, sock
        self.rbuf = b""
        self.wbuf = b""
        self.open = False
        self.authed = False
        self.visible = True
        self.last_rx = time.monotonic()
        self.closed = False
        sock.setblocking(False)
        self.rwatch = GLib.io_add_watch(sock.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._on_read)
        self.wwatch = None

    def _on_read(self, fd, cond):
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            self.close(); return False
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            self.close(); return False
        if not data:
            self.close(); return False
        self.last_rx = time.monotonic()
        self.rbuf += data
        if len(self.rbuf) > (4 << 20):
            self.close(); return False
        if not self.open:
            if b"\r\n\r\n" not in self.rbuf:
                return True
            head, self.rbuf = self.rbuf.split(b"\r\n\r\n", 1)
            if not self._handshake(head):
                self.close(); return False
        while not self.closed:
            fr = self._parse_frame()
            if fr is None:
                break
            op, payload = fr
            if op == 0x1:
                try:
                    self.hub.on_message(self, json.loads(payload.decode("utf-8")))
                except ValueError:
                    pass
            elif op == 0x8:
                self._send_frame(0x8, payload[:2]); self.close()
            elif op == 0x9:
                self._send_frame(0xA, payload)
        return not self.closed

    def _handshake(self, head):
        lines = head.decode("latin-1").split("\r\n")
        if not lines[0].startswith("GET "):
            return False
        hdr = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                hdr[k.strip().lower()] = v.strip()
        key = hdr.get("sec-websocket-key")
        if not key or "websocket" not in hdr.get("upgrade", "").lower():
            return False
        acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        self._write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                     "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % acc).encode())
        self.open = True
        return True

    def _parse_frame(self):
        b = self.rbuf
        if len(b) < 2:
            return None
        op = b[0] & 0x0F
        masked = b[1] & 0x80
        ln = b[1] & 0x7F
        i = 2
        if ln == 126:
            if len(b) < 4:
                return None
            ln = struct.unpack(">H", b[2:4])[0]; i = 4
        elif ln == 127:
            if len(b) < 10:
                return None
            ln = struct.unpack(">Q", b[2:10])[0]; i = 10
        if masked:
            if len(b) < i + 4:
                return None
            mask = b[i:i + 4]; i += 4
        if len(b) < i + ln:
            return None
        payload = b[i:i + ln]
        if masked:
            payload = bytes(c ^ mask[j % 4] for j, c in enumerate(payload))
        self.rbuf = b[i + ln:]
        return op, payload

    def _send_frame(self, op, payload):
        n = len(payload)
        if n < 126:
            h = struct.pack(">BB", 0x80 | op, n)
        elif n < 65536:
            h = struct.pack(">BBH", 0x80 | op, 126, n)
        else:
            h = struct.pack(">BBQ", 0x80 | op, 127, n)
        self._write(h + payload)

    def send(self, obj):
        if self.open and self.authed and not self.closed:
            self._send_frame(0x1, json.dumps(obj, separators=(",", ":")).encode())

    def ping(self):
        if self.open and not self.closed:
            self._send_frame(0x9, b"hb")

    def _write(self, data):
        if self.closed:
            return
        self.wbuf += data
        if len(self.wbuf) > (8 << 20):
            self.close(); return
        self._flush()

    def _flush(self, *watch_args):
        from_watch = bool(watch_args)
        try:
            while self.wbuf:
                n = self.sock.send(self.wbuf)
                self.wbuf = self.wbuf[n:]
        except BlockingIOError:
            pass
        except OSError:
            self.close(); return False
        if self.wbuf:
            if self.wwatch is None:
                self.wwatch = GLib.io_add_watch(self.sock.fileno(), GLib.IO_OUT, self._flush)
            return True
        if self.wwatch is not None:
            if not from_watch:
                GLib.source_remove(self.wwatch)
            self.wwatch = None
        return False

    def close(self):
        if self.closed:
            return
        self.closed = True
        for w in (self.rwatch, self.wwatch):
            if w is not None:
                try:
                    GLib.source_remove(w)
                except Exception:
                    pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.hub.on_close(self)


class Hub:
    def __init__(self, token):
        self.token = token
        self.clients = []
        self.nfc = Nfc(self)

    def listen(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, PORT))
        s.listen(4)
        s.setblocking(False)
        self.srv = s
        GLib.io_add_watch(s.fileno(), GLib.IO_IN, self._accept)
        GLib.timeout_add_seconds(15, self._heartbeat)
        log("listening on ws://%s:%d" % (HOST, PORT))

    def _accept(self, fd, cond):
        try:
            sock, _ = self.srv.accept()
        except OSError:
            return True
        self.clients.append(Client(self, sock))
        return True

    def _heartbeat(self):
        now = time.monotonic()
        for c in list(self.clients):
            if now - c.last_rx > 45:
                c.close()
            else:
                c.ping()
        return True

    def on_close(self, c):
        if c in self.clients:
            self.clients.remove(c)
        self.update_scan()

    def broadcast(self, obj):
        for c in self.clients:
            c.send(obj)

    def push_state(self, only=None):
        st = self.nfc.state()
        for c in ([only] if only else self.clients):
            c.send(st)

    def update_scan(self):
        self.nfc.want_reader = any(c.authed and c.visible for c in self.clients)
        self.nfc.update_mode()

    def on_message(self, c, m):
        t = m.get("t")
        if not c.authed:
            if t == "hello" and secrets.compare_digest(str(m.get("token", "")), self.token):
                c.authed = True
                c.visible = bool(m.get("visible", True))
                self.push_state(c)
                if self.nfc.cur_info:
                    c.send({"t": "tag", "tag": self.nfc.cur_info})
                self.update_scan()
            else:
                c.close()
            return
        try:
            self._handle(c, m, t)
        except Exception as e:
            c.send({"t": "error", "msg": str(e)})

    def _handle(self, c, m, t):
        n = self.nfc
        if t == "visible":
            c.visible = bool(m.get("v"))
            self.update_scan()
        elif t == "read":
            info = n.read_current()
            if info is None:
                c.send({"t": "error", "msg": "No tag present"})
        elif t == "save":
            if not n.cur_info:
                c.send({"t": "error", "msg": "No tag to save"}); return
            ent = n.save_library(n.cur_info)
            self.broadcast({"t": "saved", "name": ent["name"], "path": ent["path"]})
        elif t == "copy":
            cnt = n.copy_current()
            c.send({"t": "copied", "pages": cnt, "name": n.clone_source["name"]})
        elif t == "library":
            c.send({"t": "library", "list": n.list_library()})
        elif t == "clone":
            if m.get("file"):
                src = n.read_library(m["file"])
                pages = src.get("pages")
            elif n.clone_source:
                pages = n.clone_source["pages"]
            else:
                pages = None
            if not pages:
                c.send({"t": "error", "msg": "No Type 2 source copied. Read a tag and tap Copy first."}); return
            w = n.clone_to_current(pages)
            c.send({"t": "cloned", "pages": w})
            n.read_current()
        elif t == "write_ndef":
            ln = n.write_ndef_current(m["kind"], m["value"])
            c.send({"t": "ndef_written", "bytes": ln})
            n.read_current()
        elif t == "emulate":
            n.start_emulation(m["kind"], m["value"])
            self.push_state()
        elif t == "stop_emulate":
            n.stop_emulation()
            self.push_state()
            self.update_scan()
        elif t == "apdu":
            data = bytes.fromhex(m["hex"].replace(" ", ""))
            resp = n.apdu(data)
            c.send({"t": "apdu_resp", "hex": hexstr(resp, "")})
        elif t == "load":
            parsed = parse_flipper_nfc(m["content"])
            c.send({"t": "loaded", "info": {"uid": parsed.get("uid"), "model": parsed.get("model"),
                    "device_type": parsed.get("device_type"),
                    "pages": len(parsed.get("pages", []))}})

    def _suggest_name(self, info):
        uid = (info.get("uid") or "tag").replace(" ", "")
        base = info.get("model") or info.get("type") or "tag"
        base = base.split("(")[0].strip().replace("/", "-").replace(" ", "_")
        return "%s_%s.nfc" % (base, uid)


def load_token():
    os.makedirs(APP_DIR, exist_ok=True)
    try:
        with open(TOKEN_FILE) as f:
            t = f.read().strip()
            if len(t) >= 32:
                return t
    except OSError:
        pass
    t = secrets.token_hex(24)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t + "\n")
    return t


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    hub = Hub(load_token())
    hub.listen()
    loop = GLib.MainLoop()

    def stop(*a):
        try:
            hub.nfc.stop_emulation()
            if hub.nfc.reader_id is not None:
                hub.nfc._daemon().ReleaseMode(dbus.UInt32(hub.nfc.reader_id))
        except Exception:
            pass
        GLib.timeout_add(200, loop.quit)
        return False

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, stop)
    loop.run()


if __name__ == "__main__":
    main()
