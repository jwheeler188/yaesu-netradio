#!/usr/bin/env python3
"""W5PL FT-897D network radio - bridge (runs on the Raspberry Pi the Digirig is plugged into). Also for the FT-891 (--radio ft891).

Shares a Yaesu FT-897D + Digirig on the network so a laptop anywhere in the house can run WSJT-X, VARA, Pat etc.:
  CAT + PTT  TCP 4533  Hamlib rigctld protocol: apps use "Hamlib NET rigctl", server PI-ADDRESS:4533
  audio      TCP 4534  ft897-audio.py on the laptop plays receive audio into BlackHole / VB-CABLE and sends the
                       app's transmit audio back (48 kHz, 16-bit, mono, uncompressed: ~0.8 Mbit/s each way)

The Digirig is two USB devices: a CP2102 serial port (CAT, PTT on RTS) and a CM108 sound card. rigctld (Hamlib)
runs here as a child process on 127.0.0.1; the CAT port is a proxy in front of it that watches PTT commands.

Safety: the bridge un-keys the radio itself if, while it transmits,
  - the laptop's audio link drops or its transmit audio stops for --stall seconds (Wi-Fi drop, laptop asleep),
  - the app that keyed it disconnects (crash, Wi-Fi drop), or
  - it has been keyed for --tx-timeout seconds.
Transmit audio is only ever passed to the radio while it is keyed by CAT; the laptop sends none otherwise.
One laptop at a time owns the audio link: a second one is refused while the first is alive.

Web panel on TCP 8080 (panel.html): frequency, mode, VFO, split, RIT, FM repeater settings, PTT and meters (S, power,
ALC, SWR) from any browser. "Take control" gives one computer the radio: every other CAT and audio connection is closed
and new ones from other computers are refused until that computer releases it (or has not been heard from for a while).
Whoever is using the radio is asked first. The administration page (/admin) sets the passwords, the bands and ranges
where transmitting is locked, and the high-SWR shutdown (ft897_access.py).
"""
import argparse, array, collections, datetime, shutil, fcntl, json, os, queue, re, signal, socket, struct, subprocess, sys, threading, time, glob
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie, CookieError
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ft897_access as acc
from ft897_lsb import Invert, PIVOT
import ft897_cw as cwm
import ft897_aprs as aprs
import ft897_maps as maps
from ft897_tap import TickClean
import ft897_radio as radios
from ft897_radio import parse_raw
cfg = acc.cfg

VERSION = "2026.11.27"                     # bridge build (logs, client checks)
RELEASE = "1.0.0-beta.4"                    # yaesu-netradio release, shown on the panel
p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.ArgumentDefaultsHelpFormatter)
p.add_argument("--cat-port", type=int, default=4533, help="TCP port for CAT (Hamlib NET rigctl)")
p.add_argument("--audio-port", type=int, default=4534, help="TCP port for ft897-audio.py")
p.add_argument("--web-port", type=int, default=8080, help="TCP port for the web control panel (0 = off)")
p.add_argument("--bind", default="0.0.0.0", help="address to listen on (0.0.0.0 = all networks)")
p.add_argument("--radio", default="", choices=["", *radios.RADIOS], help="which radio (default: ft897d, or the one --model belongs to)")
p.add_argument("--serial", default="", help="CAT serial device (default: FT-897D the Digirig's CP2102, 10c4:ea60; FT-891 the radio's own "
               "USB, CP2105 10c4:ea70, first port)")
p.add_argument("--baud", type=int, default=0, help="CAT baud rate: must match the radio's CAT RATE menu (0 = FT-897D 38400; FT-891: found by asking)")
p.add_argument("--ptt", default="", choices=["", "RTS", "DTR", "CAT"], help="how to key the radio (default: FT-897D with Digirig RTS, FT-891 CAT)")
p.add_argument("--ptt-serial", default="", help="serial device for RTS / DTR keying, if it is not the CAT device")
p.add_argument("--model", type=int, default=0, help="Hamlib rig model, only if it is not the usual one (1023 = FT-897, 1022 = FT-857)")
p.add_argument("--rigctld", default="rigctld", help="rigctld program")
p.add_argument("--rigctld-port", type=int, default=14533, help="internal rigctld port (127.0.0.1 only)")
p.add_argument("--card", default="", help="ALSA device for the Digirig's sound card (default: first USB sound card, "
               "as plughw:CARD=...,DEV=0); 'null' for a test without a radio")
p.add_argument("--rate", type=int, default=48000, help="audio sample rate")
p.add_argument("--stall", type=float, default=1.5, help="un-key if TX audio from the laptop stops for this long (s)")
p.add_argument("--no-audio-check", action="store_true", help="allow keying with no laptop audio link (e.g. CW keyer "
               "apps); the disconnect and --tx-timeout checks still apply")
p.add_argument("--xara", default="~/.local/bin/xara", help="xara, the VARA-compatible modem for Winlink on the Pi")
p.add_argument("--pat", default="", help="Pat, the Winlink mail program (default: pat, or pat-winlink as Ubuntu's own package names it)")
p.add_argument("--pat-port", type=int, default=8092, help="TCP port for Pat's mailbox web page while Winlink runs")
p.add_argument("--dummy", action="store_true", help="test mode: Hamlib dummy rig, ALSA null audio, no radio needed")
p.add_argument("-v", "--verbose", action="store_true", help="log PTT, frequency changes and connections")
a = p.parse_args()
TX_RAW = os.environ.get("FT897_TX_RAW", "") if a.dummy else ""        # test mode only: a file that gets the transmit audio
FAKE = os.environ.get("FT897_FAKE_METERS", "") if a.dummy else ""      # test mode only: a file with {"freq", "split", "swr", "hi_swr"}
RADIO = radios.pick(a.radio, a.model)
if not a.model: a.model = RADIO.model
if not a.ptt: a.ptt = RADIO.ptt
if a.dummy: a.model = 1; a.card = a.card or "null"
if RADIO.swr_level: acc.DEFAULT["swr"]["level"] = cfg["swr"]["level"] = RADIO.swr_level
acc.DEFAULT["bands"][:] = [b for b in acc.DEFAULT["bands"] if b["lo"] < RADIO.max_hz]
cfg["bands"] = [b for b in cfg["bands"] if b["lo"] < RADIO.max_hz]
if a.ptt == "CAT": a.ptt = "RIG"                # rigctld's word for it

FRAME_MS = 20
FRAME = a.rate * 2 * FRAME_MS // 1000          # bytes of S16LE mono per 20 ms
PREBUF = FRAME * 8      # TX jitter buffer: start playing at 160 ms queued (Wi-Fi jitter); the client may ask for more
TXMAX_EXTRA = a.rate * 2 * 250 // 1000          # drop TX audio queued beyond the buffer + 250 ms (latency cap)

_log_lock = threading.Lock()
def log(msg):
    with _log_lock: print(f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M:%S} {msg}", flush=True)

lock = threading.RLock()
st = {"keyed": False, "keyed_by": None, "key_ts": 0.0, "audio": None, "audio_addr": None, "last_frame": 0.0,
      "last_audio": 0.0, "txq": bytearray(), "playing": False, "rig_ok": False, "freq": None,
      "tx_played": 0, "tx_sound": 0, "underruns": 0,
      "draining": False, "last_sound": 0.0, "tx_sq": 0, "tx_n": 0, "tx_peak": 0, "tx_meter": [], "prebuf": PREBUF,
      "smeter_live": not RADIO.ticks, "owner": None, "owner_name": None, "owner_seen": 0.0, "cat_clients": {}, "audio_ip": None, "audio_since": 0.0,
      "req": None, "answers": {}, "swr_n": 0, "tx_hz": 0, "vfo_freq": {}, "meter_ok": True, "web_ptt_seen": 0.0, "panel_seen": 0.0, "panels": {}, "rig": {}, "rit": 0,
      "lsb": False, "lsb_at": 0.0, "cw": 0, "cw_at": 0.0, "cat_fail": 0, "cat_dead": False, "alive_at": 0.0, "stuck": None, "unkeys": 0,
      "rpt": {"shift": None, "offset": None, "tone": None, "mode": None, "dcs": None, "hz": 0}, "last_tx": 0, "unkey_at": 0.0}      # last set over CAT: the radio can't report it                     # software LSB on: the radio is in DIG, its dial PIVOT Hz below the LSB carrier

def dbfs(v):
    import math
    return f"{20 * math.log10(v / 32768):.0f} dBFS" if v >= 1 else "silent"

def ip_of(addr):
    ip = addr[0]
    return ip[7:] if ip.startswith("::ffff:") else ip

def allowed(ip):
    """While someone has taken control, only their computer (and the Pi itself) may use the radio."""
    with lock: o = st["owner"]
    return ip == "127.0.0.1" or (ip == o if o is not None else not cfg["need_control"])

def why_not(): return f"radio controlled by {st['owner_name']}" if st["owner"] else "take control first"

def seen(ip):
    with lock:
        if st["owner"] == ip: st["owner_seen"] = time.time()

_names = {}
def host_name(ip):
    """Reverse-DNS name for the panel's connection list (looked up once, in the background)."""
    if ip not in _names:
        _names[ip] = ip
        def look():
            try: _names[ip] = socket.gethostbyaddr(ip)[0].split(".")[0]
            except OSError: pass
        threading.Thread(target=look, daemon=True).start()
    return _names[ip]

# ---------------------------------------------------------------- frames (audio link)
# type (1 byte) + length (2 bytes, big endian) + payload
#   bridge -> laptop: H hello (JSON)  A RX audio  K keyed state b"1"/b"0"  B busy (text, then close)
#   laptop -> bridge: H hello (JSON)  A TX audio (only while keyed)  P heartbeat  k "K received, RX flushed"
def send_frame(sock, t, payload=b""):
    sock.sendall(t + struct.pack(">H", len(payload)) + payload)

def recv_exact(sock, n):
    b = bytearray()
    while len(b) < n:
        d = sock.recv(n - len(b))
        if not d: raise ConnectionError("closed")
        b += d
    return bytes(b)

def recv_frame(sock):
    h = recv_exact(sock, 3)
    n = struct.unpack(">H", h[1:])[0]
    return h[:1], recv_exact(sock, n) if n else b""

audio_send_lock = threading.Lock()
def audio_send(t, payload=b""):
    with lock: c = st["audio"]
    if c is None: return
    try:
        with audio_send_lock: send_frame(c, t, payload)
    except OSError as e:
        drop_audio(c, f"send failed: {e}")

# ---------------------------------------------------------------- devices
def find_serial():
    if a.serial: return a.serial
    if a.dummy: return None
    if RADIO.key == "ft891":                       # the radio's own USB has two ports: the first (Enhanced) is CAT
        for d in sorted(glob.glob("/dev/serial/by-id/*CP2105*")):
            if "-if00" in d: return os.path.realpath(d)
        try:
            import serial.tools.list_ports
            for port in serial.tools.list_ports.comports():
                if port.vid == 0x10C4 and port.pid == 0xEA70 and "Enhanced" in (port.interface or "Enhanced"): return port.device
        except ImportError: pass
        return None
    for d in sorted(glob.glob("/dev/serial/by-id/*")):
        if "CP2105" not in d and ("CP210" in d or "Digirig" in d or "digirig" in d): return os.path.realpath(d)
    try:
        import serial.tools.list_ports
        for port in serial.tools.list_ports.comports():
            if port.vid == 0x10C4 and port.pid == 0xEA60: return port.device
    except ImportError: pass
    return None

def find_card():
    if a.card: return a.card
    for d in sorted(glob.glob("/proc/asound/card[0-9]*")):
        if os.path.exists(d + "/usbid"):
            with open(d + "/id") as f: return f"plughw:CARD={f.read().strip()},DEV=0"
    return None

def card_index(dev):
    """plughw:CARD=Device,DEV=0 -> ALSA card id for amixer"""
    return dev.split("CARD=")[1].split(",")[0] if "CARD=" in dev else None

# ---------------------------------------------------------------- rigctld (child process)
rig_proc = None
def rig_loop():
    global rig_proc
    warned = False
    while True:
        dev = find_serial()
        if dev is None and not a.dummy:
            if not warned: log(f"waiting for {RADIO.serial_hint} to appear..."); warned = True
            time.sleep(2); continue
        warned = False
        cmd = [a.rigctld, "-m", str(a.model), "-T", "127.0.0.1", "-t", str(a.rigctld_port)]
        if a.dummy: cmd += ["-P", "RIG"]
        else:
            baud = a.baud or RADIO.baud
            if not baud:                                        # FT-891: ask it at each speed
                baud = radios.find_baud(dev)
                if baud: log(f"{RADIO.name} found on {dev} at {baud} baud")
                else:
                    log(f"no {RADIO.name} answers on {dev}: is it on? (tried every CAT RATE)"); time.sleep(5); continue
            cmd += ["-r", dev, "-s", str(baud), "-P", a.ptt] + RADIO.rigctld_args
            if a.ptt != "RIG": cmd += ["-p", a.ptt_serial or dev]
        log("starting " + " ".join(cmd))
        try:
            rig_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError:
            log(f"{a.rigctld} not found: sudo apt install libhamlib-utils"); time.sleep(10); continue
        threading.Thread(target=rig_ready, daemon=True).start()
        for line in rig_proc.stdout:
            line = line.rstrip()
            if line: log("rigctld: " + line)
        rc = rig_proc.wait()
        with lock: st["rig_ok"] = False
        log(f"rigctld stopped (exit {rc}): restarting in 3 s")
        time.sleep(3)

def rig_cmd(line, timeout=3.0):
    """Send one command to rigctld on a connection of our own; returns the reply text."""
    s = socket.create_connection(("127.0.0.1", a.rigctld_port), timeout=timeout)
    try:
        s.sendall(line.encode() + b"\n")
        out = b""
        while not out.endswith(b"\n"):
            d = s.recv(4096)
            if not d: break
            out += d
        return out.decode(errors="replace").strip()
    finally: s.close()

def rig_ready():
    for _ in range(50):
        time.sleep(0.2)
        try:
            f = rig_cmd("f")
            with lock: st["rig_ok"] = True
            log(f"radio answering: {int(f) / 1e6:.4f} MHz" if f.isdigit() else f"rigctld up (f -> {f!r})")
            try:
                if not a.dummy and not st["keyed"] and a.ptt == "RIG":       # left transmitting by a crash? CAT keying stays on by itself
                    if radio.tx_status() == "tx":
                        log("SAFETY: the radio was transmitting when the bridge started: un-keying it"); rig_cmd("T 0"); radio.release()
                if not a.dummy and not radio.alive(): log("the radio does not answer a status request")
                if radio.check(): log("NOT the right radio: " + radio.check())
            except Exception as e: log(f"start check: {e!r}")
            try: lsb_resume(); cw_resume()
            except Exception as e: log(f"software LSB / CW not resumed: {e!r}")
            return
        except (OSError, ValueError): pass
    log(f"rigctld does not answer: check the CAT cable, the radio's menu {RADIO.baud_menu}, and that the radio is on")

# ---------------------------------------------------------------- PTT state + safety
def set_keyed(on, who, why=""):
    with lock:
        if on == st["keyed"]:
            if on: st["keyed_by"] = who
            return
        st["keyed"] = on; st["keyed_by"] = who if on else None
        stats = ""
        if on:
            st["key_ts"] = time.time(); st["txq"].clear(); st["playing"] = False
            st["tx_played"] = st["tx_sound"] = st["underruns"] = 0
            st["tx_sq"] = st["tx_n"] = st["tx_peak"] = 0
            st["tx_meter"] = []; st["swr_n"] = 0; st["meter_ok"] = None; st["safety"] = None
        else:
            r2 = a.rate * 2
            stats = (f": {time.time() - st['key_ts']:.1f} s keyed, {st['tx_played'] / r2:.2f} s of audio sent to the "
                     f"radio ({st['tx_sound'] / r2:.2f} s not silent), {st['underruns']} underruns, "
                     f"{len(st['txq']) / r2:.2f} s left unplayed, audio level "
                     f"{dbfs((st['tx_sq'] / max(st['tx_n'], 1)) ** 0.5)} average, {dbfs(st['tx_peak'])} peak")
            m = st["tx_meter"]
            if m:
                n15 = lambda k: f"{max(x[k] for x in m) * 15:.0f}/15 max, {sum(x[k] for x in m) / len(m) * 15:.1f} average"
                stats += f"; radio meters ({len(m)} reads): power {n15(0)}; ALC {n15(1)}; SWR {n15(2)}"
            st["txq"].clear(); st["playing"] = False
    if not on:
        with lock: st["unkeys"] += 1; n = st["unkeys"]; st["last_tx"] = st["tx_hz"]; st["unkey_at"] = st["cmd_at"] = time.time()       # (cmd_at: the poller reads the dial again at once)
        threading.Thread(target=verify_rx, args=(n,), daemon=True).start()
    if a.verbose or why: log(("radio KEYED" if on else "radio un-keyed") + (f" ({why})" if why else "") + stats)
    audio_send(b"K", b"1" if on else b"0")

def safety_unkey(why):
    log(f"SAFETY: un-keying the radio: {why}")
    try:
        r = rig_cmd("T 0")
        if r != "RPRT 0": log(f"SAFETY: rigctld answered {r!r} to T 0")
    except OSError as e:
        log(f"SAFETY: rigctld unreachable ({e}): restarting it (releases PTT)")
        if rig_proc and rig_proc.poll() is None: rig_proc.terminate()
    set_keyed(False, None, "safety")

# ---------------------------------------------------------------- software LSB (mode "DLSB" to the panel; ft897_lsb.py)
LSB_FILE = "/dev/shm/ft897-lsb.json" if RADIO.caps["lsb"] and os.path.isdir("/dev/shm") and not a.dummy else ""      # survives a restart of the bridge, not of the Pi
inv_rx, inv_tx = Invert(), Invert()
RPT_FILE = "/dev/shm/ft897-rpt.json" if not RADIO.caps["reports"] and os.path.isdir("/dev/shm") and not a.dummy else ""
def rpt_save():
    if not RPT_FILE: return
    try:
        with open(RPT_FILE, "w") as f: json.dump(st["rpt"], f)
    except OSError: pass
def rpt_load():
    if not RPT_FILE: return
    try:
        with open(RPT_FILE) as f: j = json.load(f)
        if isinstance(j, dict): st["rpt"].update({k: j.get(k) for k in st["rpt"] if k in j})
    except (OSError, ValueError): pass
def rpt_track(line):
    """Remembers repeater shift, offset and tones set by a CAT line (panel, favorite or an app): the FT-897 has no way to report them."""
    t = line.strip().lstrip("+;|,").split()
    if len(t) < 2: return
    c, v = t[0], t[-1]; r = st["rpt"]; old = dict(r)
    try:
        if c in ("R", "\\set_rptr_shift") and v in ("+", "-", "0"): r["shift"] = v
        elif c in ("O", "\\set_rptr_offs"): r["offset"] = int(float(v))
        elif c in ("C", "\\set_ctcss_tone"): r["tone"] = int(float(v)) / 10
        elif c in ("U", "\\set_func") and len(t) >= 3 and t[-2] in ("TONE", "TSQL"): r["mode"] = t[-2].lower() if v == "1" else "none"
        elif c in ("D", "\\set_dcs_code"): r["dcs"] = int(v); r["mode"] = "dcs" if int(v) else "none"
        elif c == "\\set_dcs_sql": r["dcs"] = int(v); r["mode"] = "dsql" if int(v) else "none"
        else: return
    except ValueError: return
    if c in ("R", "\\set_rptr_shift") and v == "0" and r["mode"] in (None,) : pass
    r["hz"] = st["rig"].get("freq") or 0
    if r != old: rpt_save()
def rpt_read():
    """FT-891: the radio reports its repeater shift and tones itself."""
    r = radio.rpt()
    if r: st["rpt"].update(r)
def tx_freq(hz, mode):
    """Where the radio will transmit: with a repeater shift in FM that is not where it listens."""
    r = st["rpt"]
    # while it transmits (and for a moment after) the FT-897 reports the transmit frequency itself
    if st["keyed"] or (st["last_tx"] and abs(hz - st["last_tx"]) < 5 and (time.time() - st["unkey_at"] < 3 or st["rig"].get("freq_at", 0) < st["unkey_at"] + 1)): return hz
    return hz + (r["offset"] if r["shift"] == "+" else -r["offset"]) if mode in ("FM", "FMN") and r["shift"] in ("+", "-") and r["offset"] else hz
def soft_off(): return PIVOT if st["lsb"] else st["cw"]              # software LSB / CW: how far the dial is below the carrier
def vhz(hz): return hz + soft_off() if hz else hz                     # the frequency to show and to check: the carrier
def raw_hz(hz): return hz - soft_off()                                # what the dial has to be
def tx_reason(v):
    """Locks for a carrier frequency as shown; with software LSB the signal is the 2.8 kHz below it."""
    return acc.reason(v) or (acc.reason(v - 2800) if st["lsb"] and v else "")
def lsb_set(on, why=""):
    with lock:
        if st["lsb"] == on: return
        st["lsb"] = on; st["lsb_at"] = time.time(); inv_rx.reset(); inv_tx.reset(); st["tx_hz"] = 0
    log(f"software LSB {'on' if on else 'off'}" + (f" ({why})" if why else ""))
    try:
        if LSB_FILE and on:
            with open(LSB_FILE, "w") as f: json.dump({"on": True}, f)
        elif LSB_FILE: os.remove(LSB_FILE)
    except OSError: pass
dig_side = [None]
def dig_upper():
    """The radio's DIG menu (038), from its memory, read only, once: 5 = USER-U, 4 = USER-L. None = could not be read."""
    if dig_side[0] is None and KNOWN: dig_side[0] = radio.dig_upper()
    return dig_side[0]
def lsb_on(hz=None):
    """Into software LSB on carrier hz (None = the frequency shown now). -> (code, message)"""
    if not RADIO.caps["lsb"]: return 1, "this radio has no LSB voice: use LSB"
    if a.rate != 48000: return 1, "LSB voice needs the 48 kHz audio rate"
    if st["keyed"]: return 1, "transmitting"
    c = st.get("client") if st["audio"] is not None else None
    if c and c.get("voice") is False: return 1, "LSB voice needs the voice client: an FT8 / data audio client is connected"
    d = dig_upper()
    if d == 4: return 1, "the radio's DIG is USER-L (menu 038), so DIG is LSB already. For LSB voice set it to USER-U"
    if d is None and KNOWN: return 1, "the radio's DIG menu could not be read: is the radio on?"
    if d is not None and d != 5: return 1, "set the radio's menu 038 DIG MODE to USER-U first"
    if rig.cmd("s")[1].get("Split") == "1": return 1, "switch split off first"
    if st["cw"]:
        if hz is None: hz = vhz(st["rig"].get("freq") or 0) or None
        cw_set(0, "LSB voice chosen")
    if hz is None:
        if st["lsb"]:
            if rig.cmd("m")[1].get("Mode") == "PKTUSB": return 0, ""
            lsb_set(False, "the mode was changed at the radio")
        r, v = rig.cmd("f")
        try: hz = int(float(v.get("Frequency", 0)))
        except ValueError: hz = 0
        if r or not hz: return r or 1, "the frequency is not known"
    if not 1_000_000 <= hz <= RADIO.max_hz: return 1, "frequency out of range"
    for cmd in ("M PKTUSB 0", f"F {hz - PIVOT}"):
        r = rig.cmd(cmd)[0]
        if r != 0: return r, f"the radio refused \"{cmd}\" (RPRT {r})"
    with lock: st["rig"].update(freq=hz - PIVOT, mode="PKTUSB")
    lsb_set(True); return 0, ""
def lsb_leave(restore=True, why=""):
    """Out of software LSB; restore: put the dial on the carrier frequency."""
    if not st["lsb"]: return
    if restore:
        try: hz = int(float(rig.cmd("f")[1].get("Frequency", 0)))
        except ValueError: hz = 0
        if hz and rig.cmd(f"F {hz + PIVOT}")[0] == 0:
            with lock: st["rig"]["freq"] = hz + PIVOT
    lsb_set(False, why)
def lsb_resume():
    """After a restart of the bridge: the radio is still 3 kHz low in DIG."""
    if not LSB_FILE or not os.path.exists(LSB_FILE): return
    if rig.cmd("m")[1].get("Mode") == "PKTUSB": lsb_set(True, "as before the restart")
    else:
        try: os.remove(LSB_FILE)
        except OSError: pass

# ---------------------------------------------------------------- CW through DIG (mode "DCW" to the panel; ft897_cw.py)
CW_SHM = "/dev/shm/ft897-cw.json" if RADIO.caps["lsb"] and os.path.isdir("/dev/shm") and not a.dummy else ""
CW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cw.json")
CWDEF = {"wpm": 20, "pitch": 700, "hang": 0.8, "level": -6.0, "farns": 0, "auto": True}
CWR = {"wpm": (5, 50), "pitch": (400, 1000), "hang": (0.2, 3.0), "level": (-30.0, 0.0), "farns": (0, 50)}
cwset = dict(CWDEF)
def cw_load():
    try:
        with open(CW_FILE) as f: cw_clean(json.load(f))
    except (OSError, ValueError, TypeError): pass
def cw_clean(j):
    """Copies valid settings from j into cwset. -> list of what changed"""
    ch = []
    if not isinstance(j, dict): return ch
    for k, (lo, hi) in CWR.items():
        v = j.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = type(CWDEF[k])(max(lo, min(hi, v)))
            if v != cwset[k]: cwset[k] = v; ch.append(k)
    if isinstance(j.get("auto"), bool) and j["auto"] != cwset["auto"]: cwset["auto"] = j["auto"]; ch.append("auto")
    return ch
def cw_save():
    try:
        with open(CW_FILE + ".tmp", "w") as f: json.dump(cwset, f)
        os.replace(CW_FILE + ".tmp", CW_FILE)
    except OSError as e: log(f"cw.json not saved: {e}")
cwtl = cwm.Timeline()
cwq = {"text": [], "next": 0.0, "played": [], "sent": "", "err": "", "err_at": 0.0, "stop": False, "who": "", "ip": "", "seen_down": 0.0}
cw_lock = threading.Lock()
cw_dec = cwm.Decoder(CWDEF["pitch"], 20)          # listens from 20 WPM and follows the station, whatever speed you send at
cw_rxq = queue.Queue(maxsize=50)
def cw_set(pitch, why=""):
    """CW mode on (pitch Hz: the dial is that far below the carrier) or off (0). Only flags: cw_on / cw_leave tune."""
    with lock:
        if st["cw"] == pitch: return
        was = st["cw"]; st["cw"] = pitch; st["cw_at"] = time.time(); st["tx_hz"] = 0
    with cw_lock: cwq["text"].clear(); cwq["played"].clear()
    cwtl.clear(time.time()); cw_dec.set_f(pitch or cwset["pitch"])        # (the decoder keeps the speed it last heard)
    if bool(was) != bool(pitch): log(f"CW keyer mode {'on' if pitch else 'off'}" + (f" ({why})" if why else ""))
    try:
        if CW_SHM and pitch:
            with open(CW_SHM, "w") as f: json.dump({"pitch": pitch}, f)
        elif CW_SHM and os.path.exists(CW_SHM): os.remove(CW_SHM)
    except OSError: pass
def cw_on(hz=None):
    """Into CW keyer mode on carrier hz (None = the frequency shown now). -> (code, message)"""
    if not RADIO.caps["lsb"]: return 1, "this radio has no CW keyer mode yet"
    if a.rate != 48000: return 1, "the CW keyer needs the 48 kHz audio rate"
    if st["keyed"]: return 1, "transmitting"
    d = dig_upper()
    if d == 4: return 1, "the radio's DIG is USER-L (menu 038): set it to USER-U for the CW keyer"
    if d is None and KNOWN: return 1, "the radio's DIG menu could not be read: is the radio on?"
    if d is not None and d != 5: return 1, "set the radio's menu 038 DIG MODE to USER-U first"
    if rig.cmd("s")[1].get("Split") == "1": return 1, "switch split off first"
    if hz is None:
        if st["cw"] and rig.cmd("m")[1].get("Mode") == "PKTUSB": return 0, ""
        if st["lsb"] or st["cw"]: hz = vhz(st["rig"].get("freq") or 0)
        else:
            r, v = rig.cmd("f")
            try: hz = int(float(v.get("Frequency", 0)))
            except ValueError: hz = 0
            if r or not hz: return r or 1, "the frequency is not known"
    if not 1_000_000 <= hz <= RADIO.max_hz: return 1, "frequency out of range"
    if st["lsb"]: lsb_set(False, "CW keyer chosen")
    p = cwset["pitch"]
    for cmd in ("M PKTUSB 0", f"F {hz - p}"):
        r = rig.cmd(cmd)[0]
        if r != 0: return r, f"the radio refused \"{cmd}\" (RPRT {r})"
    with lock: st["rig"].update(freq=hz - p, mode="PKTUSB")
    cw_set(p); return 0, ""
def cw_leave(restore=True, why=""):
    """Out of CW keyer mode; restore: put the dial on the carrier frequency."""
    if not st["cw"]: return
    if restore:
        try: hz = int(float(rig.cmd("f")[1].get("Frequency", 0)))
        except ValueError: hz = 0
        if hz and rig.cmd(f"F {hz + st['cw']}")[0] == 0:
            with lock: st["rig"]["freq"] = hz + st["cw"]
    cw_set(0, why)
def cw_resume():
    if not CW_SHM or not os.path.exists(CW_SHM): return
    try:
        with open(CW_SHM) as f: p = int(json.load(f).get("pitch") or 0)
    except (OSError, ValueError, TypeError, AttributeError): p = 0
    if p and rig.cmd("m")[1].get("Mode") == "PKTUSB": cw_set(p, "as before the restart")
    else: cw_set(0)
def cw_err(why):
    with cw_lock: cwq["err"] = why; cwq["err_at"] = time.time(); cwq["text"].clear()
    cwtl.clear(time.time())
def cw_amp(): return 10 ** (min(cwset["level"], cfg["max_drive"]) / 20)
def cw_mine(): return (st["keyed_by"] or "").startswith("cw:")
def cw_loop():
    """Keys the radio for the CW keyer, feeds typed text into the timeline, lets go after the hang time, and lets go of a key that
    is held down while the panel has gone quiet (browser closed, Wi-Fi drop)."""
    while True:
        time.sleep(0.005); now = time.time()
        if not st["cw"]:
            if cw_mine(): rig.cmd("T 0"); set_keyed(False, None, "CW mode left")
            continue
        with cw_lock:                              # typed text: keep about a second scheduled ahead
            while cwq["text"] and not cwq["stop"]:
                start = max(cwq["next"], cwtl.end, now + 0.15)
                if start > now + 1.0: break
                tok = cwq["text"].pop(0)
                ev, nxt = cwm.char_events(tok, start, cwset["wpm"], cwset["farns"])
                cwtl.add(ev); cwq["next"] = nxt; cwq["played"].append((start, tok))
            while cwq["played"] and cwq["played"][0][0] <= now - cwm.DELAY * 0:
                cwq["sent"] = (cwq["sent"] + cwq["played"].pop(0)[1])[-80:]
            stop = cwq["stop"]; cwq["stop"] = False
        busy = cwtl.busy(now)
        if stop:
            cwtl.clear(now)
            if cw_mine(): rig.cmd("T 0"); set_keyed(False, None, "CW stopped")
            continue
        if busy and not cw_mine():
            if st["keyed"]: cw_err(f"already keyed by {st['keyed_by']}"); continue
            why = tx_block()
            if why: log(f"CW refused: {why}"); cw_err(why); continue
            with lock:
                if st["keyed"]: cw_err(f"already keyed by {st['keyed_by']}"); continue
                set_keyed(True, "cw:" + (cwq["ip"] or "?"))
            c, _ = rig.cmd("T 1")
            if c != 0: set_keyed(False, None); cw_err(f"radio refused PTT (RPRT {c})"); continue
            log(f"radio KEYED by the CW keyer ({cwq['who']})")
        if cw_mine():
            with cwtl.l: down = bool(cwtl.ev) and cwtl.ev[-1][1]; ds = cwtl.down_since; seen = cwtl.seen
            if down and now - seen > 1.0: cwtl.force_up(now); cw_err("the key was let go: the panel stopped answering")
            elif down and ds and now - ds > 15: cwtl.force_up(now); cw_err("the key was held down for 15 s")
            if not cwtl.busy(now) and now > cwtl.end + cwset["hang"] and not cwq["text"]:
                rig.cmd("T 0"); set_keyed(False, None)
        cwtl.prune(now - 0.5)
def cw_rx_loop():
    """Decoder: 48 kHz frames from rx_loop -> 8 kHz -> ft897_cw.Decoder."""
    last = 0.0
    while True:
        d = cw_rxq.get()
        s = array.array("h", d)
        cw_dec.feed([sum(s[i:i + 6]) / 6 for i in range(0, len(s) - 5, 6)])
        if cwset["auto"] and time.time() - last > 0.5: last = time.time(); cw_dec.track(st["cw"] or cwset["pitch"])
def cw_rx_feed(d):
    if st["cw"] and not st["keyed"]:
        try: cw_rxq.put_nowait(d)
        except queue.Full: pass
def cw_op(ip, j, admin=False):
    op = j.get("op")
    if op == "set":
        if cfg["locks"].get("txaudio") and not admin and "level" in j: return 1, "the transmit audio settings are locked by the administrator"
        old = cwset["pitch"]; ch = cw_clean(j)
        if "pitch" in ch and st["cw"]:
            if st["keyed"]: cwset["pitch"] = old; return 1, "not while transmitting"
            hz = vhz(st["rig"].get("freq") or 0); p = cwset["pitch"]
            if hz and rig.cmd(f"F {hz - p}")[0] == 0:
                with lock: st["rig"]["freq"] = hz - p
            cw_set(p)
        if "pitch" in ch or "auto" in ch: cw_dec.set_f(st["cw"] or cwset["pitch"])
        if ch: cw_save()
        return 0, ""
    if op == "clear": cw_dec.text = ""; return 0, ""
    if op == "stop":
        with cw_lock: cwq["text"].clear(); cwq["played"].clear(); cwq["stop"] = True
        return 0, ""
    if not st["cw"]: return 1, "choose the CW keyer mode first"
    if op == "key":
        ev = j.get("ev") or []
        if not isinstance(ev, list) or len(ev) > 200: return 1, "bad events"
        with cw_lock: cwq["who"] = host_name(ip); cwq["ip"] = ip
        if ev and st["keyed"] and not cw_mine(): return 1, f"already keyed by {st['keyed_by']}"
        cwtl.add_panel(ev, time.time()); return 0, ""
    if op == "text":
        t = cwm.clean_text(j.get("text"), 1000)
        if not t.strip(): return 1, "nothing to send"
        with cw_lock:
            if len(cwq["text"]) + len(t) > 2000: return 1, "too much text queued"
            cwq["who"] = host_name(ip); cwq["ip"] = ip; cwq["text"].extend(cwm.tokens(t))
        return 0, ""
    return 1, "unknown CW command"
def cw_state():
    now = time.time()
    with cw_lock:
        q = "".join(t for _, t in cwq["played"]) + "".join(cwq["text"])
        return {**cwset, "on": bool(st["cw"]), "rx": cw_dec.text[-400:], "rx_f": round(cw_dec.f), "rx_wpm": cw_dec.wpm(),
                "rx_snr": round(10 * __import__("math").log10(max(cw_dec.snr, 1e-9)), 1), "rx_on": cw_dec.on, "queue": q[:300], "sent": cwq["sent"],
                "err": cwq["err"] if now - cwq["err_at"] < 8 else "", "keyed": cw_mine(), "delay": cwm.DELAY}

def tx_block():
    """Asked before the radio is keyed, on every path: why transmitting is not allowed now ("" = go ahead). Reads the
    frequency from the radio, as its dial may have been turned. With split on, the other VFO is the one that transmits."""
    with lock: st["tx_hz"] = 0
    if not cfg["tx"]: return acc.reason(1)
    if st["stuck"]: return "the radio did not go back to receive after the last transmission"
    if st["cat_dead"] or not alive(): return "the radio is not answering"
    if radio.check(): return radio.check()
    try: hz = int(float(rig.cmd("f")[1].get("Frequency", 0)))
    except ValueError: hz = 0
    m = rig.cmd("m")[1].get("Mode")
    if st["lsb"]:
        if m != "PKTUSB":
            lsb_set(False, f"the radio is in {m}"); return "the radio's mode was changed: LSB voice is off now, press its button again"
        hz = vhz(hz); m = "DLSB"
    elif st["cw"]:
        if m != "PKTUSB":
            cw_set(0, f"the radio is in {m}"); return "the radio's mode was changed: the CW keyer is off now, press its button again"
        hz = vhz(hz); m = "DCW"
    if acc.mode_reason(m): return acc.mode_reason(m)
    with lock: st["tx_mode_why"] = ""
    c, v = rig.cmd("s")
    if c == 0 and v.get("Split") == "1":
        if st["lsb"] or st["cw"]: return "switch split off: LSB voice and the CW keyer can't use it"
        tx = v.get("TX VFO"); cur = rig.cmd("v")[1].get("VFO")
        if hz and cur: st["vfo_freq"][cur] = hz
        if cur == tx: pass
        else:
            if KNOWN and RADIO.key == "ft891": st["vfo_freq"][tx] = radio.tx_vfo_hz()
            hz = st["vfo_freq"].get(tx, 0)
            if not hz and acc.reason(0): return f"split is on and the frequency of {tx} is not known yet: select it once"
    tx = hz
    if RADIO.caps["reports"] and not a.dummy: rpt_read()
    if st["rpt"]["shift"] in ("+", "-") and not st["lsb"] and not st["cw"]:
        tx = tx_freq(hz, m)
    why = tx_reason(tx)
    if why and tx != hz: why = f"the repeater shift would transmit on {acc.mhz(tx)}: {why}"
    if not why:
        with lock: st["tx_hz"] = tx
    return why

def watchdog():
    while True:
        time.sleep(0.1)
        why = None
        with lock:
            if st["keyed"]:
                now = time.time(); keyed_for = now - st["key_ts"]
                if keyed_for > cfg["tx_timeout"]:
                    why = f"keyed for more than {cfg['tx_timeout']} s"
                elif st["tx_hz"] and tx_reason(st["tx_hz"]): why = tx_reason(st["tx_hz"])      # locked meanwhile
                elif st.get("tx_mode_why"): why = st["tx_mode_why"]                              # mode changed on the radio itself
                elif not a.no_audio_check and not cw_mine():           # (the CW keyer makes its own audio)
                    if st["audio"] is None and keyed_for > a.stall:
                        why = "no laptop audio link (start ft897-audio.py on the laptop)"
                    elif st["audio"] is not None and now - max(st["last_audio"], st["key_ts"]) > a.stall:
                        why = f"no transmit audio from the laptop for {a.stall:.1f} s"
                if not why and (st["keyed_by"] or "").startswith("web:") and now - st["web_ptt_seen"] > 1.0:
                    why = "the web panel's PTT button stopped (browser closed or Wi-Fi drop)"
            if st["owner"] and time.time() - st["owner_seen"] > cfg["owner_timeout"]:
                log(f"control released: {st['owner_name']} not heard from for {cfg['owner_timeout']} s")
                st["owner"] = st["owner_name"] = None
        if why: st["safety"] = {"why": why, "at": time.time()}; safety_unkey(why)
        request_step()

# ---------------------------------------------------------------- CAT proxy (Hamlib rigctld protocol)
def ptt_value(line):
    """'T 1', 'T VFOA 1', '\\set_ptt 1', '+\\set_ptt VFOA 0' -> True/False; anything else -> None"""
    s = line.strip()
    if s[:1] in "+;|,": s = s[1:]
    t = s.split()
    if len(t) >= 2 and t[0] in ("T", "\\set_ptt", "set_ptt"):
        try: return int(t[-1]) != 0
        except ValueError: return None
    return None

def cat_refuse(text):
    """Lines from CAT apps that never reach the radio: raw CAT bytes (would get round the transmit locks, and can write the
    radio's EEPROM), and a PTT command hidden behind another one on the same line or cut off before its value."""
    t = text.strip().lstrip("+;|,").split()
    if not t: return False
    if len(t[0]) > 1 and not t[0].startswith("\\"): return True      # rigctld would read it as several one-letter commands
    if t[0] in ("w", "W", "\\send_cmd", "send_cmd", "\\send_cmd_rx", "send_cmd_rx"): return True
    ptt = ("T", "\\set_ptt", "set_ptt")
    if t[0] in ptt: return ptt_value(text) is None or len(t) > 3
    return any(x.lstrip("+;|,") in ptt for x in t[1:])

def tok0(text):
    t = text.strip().lstrip("+;|,").split(); return t[0] if t else ""

HTTP = re.compile(rb"^(GET|POST|PUT|HEAD|OPTIONS|DELETE|PATCH|CONNECT|TRACE|PRI) \S+ HTTP/|^Host:", re.I)

def drain_tx(limit=1.5):
    """On PTT off: wait until the app's last transmit audio has been played to the radio, so the end of a
    transmission is not cut off. The laptop sends silence (zeros) once the app stops, so: wait until no sound has
    arrived for 0.1 s and none is left in the queue, then for the sound card's buffer (0.1 s)."""
    t0 = time.time()
    with lock: st["draining"] = True
    try:
        while time.time() - t0 < limit:
            with lock:
                done = time.time() - st["last_sound"] > 0.1 and not any(st["txq"])
            if done: break
            time.sleep(0.01)
        time.sleep(0.1)
    finally:
        with lock: st["draining"] = False
    if a.verbose: log(f"PTT off held {time.time() - t0:.2f} s for the last transmit audio")

def cat_client(c, addr):
    name = f"{addr[0]}:{addr[1]}"; ip = ip_of(addr)
    if not allowed(ip):
        if a.verbose: log(f"CAT client {name} refused: {why_not()}")
        c.close(); return
    try: up = socket.create_connection(("127.0.0.1", a.rigctld_port), timeout=5)
    except OSError as e:
        log(f"CAT client {name} refused: rigctld not running ({e})"); c.close(); return
    up.settimeout(None); c.settimeout(None)
    for s in (c, up): s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if a.verbose: log(f"CAT client {name} connected")
    with lock: st["cat_clients"][name] = {"sock": c, "up": up, "ip": ip, "since": time.time()}
    done = threading.Event()
    pending = []                                       # PTT-on commands waiting for rigctld's answer

    def downstream():                                  # rigctld -> app
        tail = b""
        try:
            while True:
                d = up.recv(65536)
                if not d: break
                if pending:
                    tail = (tail + d)[-256:]
                    i = tail.find(b"RPRT ")
                    if i >= 0 and b"\n" in tail[i:]:
                        code = tail[i + 5:].split(b"\n")[0].strip(); tail = b""; pending.clear()
                        if code != b"0":
                            log(f"rigctld refused PTT on for {name} (RPRT {code.decode()}): radio not keyed")
                            set_keyed(False, None)
                c.sendall(d)
        except OSError: pass
        done.set()
        for s in (c, up):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass
    threading.Thread(target=downstream, daemon=True).start()

    buf = b""
    try:
        while not done.is_set():
            d = c.recv(65536)
            if not d: break
            seen(ip)
            buf += d
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if HTTP.match(line):                           # a web page trying to reach the CAT port
                    log(f"CAT client {name} closed: that was a web browser"); raise OSError("http")
                text = line.decode(errors="replace").replace("\r", "")
                v = ptt_value(text)
                if cat_refuse(text):
                    log(f"CAT client {name}: refused {text.strip()[:60]!r}"); c.sendall(b"RPRT -11\n"); continue
                if ip == "127.0.0.1" and ap_running() and tok0(text) in ("V", "\\set_vfo"):
                    # Dire Wolf's Hamlib flips VFO A/B at start to read both frequencies; on the FT-897 that is a swap, which can leave the
                    # radio on the other VFO. Dire Wolf only needs PTT: say yes and leave the radio alone.
                    v2 = text.strip().split()[-1] if len(text.split()) > 1 else ""
                    c.sendall((f"set_vfo: {v2}\n".encode() if text.strip()[:1] in "+;|," else b"") + b"RPRT 0\n"); continue
                if RADIO.key == "ft891" and not a.dummy and re.fullmatch(r"[+;|,]?(S|\\set_split_vfo)( currVFO| VFO[AB])? 0( \w+)?", text.strip()):      # Hamlib 4.5.5 can't: RPRT -12
                    ok = radio.line("S 0 VFOA")[0] == 0
                    c.sendall((b"set_split_vfo: 0 VFOA\n" if text.strip()[:1] in "+;|," else b"") + (b"RPRT 0\n" if ok else b"RPRT -9\n")); continue
                if (st["lsb"] or st["cw"]) and tok0(text) in ("F", "M", "V", "S", "I", "X", "G", "\\set_freq", "\\set_mode", "\\set_vfo", "\\set_split_vfo", "\\set_split_freq", "\\set_split_mode", "\\vfo_op"):
                    if st["keyed"]: c.sendall(b"RPRT -9\n"); continue
                    lsb_set(False, f"{name} sets the radio"); cw_set(0, f"{name} sets the radio")          # an app such as WSJT-X has taken over
                if v is True and not (st["keyed"] and st["keyed_by"] == name):
                    why = f"already keyed by {st['keyed_by']}" if st["keyed"] else tx_block()
                    if why:
                        log(f"PTT on refused for {name}: {why}"); st["safety"] = {"why": why, "at": time.time()}
                        c.sendall((b"set_ptt: 1\n" if text.strip()[:1] in "+;|," else b"") + b"RPRT -9\n"); continue
                if v is True:
                    if not st["rig_ok"]: log("PTT on while the radio is not answering CAT")
                    set_keyed(True, name)                       # before forwarding: TX audio is accepted at once
                    pending.append(1)
                elif v is False and st["keyed"]:
                    drain_tx()                                  # let the last TX audio reach the radio first
                tok = text.strip().lstrip("+;|,").split()
                if a.verbose and tok and tok[0] in ("F", "\\set_freq") and tok[-1].split(".")[0].isdigit():
                    f = tok[-1].split(".")[0]
                    if f != st["freq"]: st["freq"] = f; log(f"{name} set frequency {int(f) / 1e6:.4f} MHz")
                if len(tok) > 1 and tok[-1] in ("VFOA", "VFOB") and tok[0] in ("S", "\\set_split_vfo"): st["tx_vfo"] = tok[-1]
                if len(tok) > 1 and tok[0] in ("I", "\\set_split_freq") and tok[-1].split(".")[0].isdigit():
                    st["vfo_freq"][st.get("tx_vfo", "VFOB")] = int(tok[-1].split(".")[0])
                if st["keyed"] and len(tok) > 1 and tok[0] in ("M", "\\set_mode") and acc.mode_reason(tok[1]):
                    log(f"{name}: mode refused while transmitting: {acc.mode_reason(tok[1])}"); c.sendall(b"RPRT -9\n"); continue
                if st["keyed"] and len(tok) > 1 and tok[0] in ("F", "\\set_freq") and tok[-1].split(".")[0].isdigit():
                    why = acc.reason(int(tok[-1].split(".")[0]))      # moving while transmitting: not into a locked band
                    if why: log(f"{name}: frequency refused while transmitting: {why}"); c.sendall(b"RPRT -9\n"); continue
                    if not st["rig"].get("split"): st["tx_hz"] = int(tok[-1].split(".")[0])
                rpt_track(text)                                 # (an app that sets shift or tones itself)
                up.sendall(line + b"\n")
                if v is False: set_keyed(False, name)
    except OSError: pass
    done.set()
    for s in (c, up):
        try: s.close()
        except OSError: pass
    if a.verbose: log(f"CAT client {name} disconnected")
    with lock: st["cat_clients"].pop(name, None)
    with lock: mine = st["keyed"] and st["keyed_by"] == name
    if mine: safety_unkey(f"the app that keyed the radio ({name}) disconnected")

def cat_server():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((a.bind, a.cat_port)); srv.listen(8)
    log(f"CAT (Hamlib NET rigctl) on TCP {a.cat_port}")
    while True:
        c, addr = srv.accept()
        threading.Thread(target=cat_client, args=(c, addr), daemon=True).start()

# ---------------------------------------------------------------- audio
def alsa(cmd_name, dev, buffer_us):
    return [cmd_name, "-q", "-D", dev, "-f", "S16_LE", "-c", "1", "-r", str(a.rate), "-t", "raw",
            f"--buffer-time={buffer_us}", f"--period-time={FRAME_MS * 1000}"]

def wait_card():
    warned = False
    while True:
        dev = find_card()
        if dev: return dev
        if not warned: log("waiting for the Digirig sound card to appear..."); warned = True
        time.sleep(2)

tap_clean = TickClean()
taps = {}                                      # queue -> True = full 48 kHz, False = 8 kHz; viewers of the panel's waterfall
TAP_MAX = 6

def tap_feed(kind, frame):
    """Copies a 20 ms audio frame to the waterfall viewers (GET /api/audio). Never blocks: a slow viewer loses frames."""
    low = None
    for q, full in list(taps.items()):
        if full: d = frame
        else:
            if low is None:
                if RADIO.ticks and kind == b"R" and len(frame) == FRAME: frame = tap_clean.process(frame)         # (the full-rate feed stays raw)
                s = array.array("h", frame); k = max(1, a.rate // 8000)
                low = array.array("h", [sum(s[i:i + k]) // k for i in range(0, len(s) - k + 1, k)]).tobytes()
            d = low
        try: q.put_nowait(kind + struct.pack(">H", len(d)) + d)
        except queue.Full: pass

def set_levels(cid):
    """The card's levels as set on the administration page: a card that was unplugged comes back with its defaults, which can be
    20 dB too hot. Off (None) = left to alsamixer."""
    subprocess.run(["amixer", "-q", "-c", cid, "sset", "Auto Gain Control", "off"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for name, v in (("Mic", cfg["mic"]), ("Speaker", cfg["spk"])):
        if v is None: continue
        r = subprocess.run(["amixer", "-q", "-c", cid, "sset", name, str(v)] + (["capture"] if name == "Mic" else []), capture_output=True, text=True)
        log(f"sound card {name} level {v}" + (f" NOT set: {r.stderr.strip()}" if r.returncode else ""))

def rx_loop():
    """Digirig sound card -> laptop"""
    if a.card == "null":                       # test mode: a paced 1 kHz tone at -20 dBFS stands in for the radio
        import math
        tone = b"".join(struct.pack("<h", int(3276 * math.sin(2 * math.pi * 1000 * i / a.rate))) for i in range(FRAME // 2))
        t = time.time(); RXF = os.environ.get("FT897_RX_FILE", ""); rxd, rxm, rxp = b"", 0, 0
        while True:
            t += FRAME_MS / 1000; time.sleep(max(0, t - time.time()))
            d = tone
            if RXF and os.path.exists(RXF):            # test mode: what the "radio" hears comes from a file (once each time it changes, then silence)
                try:
                    m = os.stat(RXF).st_mtime_ns
                    if m != rxm: rxm = m; rxd = open(RXF, "rb").read(); rxp = 0
                except OSError: rxd = b""
                d = rxd[rxp:rxp + FRAME].ljust(FRAME, b"\0"); rxp += FRAME
            d = inv_rx.process(d) if st["lsb"] else d
            if st["audio"] is not None: audio_send(b"A", d)
            if taps and not st["keyed"]: tap_feed(b"R", d)
            cw_rx_feed(d)
    while True:
        dev = wait_card()
        cid = card_index(dev)
        if cid: set_levels(cid)                    # (and the CM108's capture AGC off: it pumps the noise floor)
        log(f"RX audio from {dev}")
        pr = subprocess.Popen(alsa("arecord", dev, 100000), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        while True:
            d = pr.stdout.read(FRAME)
            if not d: break
            with lock: c = st["audio"]
            if st["lsb"] and (c is not None or taps): d = inv_rx.process(d)
            if c is not None: audio_send(b"A", d)
            if taps and not st["keyed"]: tap_feed(b"R", d)     # (during transmit: the transmit audio, from tx_loop)
            cw_rx_feed(d)
        err = pr.stderr.read().decode(errors="replace").strip()
        log(f"RX audio stopped ({err or 'exit ' + str(pr.wait())}): retrying in 2 s"); time.sleep(2)

def tx_loop():
    """laptop -> Digirig sound card. Writes one 20 ms frame at a time into a 4 kB pipe, so audio reaches the radio
    within ~0.15 s (a full 64 kB pipe would add 0.7 s and cut off the end of every transmission)."""
    silence = bytes(FRAME); tone = cwm.Tone(a.rate); gen_t = None; LAT = 0.14
    while True:
        dev = wait_card()
        log(f"TX audio to {dev}")
        if dev == "null":                      # test mode: play into nothing at the real pace
            class Paced:
                t = time.time()
                def write(self, b):
                    Paced.t += len(b) / (a.rate * 2); time.sleep(max(0, Paced.t - time.time()))
                    if TX_RAW:                 # test mode only: what would go to the radio
                        with open(TX_RAW, "ab") as f: f.write(b if st["keyed"] else bytes(len(b)))
                def flush(self): pass
                def fileno(self): return -1
            class Fake:
                stdin = Paced(); stderr = None
                def poll(self): return None
            pr = Fake()
        else:
            pr = subprocess.Popen(alsa("aplay", dev, 100000), stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try: fcntl.fcntl(pr.stdin.fileno(), getattr(fcntl, "F_SETPIPE_SZ", 1031), 4096)
        except (OSError, ValueError): pass
        try:
            while pr.poll() is None:
                if st["keyed"] and cw_mine() and st["cw"]:     # the CW keyer: a keyed tone, made here
                    now = time.time()
                    if gen_t is None: gen_t = now + LAT
                    elif abs(gen_t - (now + LAT)) > 0.08 and tone.env == 0 and not cwtl.state_at(gen_t): gen_t = now + LAT   # catch up after a stall only in silence: a dit or dah is never cut short
                    chunk = tone.frame(gen_t, FRAME // 2, cwtl, st["cw"], cw_amp()); gen_t += FRAME_MS / 1000
                    smp = array.array("h", chunk); pk = max(max(smp), -min(smp))
                    with lock:
                        st["tx_played"] += FRAME; st["tx_sound"] += FRAME if pk else 0; st["tx_peak"] = max(st["tx_peak"], pk)
                        if pk: st["tx_sq"] += (pk * pk) // 2 * len(smp); st["tx_n"] += len(smp)        # (a sine: rms = peak / sqrt 2)
                    if taps: tap_feed(b"T", chunk)
                    pr.stdin.write(chunk); pr.stdin.flush(); continue
                gen_t = None
                with lock:
                    q = st["txq"]
                    if st["keyed"] and not st["playing"] and (len(q) >= st["prebuf"] or (st["draining"] and q)):
                        st["playing"] = True
                    if st["playing"] and (len(q) >= FRAME or (st["draining"] and q)):
                        chunk = bytes(q[:FRAME]).ljust(FRAME, b"\0"); del q[:FRAME]
                        st["tx_played"] += FRAME
                        if any(chunk): st["tx_sound"] += FRAME
                        smp = struct.unpack(f"<{FRAME // 2}h", chunk)
                        st["tx_sq"] += sum(x * x for x in smp); st["tx_n"] += len(smp)
                        st["tx_peak"] = max(st["tx_peak"], max(map(abs, smp)))
                    else:
                        if st["playing"] and st["keyed"]:   # underrun (network hiccup): re-buffer
                            st["playing"] = False; st["underruns"] += 1
                            if (st.get("client") or {}).get("voice"):      # voice: 100 ms more buffer from now on, up to 600 ms
                                st["prebuf"] = max(st["prebuf"], min(FRAME * 30, st["prebuf"] + FRAME * 5))
                            if a.verbose: log(f"TX audio underrun {time.time() - st['key_ts']:.2f} s after PTT on")
                        chunk = silence
                if taps and st["keyed"]: tap_feed(b"T", chunk)
                if st["lsb"] and st["keyed"]: chunk = inv_tx.process(chunk)
                pr.stdin.write(chunk); pr.stdin.flush()     # blocks at the sound card's pace
        except (BrokenPipeError, OSError): pass
        err = pr.stderr.read().decode(errors="replace").strip()
        log(f"TX audio stopped ({err or 'exit ' + str(pr.wait())}): retrying in 2 s"); time.sleep(2)

def drop_audio(c, why):
    with lock:
        if st["audio"] is not c: return
        st["audio"] = None; addr = st["audio_addr"]
    try: c.close()
    except OSError: pass
    log(f"laptop audio {addr} disconnected ({why})")

def audio_client(c, addr):
    name = f"{addr[0]}:{addr[1]}"
    c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    c.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    c.settimeout(10)
    try:
        t, hello = recv_frame(c)
        info = json.loads(hello or b"{}")
        if t != b"H" or not isinstance(info, dict): raise ValueError("not our client")
    except (OSError, ValueError, ConnectionError):
        c.close(); return
    who = f"{info.get('name', '?')} ({name})"; ip = ip_of(addr)
    if not allowed(ip):
        try: send_frame(c, b"B", why_not().encode())
        except OSError: pass
        c.close(); log(f"refused laptop audio {who}: {why_not()}"); return
    with lock:
        cur = st["audio"]
        if cur is not None and time.time() - st["last_frame"] < 5 and st["audio_ip"] != ip and st["owner"] != ip:
            busy = st["audio_addr"]
        else: busy = None
    if busy:
        try: send_frame(c, b"B", f"radio in use by {busy}".encode())
        except OSError: pass
        c.close(); log(f"refused laptop audio {who}: in use by {busy}"); return
    if cur is not None: drop_audio(cur, f"replaced by {who}")
    send_frame(c, b"H", json.dumps({"bridge": VERSION, "rate": a.rate, "frame_ms": FRAME_MS}).encode())
    with lock:
        st["audio"] = c; st["audio_addr"] = who; st["last_frame"] = time.time(); keyed = st["keyed"]
        st["audio_ip"] = ip; st["audio_since"] = time.time(); st["client"] = None
        try: ms = max(60, min(600, int(info.get("tx_buffer_ms") or 160)))
        except (TypeError, ValueError): ms = 160
        st["prebuf"] = FRAME * round(ms / FRAME_MS)
    log(f"laptop audio {who} connected (TX buffer {st['prebuf'] * 1000 // (a.rate * 2)} ms)")
    if keyed: audio_send(b"K", b"1")
    try:
        while True:
            t, d = recv_frame(c)
            now = time.time(); limit_drive = False
            with lock:
                if st["audio"] is not c: return
                st["last_frame"] = now
                if st["owner"] == ip: st["owner_seen"] = now
                if t == b"S":                               # the client's settings and state, for the panel
                    try: st["client"] = json.loads(d); st["client_ts"] = now
                    except ValueError: pass
                    else: limit_drive = True
                elif t == b"A":
                    st["last_audio"] = now
                    if st["keyed"]:                         # never pass audio to an un-keyed radio
                        q = st["txq"]; q += d
                        if any(d): st["last_sound"] = now
                        pb = st["prebuf"]
                        if len(q) > pb + TXMAX_EXTRA: del q[:len(q) - pb]      # catch up after a burst
            if limit_drive and st["lsb"] and not st["keyed"] and isinstance(st["client"], dict) and st["client"].get("voice") is False:
                lsb_leave(True, "a data audio client connected")
            if limit_drive and isinstance(st["client"], dict) and st["client"].get("voice"):
                dr = (st["client"].get("set") or {}).get("drive")
                if isinstance(dr, (int, float)) and dr > cfg["max_drive"]:
                    audio_send(b"C", json.dumps({"set": {"drive": float(cfg["max_drive"])}}).encode())
    except socket.timeout: why = "no data for 10 s"
    except (OSError, ConnectionError) as e: why = str(e) or "closed"
    drop_audio(c, why)

def audio_server():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((a.bind, a.audio_port)); srv.listen(4)
    log(f"audio (ft897-audio.py) on TCP {a.audio_port}, {a.rate} Hz 16-bit mono")
    while True:
        c, addr = srv.accept()
        threading.Thread(target=audio_client, args=(c, addr), daemon=True).start()

# ---------------------------------------------------------------- Winlink on the Pi: xara (VARA-compatible modem) + Pat (mail)
WL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "winlink.json")
WLDEF = {"call": "", "locator": "", "bw": 2300, "drive": -12.0}      # drive: as for WSJT-X, no ALC
wlset = dict(WLDEF)
wl = {"procs": {}, "by": None, "since": 0.0, "log": collections.deque(maxlen=60), "err": "", "stopping": False}
wl_lock = threading.Lock()
LOOP_IN, LOOP_OUT = "plughw:CARD=Loopback,DEV=1,SUBDEV=0", "plughw:CARD=Loopback,DEV=0,SUBDEV=1"     # xara's capture / playback
def wl_load():
    try:
        with open(WL_FILE) as f: wl_clean(json.load(f))
    except (OSError, ValueError, TypeError): pass
def wl_clean(j):
    if not isinstance(j, dict): return
    c = str(j.get("call", wlset["call"]) or "").strip().upper()
    if re.fullmatch(r"[A-Z0-9]{1,3}[0-9][A-Z]{1,3}(-[0-9]{1,2})?", c) or c == "": wlset["call"] = c      # (as xara checks it)
    g = str(j.get("locator", wlset["locator"]) or "").strip()
    if re.fullmatch(r"[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?", g) or g == "": wlset["locator"] = g[:2].upper() + g[2:4] + g[4:].lower()
    if j.get("bw") in (500, 2300, 2750): wlset["bw"] = j["bw"]
    if isinstance(j.get("drive"), (int, float)) and not isinstance(j.get("drive"), bool): wlset["drive"] = float(max(-30, min(0, j["drive"])))
def wl_save():
    try:
        with open(WL_FILE + ".tmp", "w") as f: json.dump(wlset, f)
        os.replace(WL_FILE + ".tmp", WL_FILE)
    except OSError as e: log(f"winlink.json not saved: {e}")
def pat_bin(): return a.pat or shutil.which("pat") or shutil.which("pat-winlink") or "pat"      # (looked up each time: Pat may be upgraded meanwhile)
def wl_xara(): return os.path.expanduser(a.xara)
XARA_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xara-state") if a.dummy else os.path.expanduser("~/.local/state/xara")
def wl_missing():
    """What is not installed for Winlink on the Pi ("" = all there)."""
    if not os.path.isfile(wl_xara()) or not os.access(wl_xara(), os.X_OK): return f"xara is not installed ({a.xara}): see the manual, Winlink on the Pi"
    if not shutil.which(pat_bin()): return "Pat is not installed (sudo apt install pat)"
    if not a.dummy and not os.path.exists("/proc/asound/Loopback"): return "the loopback sound card is not there (sudo modprobe snd-aloop; see the manual)"
    return ""
def wl_running():
    with wl_lock: return bool(wl["procs"])
def wl_reader(name, pr):
    for line in iter(pr.stdout.readline, b""):
        t = line.decode(errors="replace").rstrip()
        if "got a vara command I wasn't expecting" in t: continue       # Pat 0.15 doesn't know VARA's SN / BITRATE reports: harmless
        if t: wl["log"].append(f"{time.strftime('%H:%M:%S')} {name}: {t[:200]}")
    rc = pr.wait()
    with wl_lock: mine = wl["procs"].get(name) is pr; stopping = wl["stopping"]
    if mine and not stopping:
        wl["err"] = f"{name} stopped (exit {rc}): see its log"; log(f"Winlink: {name} stopped (exit {rc}); stopping the rest")
        threading.Thread(target=wl_stop, args=(f"{name} stopped",), daemon=True).start()
def wl_pat_config():
    """Pat's settings for this Pi: VARA HF on xara, the radio through the bridge's CAT port, the web page on --pat-port.
    The Winlink password is never written here by the bridge: Pat asks for it, or you enter it in Pat."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pat-config.json") if a.dummy else os.path.expanduser("~/.config/pat/config.json")
    try:
        with open(path) as f: c = json.load(f)
    except (OSError, ValueError): c = {}
    if not isinstance(c, dict): c = {}
    c["mycall"] = wlset["call"]; c["locator"] = wlset["locator"] or c.get("locator", "")
    c["http_addr"] = f"{a.bind}:{a.pat_port}"
    c.setdefault("service_codes", ["PUBLIC"])
    rigs = c.get("hamlib_rigs") if isinstance(c.get("hamlib_rigs"), dict) else {}
    rigs["ft897"] = {"address": f"127.0.0.1:{a.cat_port}", "network": "tcp"}; c["hamlib_rigs"] = rigs
    v = c.get("varahf") if isinstance(c.get("varahf"), dict) else {}
    v.update({"addr": "127.0.0.1:8300", "bandwidth": wlset["bw"], "rig": "ft897", "ptt_ctrl": False}); c["varahf"] = v
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f: json.dump(c, f, indent=2)
    os.chmod(path + ".tmp", 0o600); os.replace(path + ".tmp", path)
    return path
def wl_start(ip, stop_voice=False):
    if wl_running(): return 0, ""
    if ap_running(): return 1, "stop APRS first: both need the audio link"
    why = wl_missing()
    if why: return 1, why
    if not wlset["call"]: return 1, "enter your call sign first"
    if st["keyed"]: return 1, "transmitting"
    with lock: aud = st["audio"]; voice = bool(aud is not None and (st.get("client") or {}).get("voice") is not False)
    if aud is not None:
        if not stop_voice: return 1, "the voice client is connected: Winlink needs the audio link (stop the voice client first)"
        if voice: audio_send(b"C", json.dumps({"act": "quit"}).encode())
        else: drop_audio(aud, "Winlink on the Pi started")
        for _ in range(30):
            if st["audio"] is None: break
            time.sleep(0.1)
        if st["audio"] is not None: drop_audio(st["audio"], "Winlink on the Pi started")
    if st["lsb"]: lsb_leave(True, "Winlink started")
    if st["cw"]: cw_leave(True, "Winlink started")
    rig.cmd("M PKTUSB 0")
    try: pc = wl_pat_config()
    except OSError as e: return 1, f"Pat's settings could not be written: {e}"
    here = os.path.dirname(os.path.abspath(__file__))
    cmds = {"audio": [sys.executable, "-u", os.path.join(here, "ft897-winlink-audio.py"), "--port", str(a.audio_port)],
            "xara": [wl_xara(), "--call", wlset["call"].split("-")[0], "--hf", str(wlset["bw"]), "--audio", "alsa", "--capture", LOOP_IN,
                     "--playback", LOOP_OUT, "--radio", "--ptt", f"rigctld:127.0.0.1:{a.cat_port}", "--drive-db", f"{wlset['drive']:g}",
                     "--addr", "127.0.0.1", "--kiss-port", "off", "--tx-buffer-ms", "120", "--state-dir", XARA_STATE],
            "pat": [pat_bin(), "--config", pc, "http", "--addr", f"{a.bind}:{a.pat_port}"]}
    if a.dummy and os.environ.get("FT897_WL_FAKE"): cmds["xara"] = [sys.executable, "-u", os.environ["FT897_WL_FAKE"], "xara"]; cmds["pat"] = [sys.executable, "-u", os.environ["FT897_WL_FAKE"], "pat", str(a.pat_port)]
    os.makedirs(XARA_STATE, exist_ok=True)
    with wl_lock:
        wl["err"] = ""; wl["stopping"] = False; wl["by"] = ip; wl["since"] = time.time(); wl["log"].clear()
        for name in ("audio", "xara", "pat"):
            try: pr = subprocess.Popen(cmds[name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                wl["err"] = f"{name} could not start: {e}"; break
            wl["procs"][name] = pr; threading.Thread(target=wl_reader, args=(name, pr), daemon=True).start()
    if wl["err"]: wl_stop(wl["err"]); return 1, wl["err"]
    log(f"Winlink on the Pi started by {label_of(ip)} ({wlset['call']}, VARA HF {wlset['bw']})")
    return 0, ""
def wl_stop(why=""):
    with wl_lock:
        if not wl["procs"]: return
        wl["stopping"] = True; procs = dict(wl["procs"])
    for name in ("pat", "xara", "audio"):            # xara first lets go of PTT; the bridge also un-keys when its CAT link closes
        pr = procs.get(name)
        if pr and pr.poll() is None:
            try: pr.terminate()
            except OSError: pass
    for pr in procs.values():
        try: pr.wait(timeout=4)
        except subprocess.TimeoutExpired:
            try: pr.kill()
            except OSError: pass
    with wl_lock: wl["procs"].clear(); wl["by"] = None
    log("Winlink on the Pi stopped" + (f" ({why})" if why else ""))
def wl_state():
    with wl_lock:
        on = bool(wl["procs"]); alive = {k: v.poll() is None for k, v in wl["procs"].items()}
        return {**wlset, "on": on, "alive": alive, "by": label_of(wl["by"]) if wl["by"] else None, "since": int(time.time() - wl["since"]) if on else 0,
                "err": wl["err"], "log": list(wl["log"])[-12:], "pat_port": a.pat_port, "missing": wl_missing()}
def wl_op(ip, j):
    op = j.get("op")
    if op == "set":
        if wl_running() and any(k in j for k in ("call", "bw", "drive")): return 1, "stop Winlink first"
        old = dict(wlset); wl_clean(j)
        if wlset != old: wl_save()
        if j.get("call") and wlset["call"] != str(j["call"]).strip().upper(): return 1, "that call sign does not look right"
        return 0, ""
    if op == "start": return wl_start(ip, j.get("stop_voice") is True)
    if op == "stop": threading.Thread(target=wl_stop, args=(f"by {label_of(ip)}",), daemon=True).start(); return 0, ""
    return 1, "unknown Winlink command"

# ---------------------------------------------------------------- APRS on the Pi: Dire Wolf (modem, digipeater, iGate) + this client
# Dire Wolf sits on the loopback card like xara and keys the radio through the bridge's CAT port, so every lock applies. Its network
# ports are off (they can't be limited to this Pi); the bridge talks KISS to it over a pseudo terminal only it can open.
APRS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aprs.json")
APRS_MSGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aprs-messages.json")
APRS_RUN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aprs-run") if a.dummy else os.path.expanduser("~/.local/state/ft897-aprs")
TOCALL = "APZYNR"                                  # experimental software id: yaesu-netradio
APDEF = {"call": "", "sym": "/-", "comment": "yaesu-netradio", "beacon": 30, "lat": None, "lon": None, "freq": 144_390_000,
         "path": "WIDE1-1,WIDE2-1", "mode": "FM", "show_is": True, "is_km": 100}
apset = dict(APDEF)
ap = {"procs": {}, "by": None, "since": 0.0, "log": collections.deque(maxlen=80), "err": "", "stopping": False, "fd": None, "pty": None,
      "net": None, "net_at": 0.0, "stations": {}, "pk": collections.deque(maxlen=300), "msgs": [], "seq": 1, "bln": {}, "is_on": False, "unread": 0,
      "echo": None}                                 # our own packet heard back: {"t", "via", "src"}
def ap_echo(p, src):
    """Our own packet came back: repeated by a digipeater (rf), or gated to APRS-IS (is). Proof it got out; not a station."""
    path = p.get("path") or []
    if src == "rf": via = [x.rstrip("*") for x in path if x.endswith("*") and not re.match(r"^WIDE\d|^TRACE|^RELAY", x)] or ["a digipeater"]
    else: via = [path[path.index(q) + 1] for q in path if q.upper() in ("QAR", "QAO", "QAS", "QAC") and path.index(q) + 1 < len(path)] or ["an iGate"]
    with ap_lock:
        ap["echo"] = {"t": time.time(), "via": ", ".join(via), "src": src}
        ap["pk"].append({"t": time.time(), "src": "echo", "line": aprs.tnc2(p)[:240], "kind": "echo", "via": ", ".join(via)})
ap_lock = threading.Lock()
def ap_clean(j):
    """Copies valid station settings from j. -> "" or what was wrong"""
    if not isinstance(j, dict): return "bad settings"
    bad = []
    if "call" in j:
        c = str(j["call"] or "").strip().upper()
        if c == "" or re.fullmatch(r"[A-Z0-9]{1,3}[0-9][A-Z]{1,3}(-([0-9]|1[0-5]))?", c): apset["call"] = c
        else: bad.append("call sign (e.g. W5PL-10)")
    if isinstance(j.get("sym"), str) and len(j["sym"]) == 2 and all(33 <= ord(x) <= 126 for x in j["sym"]) and j["sym"][0] in "/\\0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ": apset["sym"] = j["sym"]
    if "comment" in j: apset["comment"] = re.sub(r"[|~\r\n]", " ", str(j["comment"] or ""))[:40]
    if isinstance(j.get("beacon"), (int, float)) and not isinstance(j["beacon"], bool): apset["beacon"] = 0 if j["beacon"] <= 0 else int(max(10, min(120, j["beacon"])))
    if "grid" in j and str(j["grid"] or "").strip():
        try: apset["lat"], apset["lon"] = aprs.grid_to_latlon(str(j["grid"]))
        except ValueError: bad.append("grid square (e.g. DM79mp)")
    for k, lo, hi in (("lat", -90, 90), ("lon", -180, 180)):
        if k in j and j[k] is not None:
            try:
                v = float(j[k])
                if lo <= v <= hi: apset[k] = round(v, 5)
                else: bad.append(k)
            except (TypeError, ValueError): bad.append(k)
    if isinstance(j.get("freq"), (int, float)) and 144_000_000 <= j["freq"] <= 148_000_000 or isinstance(j.get("freq"), (int, float)) and 420_000_000 <= j["freq"] <= 450_000_000: apset["freq"] = int(j["freq"])
    elif "freq" in j: bad.append("frequency (2 m or 70 cm)")
    if "path" in j:
        p = str(j["path"] or "").upper().replace(" ", "")
        if p == "" or re.fullmatch(r"([A-Z0-9]{1,6}(-[0-9]{1,2})?)(,[A-Z0-9]{1,6}(-[0-9]{1,2})?){0,2}", p): apset["path"] = p
        else: bad.append("path (e.g. WIDE1-1,WIDE2-1)")
    if j.get("mode") in ("FM", "PKT"): apset["mode"] = j["mode"]
    if isinstance(j.get("show_is"), bool): apset["show_is"] = j["show_is"]
    if isinstance(j.get("is_km"), (int, float)) and not isinstance(j["is_km"], bool): apset["is_km"] = int(max(10, min(500, j["is_km"])))
    return ("check the " + ", ".join(bad)) if bad else ""
def ap_load():
    try:
        with open(APRS_FILE) as f: ap_clean(json.load(f))
    except (OSError, ValueError, TypeError): pass
    try:
        with open(APRS_MSGS) as f: j = json.load(f)
        ap["msgs"] = [m for m in j.get("msgs", []) if isinstance(m, dict)][-500:]; ap["seq"] = int(j.get("seq", 1))
    except (OSError, ValueError, TypeError): pass
def ap_save():
    try:
        with open(APRS_FILE + ".tmp", "w") as f: json.dump(apset, f)
        os.replace(APRS_FILE + ".tmp", APRS_FILE)
    except OSError as e: log(f"aprs.json not saved: {e}")
def ap_save_msgs():
    try:
        with ap_lock: j = {"msgs": ap["msgs"][-500:], "seq": ap["seq"]}
        with open(APRS_MSGS + ".tmp", "w") as f: json.dump(j, f)
        os.replace(APRS_MSGS + ".tmp", APRS_MSGS)
    except OSError as e: log(f"aprs-messages.json not saved: {e}")
def ap_running():
    with ap_lock: return bool(ap["procs"])
def ap_missing():
    if not shutil.which("direwolf"): return "Dire Wolf is not installed (sudo apt install direwolf)"
    if not a.dummy and not os.path.exists("/proc/asound/Loopback"): return "the loopback sound card is not there (pi/winlink-install.sh adds it)"
    return ""
def ap_conf():
    """Dire Wolf's configuration from the station settings (panel) and the digipeater / iGate settings (administration page)."""
    A = cfg["aprs"]; c = apset["call"]
    # (ADEVICE = LOOP_IN / LOOP_OUT in short form: Dire Wolf cuts device names at 30 characters)
    L = ["ADEVICE plughw:Loopback,1,0 plughw:Loopback,0,1", "ARATE 48000", "ACHANNELS 1", "CHANNEL 0", f"MYCALL {c}", "MODEM 1200",
         f"PTT RIG 2 localhost:{a.cat_port}", "TXDELAY 30", "TXTAIL 10", "AGWPORT 0", "KISSPORT 0", "DEDUPE 30"]
    al = [x for x in re.split(r"[ ,]+", A["aliases"].upper()) if re.fullmatch(r"[A-Z0-9]{1,6}(-[0-9]{1,2})?", x)]
    extra = "".join(f"|^{x}$" for x in al)
    if A["digi"] == "fillin": L.append(f"DIGIPEAT 0 0 ^WIDE1-1${extra} ^WIDE1-1$")
    elif A["digi"] == "wide": L.append(f"DIGIPEAT 0 0 ^WIDE[3-7]-[1-7]$|^TEST${extra} ^WIDE[12]-[12]$ TRACE")
    if A["igate"] != "off" and acc.aprs_pass():
        L += [f"IGSERVER {A['server']}", f"IGLOGIN {c} {acc.aprs_pass()}"]
        if A["igate"] == "rxtx": L += ["IGTXVIA 0 WIDE1-1", "IGTXLIMIT 6 10"]
    if apset["beacon"] and apset["lat"] is not None:
        cm = apset["comment"].replace('"', "'")
        L.append(f'PBEACON delay=1 every={apset["beacon"]} symbol="{apset["sym"]}" lat={apset["lat"]} long={apset["lon"]} comment="{cm}"' + (f" via={apset['path']}" if apset["path"] else ""))
    return "\n".join(L) + "\n"
def ap_reader(name, pr):
    for line in iter(pr.stdout.readline, b""):
        t = line.decode(errors="replace").rstrip()
        if not t: continue
        m = re.search(r"Virtual KISS TNC is available on (/dev/pts/\d+)", t)
        if m and name == "direwolf": ap["pty"] = m[1]
        ap["log"].append(f"{time.strftime('%H:%M:%S')} {name}: {t[:200]}")
    rc = pr.wait()
    with ap_lock: mine = ap["procs"].get(name) is pr; stopping = ap["stopping"]
    if mine and not stopping:
        ap["err"] = f"{name} stopped (exit {rc}): see its log"; log(f"APRS: {name} stopped (exit {rc}); stopping the rest")
        threading.Thread(target=ap_stop, args=(f"{name} stopped",), daemon=True).start()
def ap_station(p, src):
    """Remembers a station heard on the radio ("rf") or from the internet ("is")."""
    who = p.get("name") if p.get("kind") in ("object", "item") else p.get("src")
    if not who: return
    now = time.time()
    with ap_lock:
        s = ap["stations"].setdefault(who, {"call": who, "first": now, "n": 0, "rf_last": 0.0})
        s["last"] = now; s["n"] += 1; s["src"] = src
        if src == "rf": s["rf_last"] = now; s["path"] = ",".join(p.get("path") or [])
        if "lat" in p and "lon" in p:                          # a short track, for stations that move
            tr = s.setdefault("track", []); pt = [round(p["lat"], 5), round(p["lon"], 5), int(now)]
            if not tr or abs(tr[-1][0] - pt[0]) + abs(tr[-1][1] - pt[1]) > 0.0003: tr.append(pt); del tr[:-30]
        if p.get("wx"): s["wx"] = p["wx"]; s["wx_t"] = int(now)
        if p.get("wx_unit"): s["wx_unit"] = p["wx_unit"]
        for k in ("lat", "lon", "comment", "speed", "course", "kind", "text", "alt_ft"):
            if k in p and p[k] not in (None, ""): s[k] = p[k]
        if "sym" in p: s["sym"] = (p.get("table") or "/") + p["sym"]; s.pop("sym_bad", None)
        elif "sym_bad" in p: s["sym_bad"] = p["sym_bad"]; s.pop("sym", None)
        if p.get("kind") in ("object", "item"): s["by"] = p.get("src"); s["obj"] = True
        if len(ap["stations"]) > 2000:
            for k in sorted(ap["stations"], key=lambda k: ap["stations"][k]["last"])[:200]: del ap["stations"][k]
def ap_handle(p, src):
    """One packet in (decoded). src: "rf" heard on the radio, "is" from APRS-IS (the internet)."""
    p = aprs.parse(p); me = apset["call"]
    if me and p.get("src", "").upper() == me.upper(): return ap_echo(p, src)
    with ap_lock: ap["pk"].append({"t": time.time(), "src": src, "line": aprs.tnc2(p)[:240], "kind": p["kind"]})
    if p["kind"] in ("position", "object", "item", "weather", "status") or "lat" in p: ap_station(p, src)
    else: ap_station({"src": p["src"]}, src)
    if p["kind"] in ("message", "ack", "rej") and me and p.get("to", "").upper() == me:
        if p["kind"] == "message":
            with ap_lock:
                dup = any(m["dir"] == "in" and m["from"] == p["src"] and m.get("msgno") == p.get("msgno") and p.get("msgno") and time.time() - m["t"] < 1800 for m in ap["msgs"])
                if not dup: ap["msgs"].append({"id": os.urandom(4).hex(), "t": time.time(), "dir": "in", "from": p["src"], "to": me, "text": p.get("text", ""),
                                               "msgno": p.get("msgno"), "src": src, "state": "new"}); ap["unread"] += 1
            if p.get("msgno"): ap_send(aprs.ack_info(p["src"], p["msgno"]), log_as="ack")       # ack every copy: the first ack may have been lost
            if not dup: ap_save_msgs(); log(f"APRS message from {p['src']} ({src})")
        else:
            with ap_lock:
                for m in ap["msgs"]:
                    if m["dir"] == "out" and m.get("msgno") == p.get("msgno") and m["to"].upper() == p["src"].upper() and m["state"] in ("sent", "sending"):
                        m["state"] = "acked" if p["kind"] == "ack" else "rejected"; m["done"] = time.time()
            ap_save_msgs()
    if p["kind"] == "bulletin":
        with ap_lock: ap["bln"][f"{p['src']}:{p.get('to')}"] = {"t": time.time(), "from": p["src"], "id": p.get("to"), "text": p.get("text", ""), "src": src}
def ap_send(info, log_as="send"):
    """One frame out through Dire Wolf (it keys the radio)."""
    fd = ap["fd"]; c = apset["call"]
    if fd is None or not c: return "APRS is not running"
    try:
        path = [x for x in apset["path"].split(",") if x]
        f = aprs.ax25_encode(c, TOCALL, path, info); os.write(fd, aprs.kiss_wrap(f))
    except (OSError, ValueError) as e: return f"could not send: {e}"
    with ap_lock: ap["pk"].append({"t": time.time(), "src": "tx", "line": f"{c}>{TOCALL}" + "".join("," + x for x in path) + ":" + info, "kind": log_as})
    return ""
def ap_kiss_loop():
    """Frames from Dire Wolf (everything it hears) -> ap_handle; also resends messages that were not acknowledged."""
    rd = aprs.KissReader(); last = 0.0
    while True:
        if not ap_running(): time.sleep(0.5); continue
        if ap["fd"] is None:
            if ap["pty"] and os.path.exists(ap["pty"]):
                try:
                    import termios, tty
                    fd = os.open(ap["pty"], os.O_RDWR | os.O_NOCTTY); tty.setraw(fd); ap["fd"] = fd; log(f"APRS: talking to Dire Wolf on {ap['pty']}")
                except OSError as e: ap["err"] = f"Dire Wolf's KISS port: {e}"; time.sleep(2); continue
            else: time.sleep(0.3); continue
        import select
        r, _, _ = select.select([ap["fd"]], [], [], 1.0)
        if r:
            try: data = os.read(ap["fd"], 4096)
            except OSError: data = b""
            if not data:
                try: os.close(ap["fd"])
                except OSError: pass
                ap["fd"] = None; continue
            for f in rd.feed(data):
                p = aprs.ax25_decode(f)
                if p: ap_handle(p, "rf")
        if time.time() - last > 1: last = time.time(); ap_retry()
RETRY = (30, 60, 120, 240)                         # seconds before each resend of an unacknowledged message
def ap_retry():
    now = time.time(); changed = False
    with ap_lock: out = [m for m in ap["msgs"] if m["dir"] == "out" and m["state"] == "sent" and m.get("msgno")]
    for m in out:
        if now - m["at"] < RETRY[min(m["tries"] - 1, len(RETRY) - 1)]: continue
        if m["tries"] > len(RETRY): m["state"] = "failed"; changed = True; continue
        if not ap_send(aprs.message_info(m["to"], m["text"], m["msgno"]), "message"): m["tries"] += 1; m["at"] = now; changed = True
    if changed: ap_save_msgs()
def ap_net_check():
    try: socket.create_connection(("rotate.aprs2.net", 14580), timeout=4).close(); ok = True
    except OSError: ok = False
    if ok != ap["net"]: log(f"APRS: internet {'available' if ok else 'not available'}: internet features {'on' if ok else 'off'}")
    ap["net"] = ok; ap["net_at"] = time.time()
def ap_is_loop():
    """Stations from APRS-IS (the internet) for the list and map, read only, when allowed and there is internet. Never sends."""
    while True:
        if not (ap_running() and apset["show_is"] and apset["call"] and apset["lat"] is not None):
            ap["is_on"] = False; time.sleep(2); continue
        if time.time() - ap["net_at"] > 300: ap_net_check()
        if not ap["net"]: ap["is_on"] = False; time.sleep(10); continue
        try:
            s = socket.create_connection(("rotate.aprs2.net", 14580), timeout=10); s.settimeout(90)
            s.sendall(f"user {apset['call']} pass -1 vers yaesu-netradio {RELEASE} filter r/{apset['lat']}/{apset['lon']}/{apset['is_km']}\r\n".encode())
            ap["is_on"] = True; buf = b""; want = (apset["lat"], apset["lon"], apset["is_km"], apset["call"])
            while ap_running() and apset["show_is"] and want == (apset["lat"], apset["lon"], apset["is_km"], apset["call"]):
                d = s.recv(8192)
                if not d: break
                buf += d
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1); t = line.decode("latin-1").strip()
                    if t and not t.startswith("#"):
                        p = aprs.parse_tnc2(t)
                        if p: ap_handle(p, "is")
            s.close()
        except OSError: ap["net_at"] = 0
        ap["is_on"] = False; time.sleep(5)
def ap_start(ip, stop_voice=False):
    if ap_running(): return 0, ""
    if wl_running(): return 1, "stop Winlink first: both need the audio link"
    why = ap_missing()
    if why: return 1, why
    if not apset["call"]: return 1, "enter your call sign (with an SSID, e.g. W5PL-10) first"
    if st["keyed"]: return 1, "transmitting"
    if cfg["aprs"]["igate"] != "off" and not acc.aprs_pass(): return 1, "the iGate is on but no APRS-IS passcode is set (administration page)"
    with lock: aud = st["audio"]; voice = bool(aud is not None and (st.get("client") or {}).get("voice") is not False)
    if aud is not None:
        if not stop_voice: return 1, "the voice client is connected: APRS needs the audio link (stop the voice client first)"
        if voice: audio_send(b"C", json.dumps({"act": "quit"}).encode())
        else: drop_audio(aud, "APRS started")
        for _ in range(30):
            if st["audio"] is None: break
            time.sleep(0.1)
        if st["audio"] is not None: drop_audio(st["audio"], "APRS started")
    if st["lsb"]: lsb_leave(False, "APRS started")
    if st["cw"]: cw_leave(False, "APRS started")
    for c in (f"F {apset['freq']}", "M FM 0"):
        r = rig.cmd(c)[0]
        if r != 0: return r, f"the radio refused \"{c}\" (RPRT {r})"
    os.makedirs(APRS_RUN, exist_ok=True); conf = os.path.join(APRS_RUN, "direwolf.conf")
    with os.fdopen(os.open(conf + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: f.write(ap_conf())
    os.replace(conf + ".tmp", conf)
    here = os.path.dirname(os.path.abspath(__file__))
    cmds = {"audio": [sys.executable, "-u", os.path.join(here, "ft897-winlink-audio.py"), "--port", str(a.audio_port), "--name", "APRS on the Pi (Dire Wolf)"],
            "direwolf": ["direwolf", "-c", conf, "-p", "-t", "0", "-q", "d"]}
    ap_net_check()
    with ap_lock:
        ap.update(err="", stopping=False, by=ip, since=time.time(), pty=None, fd=None); ap["log"].clear()
        for name in ("audio", "direwolf"):
            try: pr = subprocess.Popen(cmds[name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, cwd=APRS_RUN)
            except OSError as e: ap["err"] = f"{name} could not start: {e}"; break
            ap["procs"][name] = pr; threading.Thread(target=ap_reader, args=(name, pr), daemon=True).start()
    if ap["err"]: ap_stop(ap["err"]); return 1, ap["err"]
    A = cfg["aprs"]
    log(f"APRS on the Pi started by {label_of(ip)} ({apset['call']}, {apset['freq'] / 1e6:.3f} MHz, digipeater {A['digi']}, iGate {A['igate']}, internet {'yes' if ap['net'] else 'no'})")
    return 0, ""
def ap_stop(why=""):
    with ap_lock:
        if not ap["procs"]: return
        ap["stopping"] = True; procs = dict(ap["procs"])
    for name in ("direwolf", "audio"):
        pr = procs.get(name)
        if pr and pr.poll() is None:
            try: pr.terminate()
            except OSError: pass
    for pr in procs.values():
        try: pr.wait(timeout=4)
        except subprocess.TimeoutExpired:
            try: pr.kill()
            except OSError: pass
    if ap["fd"] is not None:
        try: os.close(ap["fd"])
        except OSError: pass
    with ap_lock: ap["procs"].clear(); ap["by"] = None; ap["fd"] = None; ap["pty"] = None
    log("APRS on the Pi stopped" + (f" ({why})" if why else ""))
# ---------------------------------------------------------------- offline maps (ft897_maps.py): areas around a position, as MBTiles
MAPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maps") if a.dummy else os.path.expanduser("~/.local/share/yaesu-netradio/maps")
mapstore = maps.Store(MAPS_DIR, f"yaesu-netradio/{RELEASE} (offline maps for APRS; https://github.com/jwheeler188/yaesu-netradio)",
                      os.environ.get("FT897_TILE_URL", "") if a.dummy else "")
def maps_info():
    return {"sources": {k: {"name": v["name"], "max": v["max"], "credit": v["credit"]} for k, v in maps.SOURCES.items()}, "areas": mapstore.areas(),
            "job": mapstore.status(), "limit_mb": cfg["maps_max_mb"], "free_mb": int(maps.shutil_free(MAPS_DIR) / 1e6)}
def maps_op(ip, j):
    op = j.get("op")
    if op == "cancel": mapstore.cancel = True; return 0, ""
    if op == "delete":
        e = mapstore.delete(str(j.get("id") or "")); return (1, e) if e else (0, "")
    if op in ("estimate", "download"):
        try:
            lat, lon = float(j["lat"]), float(j["lon"]); r = round(float(j["radius_km"]), 1); z = int(j["maxz"]); d = round(float(j.get("detail_km") or 0), 1); src = str(j["source"])
        except (KeyError, TypeError, ValueError): return 1, "give a position, radius and zoom"
        if src not in maps.SOURCES: return 1, "unknown map source"
        if not (-85 <= lat <= 85 and -180 <= lon <= 180): return 1, "position out of range"
        if not (1 <= r <= 800 and 0 <= d <= 200 and 8 <= z <= maps.SOURCES[src]["max"]): return 1, "bad radius, detail or zoom"
        if op == "estimate":
            e = maps.estimate(src, lat, lon, r, z, d); e.update(limit_mb=cfg["maps_max_mb"], free_mb=int(maps.shutil_free(MAPS_DIR) / 1e6)); return 0, e
        name = re.sub(r"[^\w ,.-]", "", str(j.get("name") or ""))[:40].strip() or f"{r} km around {lat:.2f},{lon:.2f}"
        e, aid = mapstore.start(name, src, lat, lon, r, z, d, cfg["maps_max_mb"])
        if e: return 1, e
        log(f"offline maps: downloading \"{name}\" ({src}, {r} km, zoom {z}) for {label_of(ip)}"); return 0, {"id": aid}
    return 1, "unknown maps command"

def ap_summary():
    by = ap["by"]; by = label_of(by) if by else None              # (not under ap_lock: label_of takes the main lock)
    with ap_lock:
        on = bool(ap["procs"])
        return {"on": on, "alive": {k: v.poll() is None for k, v in ap["procs"].items()}, "err": ap["err"], "call": apset["call"],
                "net": ap["net"], "is_on": ap["is_on"], "heard": len(ap["stations"]), "unread": ap["unread"], "missing": ap_missing(),
                "by": by, "ready": bool(apset["call"]) and not ap_missing()}
def ap_full():
    """Everything the APRS tab shows (GET /api/aprs, polled only while the tab is open)."""
    now = time.time(); me = (apset["lat"], apset["lon"])
    with ap_lock:
        S = []
        for s in sorted(ap["stations"].values(), key=lambda s: -s["last"])[:300]:
            x = dict(s); x["age"] = int(now - s["last"])
            if len(s.get("track") or []) < 2: x.pop("track", None)
            else: x["track"] = [t[:2] for t in s["track"]]
            x["rf"] = now - s["rf_last"] < 1800       # heard on the radio in the last 30 min
            if me[0] is not None and "lat" in s: x["km"], x["brg"] = aprs.dist_bearing(me[0], me[1], s["lat"], s["lon"])
            S.append(x)
        j = {"settings": dict(apset), "stations": S, "packets": list(ap["pk"])[-150:], "msgs": ap["msgs"][-300:], "bulletins": sorted(ap["bln"].values(), key=lambda b: -b["t"])[:50],
             "log": list(ap["log"])[-15:], "digi": cfg["aprs"]["digi"], "igate": cfg["aprs"]["igate"], "now": now,
             "echo": dict(ap["echo"], age=int(now - ap["echo"]["t"])) if ap["echo"] else None}
    j.update(ap_summary()); return j
def ap_op(ip, j):
    op = j.get("op")
    if op == "set":
        old = dict(apset); err = ap_clean(j)
        if err: apset.update(old); return 1, err
        if apset != old:
            ap_save()
            if ap_running() and any(apset[k] != old[k] for k in ("call", "sym", "comment", "beacon", "lat", "lon", "path", "freq")):
                return 0, "saved: stop and start APRS to use them"
        return 0, ""
    if op == "start": return ap_start(ip, j.get("stop_voice") is True)
    if op == "stop": threading.Thread(target=ap_stop, args=(f"by {label_of(ip)}",), daemon=True).start(); return 0, ""
    if op == "read":
        with ap_lock:
            for m in ap["msgs"]:
                if m["dir"] == "in" and m["state"] == "new": m["state"] = "read"
            ap["unread"] = 0
        ap_save_msgs(); return 0, ""
    if op == "inject" and a.dummy:                 # test mode only: packets as if heard (no Dire Wolf, no sound card)
        for line in (j.get("lines") or [])[:50]:
            p = aprs.parse_tnc2(str(line))
            if p: ap_handle(p, "is" if j.get("src") == "is" else "rf")
        return 0, ""
    if op == "clear":                              # the station list and packet log (messages stay)
        with ap_lock: ap["stations"].clear(); ap["pk"].clear(); ap["bln"].clear()
        return 0, ""
    if not ap_running(): return 1, "start APRS first"
    if op == "beacon":
        if apset["lat"] is None: return 1, "set your position first"
        e = ap_send(aprs.position_info(apset["lat"], apset["lon"], apset["sym"][0], apset["sym"][1], apset["comment"]), "position")
        return (1, e) if e else (0, "")
    if op == "send":
        to = str(j.get("to") or "").strip().upper(); text = str(j.get("text") or "").strip()
        if not re.fullmatch(r"[A-Z0-9]{1,6}(-[0-9]{1,2})?", to): return 1, "check the call sign to send to"
        if not text: return 1, "nothing to send"
        with ap_lock: no = str(ap["seq"]); ap["seq"] = ap["seq"] % 99999 + 1
        m = {"id": os.urandom(4).hex(), "t": time.time(), "dir": "out", "from": apset["call"], "to": to, "text": text[:67], "msgno": no, "state": "sent", "tries": 1, "at": time.time()}
        e = ap_send(aprs.message_info(to, text, no), "message")
        if e: return 1, e
        with ap_lock: ap["msgs"].append(m)
        ap_save_msgs(); return 0, ""
    return 1, "unknown APRS command"

# ---------------------------------------------------------------- rig poller (for the web panel)
def fake_dead():
    try:
        with open(FAKE) as f: return bool(json.load(f).get("dead"))
    except (OSError, ValueError): return False
class Rig:
    """One persistent rigctld connection (extended protocol) for the panel's polls and commands."""
    def __init__(self): self.s = None; self.l = threading.Lock()
    def cmd(self, line, raw=False):
        if line[:1] in "ROCUD" and line[1:2] == " " or line.startswith("\\set_dcs_sql"): tracked = True
        else: tracked = False
        if FAKE and line == "f" and (st["keyed"] or time.time() - st["unkey_at"] < 0.5):
            try:
                with open(FAKE) as f: q = json.load(f).get("txfreq")
            except (OSError, ValueError): q = None
            if q: return (0, {"Frequency": str(q)})
        if self is rig and KNOWN and RADIO.key == "ft891" and not raw:
            r = radio.line(line)
            if r is not None: return r
        if FAKE and fake_dead(): return b"" if raw else (0, {}) if line[1:2] == " " and line[0].isupper() and line[0] != "W" else (-5, {})
        with self.l:
            for attempt in (1, 2):
                try:
                    if self.s is None:
                        self.s = socket.create_connection(("127.0.0.1", a.rigctld_port), timeout=3)
                    self.s.sendall(b"+" + line.encode() + b"\n")
                    out = b""
                    while b"RPRT " not in out or not out.endswith(b"\n"):
                        d = self.s.recv(4096)
                        if not d: raise OSError("rigctld closed")
                        out += d
                    if raw: return out
                    lines = out.decode(errors="replace").strip().split("\n")
                    code = int(lines[-1].split()[1])
                    vals = {}
                    for ln in lines[1:-1]:
                        k, sep, v = ln.partition(": ")
                        vals[k if sep else "value"] = v if sep else k
                    if tracked and code == 0: rpt_track(line)
                    return code, vals
                except (OSError, ValueError, IndexError):
                    try: self.s.close()
                    except Exception: pass
                    self.s = None
                    if attempt == 2: return b"" if raw else (-99, {})
rig = Rig()
rig_meters = Rig()      # its own connection, so a slow meter read never holds up PTT
radio = RADIO(rig_meters); radio.dcs = []       # (the DCS list comes further down)
KNOWN = RADIO.key != "other" and not a.dummy    # a real radio whose status can be read directly

def tx_meters():
    """The radio's meters while it transmits: po, alc, swr 0..1 and the high-SWR flag ({} = it does not transmit, or no answer)."""
    if FAKE:
        try:
            with open(FAKE) as f: j = json.load(f)
        except (OSError, ValueError): return {}
        return {"po": 0.5, "alc": 0.2, "swr": j.get("swr", 0) / 15, "hi_swr": bool(j.get("hi_swr"))}
    r = radio.meters(lambda: st["keyed"])
    if "raw" in r: st["tx_meter"].append((r["po"], r.get("alc", 0), r.get("swr", 0)))
    return r

def swr_guard(r):
    """High-SWR shutdown: un-keys the radio and locks the band until someone resets it. Two signs: the radio's own high-SWR
    flag, and its SWR meter (0-15) at or above the set step, for several readings in a row (about 10 a second)."""
    s = cfg["swr"]
    if not st["keyed"]: return
    if "hi_swr" in r: st["meter_ok"] = True
    elif st["meter_ok"] is None and time.time() - st["key_ts"] > 2:
        st["meter_ok"] = False; log("the radio's meters could not be read while transmitting: no high-SWR protection")
    if not s["on"]: return
    step = round(r.get("swr", 0) * 15); flag = bool(s["flag"] and r.get("hi_swr"))
    st["swr_n"] = st["swr_n"] + 1 if flag or step >= s["level"] else 0
    if st["swr_n"] < s["reads"]: return
    hz = st["tx_hz"] or vhz(st["rig"].get("freq") or 0)
    why = "the radio's high-SWR warning" if flag else f"SWR meter at {step}/15" + (f" (about {r['swr_ratio']}:1)" if "swr_ratio" in r else "") + f", limit {s['level']}"
    st["safety"] = {"why": f"HIGH SWR on {acc.mhz(hz)}: {why}. Transmitting on {acc.band_key(hz)} is locked", "at": time.time()}
    with lock: st["swr_n"] = 0
    acc.trip(hz, why); safety_unkey(st["safety"]["why"])

def tx_status():
    """From the radio itself: "rx", "tx", or None = no answer. None also for radios this is not known for."""
    if FAKE:
        try:
            with open(FAKE) as f: j = json.load(f)
        except (OSError, ValueError): j = {}
        return None if j.get("dead") else "tx" if j.get("stuck") else "rx"
    return radio.tx_status()
def verify_rx(n):
    """After every un-key: did the radio really go back to receive? If not: un-key again, then alarm and lock transmitting.
    (A radio that hangs while keyed shows nothing else: no audio, no carrier on SSB, only heat.)"""
    if not KNOWN and not FAKE: return
    time.sleep(0.35); s = None; t0 = time.time()
    for i in range(6):
        if i > 1 and time.time() - t0 > 5: break                   # (a silent radio makes every question slow)
        if st["keyed"] or st["unkeys"] != n: return            # keyed again, or a newer un-key is being checked
        s = tx_status()
        if s == "rx": return
        if i: 
            try: rig_cmd("T 0")
            except OSError: pass
            if KNOWN: radio.release()
        time.sleep(0.5)
    if st["keyed"] or st["unkeys"] != n: return
    why = ("THE RADIO IS STILL TRANSMITTING after it was un-keyed" if s == "tx" else "THE RADIO STOPPED ANSWERING when it was un-keyed, and may still be transmitting")
    with lock:
        st["stuck"] = {"why": why, "at": time.time()}
        if s is None: st["cat_dead"] = True; st["cat_fail"] = 2
    log("SAFETY: " + why + ": transmitting is locked until it answers and is in receive. Check the radio, or cut its power")
    if s == "tx" and rig_proc and rig_proc.poll() is None and not a.dummy:
        log("SAFETY: closing the serial port to release PTT"); rig_proc.terminate()

def alive():
    """Does the radio itself answer? Read raw: Hamlib 4.5.5 hands out the last frequency and mode it knows even when the radio is off.
    Not while transmitting."""
    if not KNOWN: return not (FAKE and fake_dead())
    return radio.alive()
def alive_check():
    if st["keyed"]: return
    ok = alive(); st["alive_at"] = time.time()
    if ok and st["stuck"] and not st["keyed"]:
        s = tx_status()
        if s == "tx":                                  # it answers again and still transmits: un-key it at once
            log("SAFETY: the radio answers and is still transmitting: un-keying it again")
            try: rig_cmd("T 0")
            except OSError: pass
            if KNOWN: radio.release()
            time.sleep(0.3); s = tx_status()
        if s == "rx" and not st["keyed"]: st["stuck"] = None; log("the radio is in receive again: transmitting is unlocked")
    if st["keyed"]: return
    st["cat_fail"] = 0 if ok else st["cat_fail"] + 1
    if st["cat_fail"] == 2 and not st["cat_dead"]: st["cat_dead"] = True; log("the radio does not answer CAT any more: is it switched off? (power, APO timer, CAT cable)")
    elif ok and st["cat_dead"]: st["cat_dead"] = False; log("the radio answers again")

def poller():
    """Reads the radio for the web panel, only while a panel is open (WSJT-X etc. poll it as well), and always while
    it transmits, for the high-SWR shutdown and the transmit locks.
    The CAT data leaks faint ticks into the receive audio (shared Digirig cable), so while someone is listening it reads
    only the frequency and mode, every 10 s. The panel's "Live S-meter" box brings back 2 reads a second.
    Full speed while transmitting (meters, and no receive audio to disturb) and for 1.5 s after a panel command."""
    n = kn = 0; key_ts = 0
    while True:
        if not st["keyed"] and not st["stuck"] and time.time() - st["panel_seen"] > 5 or not st["rig_ok"]:
            time.sleep(0.05); continue
        t0 = time.time(); r = {}
        if st["keyed"]:
            kn = kn + 1 if st["key_ts"] == key_ts else 0; key_ts = st["key_ts"]
        quiet = (RADIO.ticks or not st["smeter_live"]) and (st["audio"] is not None or taps) and not st["keyed"] and time.time() - st.get("cmd_at", 0) > 1.5
        silent = quiet and st["audio"] is not None and not st.get("smeter_live")    # keep the CAT line quiet
        if st["keyed"] and kn % 4 != 1: pass       # transmitting: mostly the meters
        elif not quiet or silent or st["audio"] is None or n % 20 == 0:
            c, v = rig.cmd("f")
            if c == 0:
                r["freq"] = int(float(v.get("Frequency", 0))); r["freq_at"] = t0
                if st["keyed"] and not st["rig"].get("split") and tx_reason(tx_freq(vhz(r["freq"]), st["rig"].get("mode"))): st["tx_hz"] = tx_freq(vhz(r["freq"]), st["rig"].get("mode"))     # dial turned: the watchdog un-keys
            c, v = rig.cmd("m")
            if c == 0:
                r["mode"] = v.get("Mode"); r["passband"] = v.get("Passband")
                why = acc.mode_reason("DLSB" if st["lsb"] else "DCW" if st["cw"] else r["mode"])          # (the mode just read, not the last one)
                if st["keyed"] and why: st["tx_mode_why"] = why                 # the watchdog un-keys
                if st["lsb"] and r["mode"] != "PKTUSB" and not st["keyed"] and time.time() - st["lsb_at"] > 2: lsb_set(False, f"the radio is in {r['mode']}")
                if st["cw"] and r["mode"] != "PKTUSB" and not st["keyed"] and time.time() - st["cw_at"] > 2: cw_set(0, f"the radio is in {r['mode']}")
        if not st["keyed"] and time.time() - st["alive_at"] > (8 if not st["cat_fail"] and not st["stuck"] else 1.5): alive_check()
        if silent or quiet and st["audio"] is not None: r["ptt"] = False      # live S-meter: only the meter itself
        elif st["keyed"]: r["ptt"] = True              # (tx_meters asks the radio itself whether it transmits)
        else:
            c, v = rig.cmd("t")
            if c == 0: r["ptt"] = v.get("PTT") == "1"
        if silent: pass
        elif r.get("ptt"):
            m = tx_meters(); r.update(m); swr_guard(m)
        else:
            c, v = rig.cmd("l RAWSTR")                   # 0..15 on the FT-897 (S0..S9+60)
            if c == 0:
                try: r["smeter"] = radio.smeter(int(float(v.get("value", 0))))
                except ValueError: pass
        if n % ((20 if st["audio"] is not None else 10) if quiet else 4) == 0 and not st["keyed"] and not silent:
            c, v = rig.cmd("v")
            if c == 0: r["vfo"] = v.get("VFO")
            c, v = rig.cmd("s")
            if c == 0: r["split"] = v.get("Split") == "1"; r["tx_vfo"] = st["tx_vfo"] = v.get("TX VFO")
            if r.get("vfo") and r.get("freq"): st["vfo_freq"][r["vfo"]] = r["freq"]
            c, v = rig.cmd("\\get_dcd")
            if c == 0: r["busy"] = v.get("DCD") == "1"
            if KNOWN and RADIO.caps["reports"]:             # the FT-891 tells more than the FT-897
                rpt_read(); st["rit"] = radio.info.get("rit", st["rit"])
                w = radio.power()
                if w: r["power_w"] = w
        n += 1
        r["ts"] = time.time()
        with lock: st["rig"].update(r)
        if quiet:                                  # wait 10 s (0.5 s), less if the radio is keyed or a panel command comes in
            while time.time() - t0 < (1.6 if st["stuck"] else 10 if silent else 0.5) and not (silent and st.get("smeter_live")) and not st["keyed"] and time.time() - st.get("cmd_at", 0) > 1.5: time.sleep(0.02)
        else: time.sleep(max(0.03, 0.2 - (time.time() - t0)))

# ---------------------------------------------------------------- takeover
def take_control(ip, label):
    with lock:
        st["owner"] = ip; st["owner_name"] = label; st["owner_seen"] = time.time()
        kb = st["keyed_by"] or ""; keyed = st["keyed"]
        aud = st["audio"] if st["audio_ip"] != ip and not (st["audio_ip"] == "127.0.0.1" and ip in (wl["by"], ap["by"])) else None
        cats = [v for v in st["cat_clients"].values() if v["ip"] not in (ip, "127.0.0.1")]
    log(f"TAKE CONTROL by {label}: closing {len(cats)} CAT connection(s)" + (" and the audio link" if aud else ""))
    if wl_running() and wl["by"] != ip: wl_stop(f"radio taken over by {label}")
    if ap_running() and ap["by"] != ip: ap_stop(f"radio taken over by {label}")
    if cwq["ip"] and cwq["ip"] != ip:                  # the CW keyer's queue belonged to someone else
        with cw_lock: cwq["text"].clear(); cwq["played"].clear()
        cwtl.clear(time.time())
    if keyed and not (kb.startswith(ip + ":") or kb in ("web:" + ip, "cw:" + ip)):
        safety_unkey(f"taken over by {label}")
    if aud is not None:
        try:
            with audio_send_lock: send_frame(aud, b"B", f"radio taken over by {label}".encode())
        except OSError: pass
        drop_audio(aud, f"taken over by {label}")
    for v in cats:
        for sk in (v["sock"], v["up"]):
            try: sk.shutdown(socket.SHUT_RDWR)
            except OSError: pass

def label_of(ip):
    with lock: n = (st["panels"].get(ip) or {}).get("name") or host_name(ip)
    return n if n == ip else f"{n} ({ip})"

def in_use_by(ip):
    """The other computer that is using the radio now (None = nobody): the one in control, or else whoever is
    transmitting, has the audio link, or has an app on the CAT port."""
    with lock:
        if st["owner"]: return st["owner"] if st["owner"] != ip else None
        here = lambda k: wl["by"] or ap["by"] or k if k == "127.0.0.1" else k          # Winlink / APRS on the Pi: whoever started it
        kb = st["keyed_by"] or ""
        if st["keyed"]:
            k = here(kb[4:] if kb.startswith("web:") else kb[3:] if kb.startswith("cw:") else kb.rsplit(":", 1)[0])
            if k != ip: return k
        if st["audio"] is not None and here(st["audio_ip"]) != ip and time.time() - st["last_frame"] < 5: return here(st["audio_ip"])
        return next((v["ip"] for v in st["cat_clients"].values() if v["ip"] not in (ip, "127.0.0.1")), None)

def ask_control(ip, name):
    """Take control: at once if nobody else is using the radio, else the user is asked. -> (error, state)"""
    with lock:
        other = in_use_by(ip); q = st["req"]
        if other is not None and q and q["ip"] != ip: return f"{q['name']} has already asked for control", ""
        if other is not None and q: return "", "waiting"
        no = st["answers"].get(ip) if other is not None else None
        if no and not no["yes"] and time.time() - no["at"] < 60: return no["why"] + f". Ask again in {60 - int(time.time() - no['at'])} s", ""
        st["answers"].pop(ip, None)
        if other is not None:
            st["req"] = {"ip": ip, "name": label_of(ip), "to": other, "at": time.time(), "yes": None}
            log(f"{st['req']['name']} asks {label_of(other)} for control")
            return "", "waiting"
    take_control(ip, label_of(ip)); return "", "granted"

def answer(ip, yes):
    with lock:
        q = st["req"]
        if not q or q["to"] != ip: return "there is no request to answer"
        q["yes"] = bool(yes)
        if not yes: end_request(f"{label_of(ip)} is keeping the radio")
    return ""

def end_request(why, yes=False):
    with lock:
        q = st["req"]; st["req"] = None
        if q: st["answers"][q["ip"]] = {"yes": yes, "why": why, "at": time.time()}; log(f"request by {q['name']}: {why}")

def request_step():
    """A pending request: granted when the user agrees or does not answer in time, but never in the middle of a transmission."""
    with lock:
        q = st["req"]
        if not q: return
        now = time.time(); p = st["panels"].get(q["ip"])
        if not p or now - p["seen"] > 10: return end_request("the panel that asked was closed")
        other = in_use_by(q["ip"])
        if other is None: q["yes"] = True
        elif other != q["to"]: q.update(to=other, at=now, yes=None); return       # someone else has it now: ask them
        if q["yes"] is None:
            if now - q["at"] < cfg["answer_s"]: return
            if cfg["no_answer"] == "refuse": return end_request("no answer")
        if st["keyed"]: return
        ip = q["ip"]; end_request("granted" if q["yes"] else "granted: no answer", True)
    take_control(ip, label_of(ip))                                   # (outside the lock: it talks to other computers)

def release_control(ip):
    with lock:
        if st["owner"] is None: return
        log(f"control released by {st['owner_name']}"); st["owner"] = st["owner_name"] = None

# ---------------------------------------------------------------- web panel
MODES = RADIO.modes
if RADIO.key == "ft891": acc.MODE_NAME.update(PKTUSB="DATA-U", PKTLSB="DATA-L", FMN="FM-N")
def cur_mode(): return "DLSB" if st["lsb"] else "DCW" if st["cw"] else st["rig"].get("mode")     # the mode as the panel shows it
PANEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.html")

def web_ptt(ip, on, start=False):
    """on: start = the button was just pressed; without it the message only keeps a transmission going, so a button that is
    still held after a safety un-key does not key the radio again."""
    who = "web:" + ip
    if on:
        with lock:
            if st["keyed"]:
                if st["keyed_by"] == who: st["web_ptt_seen"] = time.time(); return 0, ""
                return 1, f"already keyed by {st['keyed_by']}"
        if st["cw"] and start: return 1, "in the CW keyer mode, key with the space bar or the arrow keys"
        if not start:
            s = st.get("safety"); return 1, "stopped: " + s["why"] if s and time.time() - s["at"] < 5 else "transmission ended: press PTT again"
        why = tx_block()
        if why: log(f"PTT refused for the web panel ({host_name(ip)}): {why}"); return 1, why
        with lock:
            if st["keyed"]: return 1, f"already keyed by {st['keyed_by']}"
            st["web_ptt_seen"] = time.time(); set_keyed(True, who)
        c, _ = rig.cmd("T 1")
        if c != 0: set_keyed(False, None); return c, f"radio refused PTT (RPRT {c})"
        log(f"radio KEYED from the web panel ({host_name(ip)})")
        return 0, ""
    with lock: mine = st["keyed"] and st["keyed_by"] == who
    if not mine: return 0, ""
    # voice: the microphone never goes silent, so play only what was already queued when the button was released
    with lock: q = len(st["txq"]) / (a.rate * 2)
    time.sleep(min(q + 0.1, 0.8))
    c, _ = rig.cmd("T 0")
    set_keyed(False, who)
    if c != 0: safety_unkey("T 0 from the web panel failed")
    return 0, ""

# ---------------------------------------------------------------- repeaters (list kept on the Pi, shared by every panel)
REPEATERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repeaters.json")
rep_lock = threading.Lock(); rep = {"list": [], "v": 0}
def rep_load():
    try:
        with open(REPEATERS) as f: rep["list"] = [x for x in (rep_clean(i) for i in json.load(f)) if x]
    except FileNotFoundError: pass
    except (OSError, ValueError, TypeError) as e: log(f"repeaters.json could not be read ({e}): starting with an empty list")
def rep_save():
    tmp = REPEATERS + ".tmp"
    with open(tmp, "w") as f: json.dump(rep["list"], f, indent=0)
    os.replace(tmp, REPEATERS); rep["v"] += 1
DCS = [23,25,26,31,32,36,43,47,51,53,54,65,71,72,73,74,114,115,116,122,125,131,132,134,143,145,152,155,156,162,165,172,174,
       205,212,223,225,226,243,244,245,246,251,252,255,261,263,265,266,271,274,306,311,315,325,331,332,343,346,351,356,364,365,
       371,411,412,413,423,431,432,445,446,452,454,455,462,464,465,466,503,506,516,523,526,532,546,565,606,612,624,627,631,632,
       654,662,664,703,712,723,731,732,734,743,754]
radio.dcs = DCS
def dcs_cmd(code, sql):
    """DCS code as written (23 = D023); sql: receive squelch as well, else transmit only. Code 0: CTCSS / DCS off."""
    return "D 0" if not code else (f"\\set_dcs_sql {code}" if sql else f"D {code}")
AUD_NUM = ("nr", "low", "high", "shift", "notch_f", "notch_w", "notch2_f", "notch2_w", "bass", "treble", "fmsq", "comp", "presence", "txlow", "txhigh")
AUD_BOOL = ("anotch", "notch", "notch2", "squelch", "sharp")
def aud_clean(a):
    """Audio settings saved with a favorite: names to show and the settings themselves. Never volume, mic gain or drive."""
    if not isinstance(a, dict): return None
    s = a.get("set") if isinstance(a.get("set"), dict) else {}; out = {}
    for k, v in s.items():
        if k in AUD_NUM and isinstance(v, (int, float)) and not isinstance(v, bool) and -10000 < v < 10000: out[k] = v
        elif k in AUD_BOOL and isinstance(v, bool): out[k] = v
        elif k == "agc" and v in ("off", "slow", "fast"): out[k] = v
    return {"rx": str(a.get("rx") or "").strip()[:30], "tx": str(a.get("tx") or "").strip()[:30], "set": out} if out else None
def rep_clean(i):
    """A valid list entry, or None. freq = the repeater's output, Hz."""
    try:
        hz = int(i["freq"]); off = int(i.get("offset") or 0); tone = round(float(i.get("tone") or 0), 1); dcs = int(i.get("dcs") or 0)
    except (KeyError, TypeError, ValueError, OverflowError): return None
    if not (RADIO.min_hz <= hz <= RADIO.max_hz and 0 <= off <= 99_990_000 and (tone == 0 or 60 <= tone <= 260)): return None
    if dcs not in DCS: dcs = 0
    tm = i.get("tmode")
    tm = tm if dcs and tm in ("dcs", "dsql") else tm if tone and tm in ("tone", "tsql") else "dcs" if dcs and not tone else "tone" if tone else "none"
    t = lambda k, n=60: str(i.get(k) or "").strip()[:n]
    return {"id": t("id", 24) or os.urandom(6).hex(), "name": t("name"), "call": t("call", 20), "freq": hz,
            "shift": i.get("shift") if i.get("shift") in ("+", "-") else "0", "offset": off, "tone": tone,
            "dcs": dcs, "tmode": tm,
            "loc": t("loc", 80), "county": t("county"), "state": t("state", 40), "notes": t("notes", 200),
            "fav": bool(i.get("fav")), "src": t("src", 20),
            "mode": i.get("mode") if i.get("mode") in MODES else "FM", "aud": aud_clean(i.get("aud"))}
def rep_op(j):
    op = j.get("op")
    with rep_lock:
        L = rep["list"]
        if op == "put":
            x = rep_clean(j.get("item") or {})
            if not x: return 1, "check the frequency, offset and tone"
            k = next((n for n, y in enumerate(L) if y["id"] == x["id"]), None)
            if k is None: L.append(x)
            else: L[k] = x
        elif op == "del": rep["list"] = [y for y in L if y["id"] != j.get("id")]
        elif op == "fav":
            for y in L:
                if y["id"] == j.get("id"): y["fav"] = bool(j.get("on"))
        elif op == "import":                       # adds and updates; favorites and their ids are kept
            items = j.get("items")
            if not isinstance(items, list) or len(items) > 10000: return 1, "bad import"
            key = lambda y: (y["freq"], y["call"].upper(), y["tone"]); have = {key(y): n for n, y in enumerate(L)}
            for i in items:
                x = rep_clean(i) if isinstance(i, dict) else None
                if not x: continue
                k = have.get(key(x))
                if k is None: have[key(x)] = len(L); L.append(x)
                else:                              # yours wins for the text; the file updates how it is tuned
                    y = L[k]; L[k] = {**x, "id": y["id"], "fav": y["fav"], "src": y["src"], "mode": y["mode"], "aud": y["aud"],
                                      **{t: y[t] or x[t] for t in ("name", "loc", "county", "state", "notes")}}
        elif op == "clear": rep["list"] = [y for y in L if y["fav"] or y["src"] != "import"]
        else: return 1, "unknown operation"
        if len(rep["list"]) > 20000: rep["list"] = rep["list"][:20000]
        try: rep_save()
        except OSError as e: return 1, f"could not save the list: {e}"
    return 0, ""
def rep_tune(i):
    """Frequency and mode; in FM also shift, offset, CTCSS or DCS."""
    if st["keyed"]: return 1, "transmitting"
    with rep_lock: x = next((dict(y) for y in rep["list"] if y["id"] == i), None)
    if not x: return 1, "not in the list any more"
    if x["mode"] == "DLSB":
        if st["lsb"]: return rig.cmd(f"F {raw_hz(x['freq'])}")[0], ""
        return lsb_on(x["freq"])
    if x["mode"] == "DCW":
        if st["cw"]: return rig.cmd(f"F {raw_hz(x['freq'])}")[0], ""
        return cw_on(x["freq"])
    if st["lsb"]: lsb_set(False, "a favorite was chosen")
    if st["cw"]: cw_set(0, "a favorite was chosen")
    if x["mode"] not in ("FM", "WFM", "FMN"):              # shift and tones only exist in FM
        for c in (f"F {x['freq']}", f"M {x['mode']} 0"):
            r = rig.cmd(c)[0]
            if r != 0: return r, f"the radio refused \"{c}\" (RPRT {r})"
        return 0, ""
    cmds = [f"F {x['freq']}", f"M {x['mode']} 0", f"R {x['shift']}"]
    if x["shift"] != "0" and x["offset"]: cmds.append(f"O {x['offset']}")
    if x["tmode"] in ("tone", "tsql"): cmds.append(f"C {int(round(x['tone'] * 10))}")
    cmds.append({"tone": "U TONE 1", "tsql": "U TSQL 1", "dcs": dcs_cmd(x["dcs"], False), "dsql": dcs_cmd(x["dcs"], True)}.get(x["tmode"], "U TONE 0"))
    for c in cmds:
        r = rig.cmd(c)[0]
        if r != 0 and c[:1] == "O" and not RADIO.caps["offset"]: return r, f"this repeater needs an offset of {x['offset'] / 1e6:g} MHz: set it in the radio's menu (RPT SHIFT)"
        if r != 0: return r, f"the radio refused \"{c}\" (RPRT {r})"
    return 0, ""

def web_cmd(ip, j, admin=False):
    if st["cat_dead"] and j.get("a") not in ("audio", "smeter"): return 1, "the radio is not answering"
    act = j.get("a"); L = {} if admin else cfg["locks"]
    if act == "audio":
        if L.get("txaudio") and (j.get("act") == "reset" or any(k in acc.TX_AUDIO for k in j.get("set") or {})): return 1, "the transmit audio settings are locked by the administrator"
        if L.get("tone") and j.get("act") == "tone": return 1, "the test tone is locked by the administrator"
        if j.get("act") == "quit":                     # only the computer it runs on (or the administrator) may stop it
            with lock: mine = st["audio_ip"] == ip; keyed = st["keyed"]
            if not (mine or admin): return 1, "only the computer running the voice client can stop it"
            if keyed: return 1, "not while transmitting"
        s = j.get("set")
        if isinstance(s, dict) and isinstance(s.get("drive"), (int, float)): s["drive"] = min(s["drive"], cfg["max_drive"])
    if act == "smeter":
        st["smeter_live"] = bool(j.get("on")); return 0, ""
    if act == "audio":                                 # settings for the laptop's audio processing: passed on to its client
        m = {k: j[k] for k in ("set", "act") if k in j}
        with lock: c = st["audio"]; ok = bool(st.get("client") and st["client"].get("voice"))
        if c is None or not ok: return 1, "no voice client connected"
        audio_send(b"C", json.dumps(m).encode()[:4000]); return 0, ""
    if act == "repeater": return rep_tune(j.get("id"))
    if act == "freq":
        hz = int(j["hz"])
        if not (RADIO.min_hz <= hz <= RADIO.max_hz): return 1, "frequency out of range"
        if st["keyed"]:
            why = tx_reason(hz)
            if why: return 1, why
            if not st["rig"].get("split"): st["tx_hz"] = hz
        return rig.cmd(f"F {raw_hz(hz)}")[0], ""
    if act == "nudge":                                 # a step from where the dial is now (voice tuning)
        if st["keyed"]: return 1, "transmitting"
        d = max(-2000, min(2000, int(j["hz"]))); c, v = rig.cmd("f")
        try: hz = int(float(v.get("Frequency", 0)))
        except ValueError: hz = 0
        if c or not RADIO.min_hz <= hz + d <= RADIO.max_hz: return c or 1, "the frequency is not known"
        return rig.cmd(f"F {hz + d}")[0], ""
    if act == "mode":
        if j["mode"] not in MODES: return 1, "unknown mode"
        if st["keyed"] and acc.mode_reason(j["mode"]): return 1, acc.mode_reason(j["mode"])
        if j["mode"] == "DLSB": return lsb_on()
        if j["mode"] == "DCW": return cw_on()
        if st["lsb"] or st["cw"]:
            if st["keyed"]: return 1, "transmitting"
            lsb_leave(True, f"{j['mode']} chosen"); cw_leave(True, f"{j['mode']} chosen")
        return rig.cmd(f"M {j['mode']} 0")[0], ""
    if act == "vfo":
        if j["vfo"] not in ("VFOA", "VFOB"): return 1, "bad VFO"
        if st["keyed"]: return 1, "transmitting"
        if st["lsb"] or st["cw"]: return 1, "leave LSB voice / CW keyer first (press another mode)"
        return rig.cmd(f"V {j['vfo']}")[0], ""
    if act == "swap":
        if not RADIO.caps["swap"] or not KNOWN: return 1, "not on this radio"
        if st["keyed"]: return 1, "transmitting"
        return radio.swap(), ""
    if act == "power":
        if not RADIO.caps["power"] or not KNOWN: return 1, "this radio's power can't be set from here"
        if st["keyed"]: return 1, "transmitting"
        lo, hi = RADIO.caps["power"]; return radio.power(max(lo, min(hi, int(j["w"])))), ""
    if act == "split":
        if st["keyed"]: return 1, "transmitting"
        if st["lsb"] or st["cw"]: return 1, "leave LSB voice / CW keyer first (press another mode)"
        with lock: cur = st["rig"].get("vfo") or "VFOA"
        tx = "VFOB" if cur == "VFOA" else "VFOA"
        return rig.cmd(f"S {1 if j['on'] else 0} {tx}")[0], ""
    if act == "rit":
        hz = max(-9990, min(9990, int(j["hz"])))
        c = rig.cmd(f"J {hz}")[0]
        if c == 0:
            with lock: st["rit"] = hz
        return c, ""
    if act == "shift":
        if j["dir"] not in ("+", "-", "0"): return 1, "bad shift"
        return rig.cmd(f"R {j['dir']}")[0], ""
    if act == "offset":
        c = rig.cmd(f"O {max(0, min(99_990_000, int(j['hz'])))}")[0]
        return c, "" if RADIO.caps["offset"] or not c else "the offset of this radio is set in its own menu (RPT SHIFT 28MHz / 50MHz)"
    if act == "tone":                                  # CTCSS tone in Hz, e.g. 100.0
        return rig.cmd(f"C {int(round(float(j['hz']) * 10))}")[0], ""
    if act == "dcs":                                   # {"code": 23, "on": "off" | "dcs" | "dsql"}
        code = int(j.get("code") or 0)
        if j.get("on") == "off": return rig.cmd(dcs_cmd(0, False))[0], ""
        if code not in DCS or j.get("on") not in RADIO.caps["dcs"]: return 1, "unknown DCS code, or not possible on this radio"
        return rig.cmd(dcs_cmd(code, j["on"] == "dsql"))[0], ""
    if act == "simplex":                               # all repeater settings off
        for c in ("R 0", "U TONE 0") + (("D 0",) if "dcs" in RADIO.caps["dcs"] else ()):
            r = rig.cmd(c)[0]
            if r != 0: return r, f"the radio refused \"{c}\" (RPRT {r})"
        return 0, ""
    if act in ("TONE", "TSQL", "LOCK"):
        return rig.cmd(f"U {act} {1 if j['on'] else 0}")[0], ""
    return 1, "unknown command"

def state_for(ip, role=""):
    now = time.time()
    with lock:
        q = st["req"]; an = st["answers"].get(ip); sf = st.get("safety"); hz = vhz(st["rig"].get("freq") or 0)
        access = {"role": role, "admin_set": acc.has("admin"), "need_pw": acc.has("oper") and not role, "need_control": cfg["need_control"],
                  "in_use": label_of(in_use_by(ip)) if in_use_by(ip) else None, "max_drive": cfg["max_drive"], "locks": cfg["locks"],
                  "req": {"name": q["name"], "to_me": q["to"] == ip, "mine": q["ip"] == ip, "to": label_of(q["to"]), "yes": q["yes"],
                          "left": max(0, round(cfg["answer_s"] - (now - q["at"]))), "no_answer": cfg["no_answer"]} if q else None,
                  "answer": an if an and now - an["at"] < 8 else None, "bands": acc.locked_bands(), "trips": dict(acc.trips), "modes": dict(cfg["modes"]), "hide_locked": cfg["hide_locked"],
                  "tx_why": (lambda t: ("the repeater shift would transmit on " + acc.mhz(t) + ": " if t != hz else "") + tx_reason(t) if tx_reason(t) else "")(tx_freq(hz, st["rig"].get("mode"))) if hz and not st["rig"].get("split") else "" if cfg["tx"] else acc.reason(1),
                  "mode_why": acc.mode_reason(cur_mode()),
                  "safety": sf if sf and now - sf["at"] < 15 else None, "swr_on": cfg["swr"]["on"], "swr_level": cfg["swr"]["level"],
                  "meter_ok": st["meter_ok"], "reset_admin": cfg["swr"]["reset"] == "admin", "stuck": st["stuck"]}
        access["tx_why"] = access["tx_why"] or access["mode_why"]
        cats = [{"ip": v["ip"], "name": host_name(v["ip"]), "since": int(now - v["since"])}
                for v in st["cat_clients"].values() if v["ip"] != "127.0.0.1"]
        panels = [{"ip": k, "name": v["name"]} for k, v in st["panels"].items() if now - v["seen"] < 5]
        return {"bridge": VERSION, "release": RELEASE, "radio": {"key": RADIO.key, "name": RADIO.name, "min": RADIO.min_hz, "max": RADIO.max_hz, "caps": RADIO.caps, "ticks": RADIO.ticks, "problem": radio.check()}, "you": ip, "rig_ok": st["rig_ok"] and not st["cat_dead"], "rig": {**st["rig"], "freq": hz, "mode": "DLSB" if st["lsb"] else "DCW", "dial": st["rig"].get("freq")} if st["lsb"] or st["cw"] else dict(st["rig"]), "lsb": st["lsb"], "cw": cw_state(), "winlink": wl_state(), "aprs": ap_summary(), "rpt": {**st["rpt"], "tx": tx_freq(hz, st["rig"].get("mode")) if hz else 0},
                "rig_age": round(now - st["rig"].get("ts", 0), 1), "rit": st["rit"], "smeter_live": st.get("smeter_live", False),
                "rep_v": rep["v"], "dcs_codes": DCS, "listening": st["audio"] is not None,
                "client": st.get("client") if st["audio"] is not None and now - st.get("client_ts", 0) < 5 else None,
                "keyed": st["keyed"], "keyed_by": st["keyed_by"], "keyed_for": round(now - st["key_ts"], 1),
                "tx_timeout": cfg["tx_timeout"], "mine": st["keyed_by"] == "web:" + ip,
                "owner": st["owner"], "owner_name": st["owner_name"], "allowed": allowed(ip),
                "audio": ({"name": st["audio_addr"], "ip": st["audio_ip"], "since": int(now - st["audio_since"])}
                          if st["audio"] is not None else None),
                "cat": cats, "panels": panels, "modes": MODES, "access": access}

class Web(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *args): pass
    def reply(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes): body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY"); self.send_header("X-Content-Type-Options", "nosniff")
        for h in self.extra: self.send_header(*h)
        self.extra = []
        self.end_headers(); self.wfile.write(body)
    extra = []
    def ours(self, post):
        """Only from the panel's own pages: another website can't send its own headers here, and can't use its own name."""
        host = self.headers.get("Host", ""); o = self.headers.get("Origin")
        if not acc.host_ok(host, socket.gethostname()):
            self.close_connection = True
            self.reply(403, {"error": "open the panel by the Pi's address (the administrator can allow other names)"}); return False
        if (o is not None and o not in ("http://" + host, "https://" + host)) or self.headers.get("Sec-Fetch-Site") == "cross-site" and post or \
           post and (self.headers.get("X-Panel") != "1" or not (self.headers.get("Content-Type") or "").startswith("application/json")):
            self.close_connection = True
            self.reply(403, {"error": "request refused: reload this page"}); return False
        return True
    def token(self):
        try: m = SimpleCookie(self.headers.get("Cookie", "")).get("ft897s")
        except CookieError: m = None
        return m.value if m else ""
    def set_token(self, t): self.extra = [("Set-Cookie", f"ft897s={t}; Path=/; HttpOnly; SameSite=Strict" + ("" if t else "; Max-Age=0"))]
    def ident(self):
        ip = ip_of(self.client_address)
        name = self.headers.get("X-Panel-Name", "")[:40] or host_name(ip)
        with lock:
            st["panel_seen"] = time.time(); st["panels"][ip] = {"name": name, "seen": time.time()}
        seen(ip)
        return ip, name
    def do_GET(self):
        if not self.ours(False): return
        if self.path == "/admin":
            try:
                with open(os.path.join(os.path.dirname(PANEL), "admin.html"), "rb") as f: return self.reply(200, f.read(), "text/html; charset=utf-8")
            except OSError: return self.reply(404, b"admin.html missing", "text/plain")
        if self.path == "/api/admin":
            r = acc.role(self.token(), ip_of(self.client_address))
            j = {"role": r, "admin_set": acc.has("admin"), "oper_set": acc.has("oper"), "radio": {"key": RADIO.key, "name": RADIO.name, "max": RADIO.max_hz, "modes": MODES, "labels": acc.MODE_NAME}}
            if r == "admin":
                with acc.lock: j.update(cfg=json.loads(json.dumps(cfg)), trips=dict(acc.trips), problem=acc.problem, version=VERSION, aprs_pass_set=bool(acc.aprs_pass()))
                with lock: j["rig"] = dict(st["rig"])
            return self.reply(200, j)
        if self.path == "/aprs":
            try:
                with open(os.path.join(os.path.dirname(PANEL), "aprs.html"), "rb") as f: return self.reply(200, f.read(), "text/html; charset=utf-8")
            except OSError: return self.reply(404, b"aprs.html missing", "text/plain")
        if self.path.startswith("/static/"):                  # the map library and APRS icons, kept on the Pi (works with no internet)
            base = os.path.realpath(os.path.join(os.path.dirname(PANEL), "static"))
            fp = os.path.realpath(os.path.join(base, self.path.split("?")[0][len("/static/"):]))
            ct = {".js": "text/javascript", ".css": "text/css", ".png": "image/png", ".md": "text/plain; charset=utf-8"}.get(os.path.splitext(fp)[1])
            if not fp.startswith(base + os.sep) or not ct or not os.path.isfile(fp): return self.reply(404, {"error": "not found"})
            with open(fp, "rb") as f: return self.reply(200, f.read(), ct)
        if self.path in ("/", "/index.html"):
            try:
                with open(PANEL, "rb") as f: return self.reply(200, f.read(), "text/html; charset=utf-8")
            except OSError: return self.reply(404, b"panel.html missing", "text/plain")
        if self.path == "/api/state":
            ip, _ = self.ident(); return self.reply(200, state_for(ip, acc.role(self.token(), ip)))
        if self.path == "/api/aprs": return self.reply(200, ap_full())
        if self.path == "/api/maps": return self.reply(200, maps_info())
        m = re.fullmatch(r"/tiles/([a-z0-9-]{1,20})/(\d{1,2})/(\d{1,7})/(\d{1,7})(\.\w{3,4})?", self.path.split("?")[0])
        if m:                                              # offline map tiles, from the downloaded areas
            d = mapstore.tile(m[1], int(m[2]), int(m[3]), int(m[4]))
            return self.reply(200, d, "image/jpeg") if d else self.reply(404, b"", "image/jpeg")
        if self.path == "/api/repeaters":
            with rep_lock: return self.reply(200, {"list": rep["list"], "v": rep["v"]})
        if self.path.split("?")[0] == "/api/audio": return self.audio_stream()
        self.reply(404, {"error": "not found"})
    def audio_stream(self):
        """Listen-only audio for the waterfall: frames of type (R receive, T transmit), length (2 bytes), 16-bit mono PCM
        at 8 kHz (48 kHz with ?full=1). Ends when the viewer closes it; does not use up the laptop audio link."""
        if len(taps) >= TAP_MAX: return self.reply(503, {"error": "too many waterfall viewers"})
        q = queue.Queue(maxsize=250)                        # 5 s
        self.close_connection = True; self.connection.settimeout(5)
        full = "full=1" in self.path
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("X-Audio-Rate", str(a.rate if full else a.rate // max(1, a.rate // 8000)))
        self.send_header("Cache-Control", "no-store"); self.send_header("Connection", "close")
        self.end_headers()
        taps[q] = full
        try:
            while True:
                try: d = q.get(timeout=3)
                except queue.Empty: d = b"K\0\0"          # keep-alive, also notices a viewer that has gone
                while not q.empty() and len(d) < 4000: d += q.get_nowait()
                self.wfile.write(d); self.wfile.flush()
        except (OSError, queue.Empty): pass
        finally: taps.pop(q, None)
    def do_POST(self):
        if not self.ours(True): return
        ip, name = self.ident()
        try:
            n = int(self.headers.get("Content-Length", 0))
            if not 0 <= n <= 8_000_000: self.close_connection = True; return self.reply(413, {"error": "too big"})
            j = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if not isinstance(j, dict): raise ValueError
        except (ValueError, OSError): return self.reply(400, {"error": "bad request"})
        role = acc.role(self.token(), ip); admin = role == "admin"
        try:
            if self.path == "/api/login":
                r, t = acc.login(ip, str(j.get("password") or "")[:200])
                if not r: log(f"sign-in refused for {label_of(ip)}: {t}"); return self.reply(403, {"error": t})
                log(f"{label_of(ip)} signed in ({r})"); self.set_token(t); return self.reply(200, {"ok": True, "role": r})
            if self.path == "/api/logout": acc.logout(self.token()); self.set_token(""); return self.reply(200, {"ok": True})
            if self.path == "/api/admin": return self.admin_post(ip, j, admin)
            if self.path == "/api/take":
                if j.get("force") and admin: log(f"{label_of(ip)} takes over as administrator"); end_request("the administrator took over"); take_control(ip, label_of(ip)); return self.reply(200, {"ok": True, "state": "granted"})
                if acc.has("oper") and not role: return self.reply(401, {"error": "password needed to take control"})
                err, state = ask_control(ip, name)
                return self.reply(409, {"error": err}) if err else self.reply(200, {"ok": True, "state": state})
            if self.path == "/api/answer":
                err = answer(ip, j.get("yes") is True); return self.reply(409, {"error": err}) if err else self.reply(200, {"ok": True})
            if self.path == "/api/cancel":
                with lock:
                    if st["req"] and st["req"]["ip"] == ip: log(f"{st['req']['name']} no longer asks for control"); st["req"] = None
                return self.reply(200, {"ok": True})
            if self.path == "/api/release":
                if st["owner"] == ip: release_control(ip)
                return self.reply(200, {"ok": True})
            if self.path == "/api/swr_reset" and (admin or allowed(ip)):
                if cfg["swr"]["reset"] == "admin" and not admin: return self.reply(403, {"error": "only the administrator can reset this"})
                b = str(j.get("band"))[:12]
                if acc.untrip(b): log(f"high-SWR lock on {b} reset by {label_of(ip)}")
                return self.reply(200, {"ok": True})
            if not allowed(ip):
                if self.path == "/api/ptt" and not j.get("on"): return self.reply(200, {"ok": True})
                return self.reply(403, {"error": why_not()})
            if self.path == "/api/ptt":
                c, err = web_ptt(ip, j.get("on") is True, j.get("start") is True)
            elif self.path == "/api/maps":
                c, res = maps_op(ip, j)
                if c == 0: return self.reply(200, {"ok": True, **(res if isinstance(res, dict) else {})})
                err = res
            elif self.path == "/api/aprs":
                c, err = ap_op(ip, j)
                if c == 0 and err: return self.reply(200, {"ok": True, "note": err})
            elif self.path == "/api/winlink":
                c, err = wl_op(ip, j)
            elif self.path == "/api/cw":
                c, err = cw_op(ip, j, admin)
            elif self.path == "/api/repeaters":
                if cfg["locks"]["repeaters"] and not admin: return self.reply(403, {"error": "the repeater list is locked by the administrator"})
                c, err = rep_op(j)
            elif self.path == "/api/cmd":
                c, err = web_cmd(ip, j, admin)
                if j.get("a") not in ("audio", "smeter"): st["cmd_at"] = time.time()
                if c == 0 and a.verbose and j.get("a") != "audio": log(f"web panel {name}: {j}")
            else: return self.reply(404, {"error": "not found"})
        except (KeyError, ValueError, TypeError) as e:
            return self.reply(400, {"error": f"bad request ({e})"})
        if c != 0: return self.reply(409, {"error": err or f"radio answered RPRT {c}"})
        self.reply(200, {"ok": True})
    def admin_post(self, ip, j, admin):
        op = j.get("op"); pw = str(j.get("password") or "")
        if op == "setup":                                      # the first administrator password
            with acc.lock:
                if acc.has("admin"): return self.reply(409, {"error": "the administrator password is already set: sign in"})
                if not 8 <= len(pw) <= 200: return self.reply(400, {"error": "use at least 8 characters"})
                acc.set_password("admin", pw)
            log(f"administrator password set by {label_of(ip)}")
        elif not admin: return self.reply(403, {"error": "sign in as administrator"})
        elif op == "save":
            try:
                acc.update(j.get("cfg") or {})
                if "aprs_pass" in j: acc.set_aprs_pass(j["aprs_pass"]); log(f"APRS-IS passcode {'set' if j['aprs_pass'] else 'removed'} by {label_of(ip)}")
            except ValueError as e: return self.reply(400, {"error": str(e)})
            except OSError as e: return self.reply(500, {"error": f"could not save: {e}"})
            if cfg["mic"] is not None or cfg["spk"] is not None:
                d = find_card(); cid = card_index(d) if d and d != "null" else None
                if cid: set_levels(cid)
            log(f"settings changed by {label_of(ip)}"); return self.reply(200, {"ok": True, "cfg": cfg})
        elif op == "password":
            which = j.get("which")
            if which not in ("admin", "oper"): return self.reply(400, {"error": "bad request"})
            if not acc.check("admin", str(j.get("current") or "")): return self.reply(403, {"error": "the current administrator password is wrong"})
            if pw or which == "admin":
                if not 8 <= len(pw) <= 200: return self.reply(400, {"error": "use at least 8 characters"})
                if acc.check("oper" if which == "admin" else "admin", pw): return self.reply(400, {"error": "the two passwords must be different"})
            elif which == "oper": pw = ""
            acc.set_password(which, pw); log(f"{which} password {'changed' if pw else 'removed'} by {label_of(ip)}")
            if which == "oper":                                # stay signed in as administrator
                pw = str(j.get("current") or "")
        else: return self.reply(400, {"error": "bad request"})
        r, t = acc.login(ip, pw); self.set_token(t if r else "")
        return self.reply(200, {"ok": True, "role": r})

def web_server():
    rep_load()
    if not a.web_port: return time.sleep(1e9)
    srv = ThreadingHTTPServer((a.bind, a.web_port), Web); srv.daemon_threads = True
    log(f"web panel on http://{socket.gethostname()}.local:{a.web_port}/")
    srv.serve_forever()

def on_stop(signum, frame):
    for f in (wl_stop, ap_stop):
        try: f("the bridge stops")
        except Exception: pass
    if st["keyed"]:
        try: rig_cmd("T 0", timeout=1)
        except OSError: pass
        if KNOWN:
            try: radio.release()
            except Exception: pass
    if rig_proc and rig_proc.poll() is None: rig_proc.terminate()
    log("bridge stopped"); os._exit(0)

if __name__ == "__main__":
    acc.load(); rpt_load(); cw_load(); wl_load(); ap_load(); cw_dec.set_f(cwset["pitch"])
    if acc.problem: log(acc.problem + ": no transmitting until the administration page is saved")
    signal.signal(signal.SIGTERM, on_stop); signal.signal(signal.SIGINT, on_stop)
    log(f"W5PL FT-897D network bridge {VERSION} (yaesu-netradio {RELEASE})" + (f", radio {RADIO.name}" if RADIO.key != "ft897d" else "") + (" (DUMMY test mode)" if a.dummy else ""))
    def supervised(fn):
        while True:                            # a bug in one part must not silently stop it (or the watchdog)
            try: fn()
            except Exception as e:
                import traceback; log(f"{fn.__name__} failed: {e!r}\n{traceback.format_exc()}"); time.sleep(2)
    for fn in (rig_loop, watchdog, cat_server, audio_server, rx_loop, tx_loop, poller, web_server, cw_loop, cw_rx_loop, ap_kiss_loop, ap_is_loop):
        threading.Thread(target=supervised, args=(fn,), daemon=True).start()
    while True: time.sleep(3600)
