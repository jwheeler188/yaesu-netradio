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
import argparse, array, datetime, fcntl, json, os, queue, re, signal, socket, struct, subprocess, sys, threading, time, glob
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie, CookieError
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ft897_access as acc
from ft897_lsb import Invert, PIVOT
from ft897_tap import TickClean
import ft897_radio as radios
from ft897_radio import parse_raw
cfg = acc.cfg

VERSION = "2026.11.25b"
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
p.add_argument("--dummy", action="store_true", help="test mode: Hamlib dummy rig, ALSA null audio, no radio needed")
p.add_argument("-v", "--verbose", action="store_true", help="log PTT, frequency changes and connections")
a = p.parse_args()
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
      "lsb": False, "lsb_at": 0.0, "cat_fail": 0, "cat_dead": False, "alive_at": 0.0, "stuck": None, "unkeys": 0,
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
            try: lsb_resume()
            except Exception as e: log(f"software LSB not resumed: {e!r}")
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
def vhz(hz): return hz + PIVOT if st["lsb"] and hz else hz         # the frequency to show and to check: the LSB carrier
def raw_hz(hz): return hz - PIVOT if st["lsb"] else hz             # what the dial has to be
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
    if st["lsb"]:
        m = rig.cmd("m")[1].get("Mode")
        if m != "PKTUSB":
            lsb_set(False, f"the radio is in {m}"); return "the radio's mode was changed: LSB voice is off now, press its button again"
        hz = vhz(hz)
    c, v = rig.cmd("s")
    if c == 0 and v.get("Split") == "1":
        if st["lsb"]: return "switch split off: LSB voice can't use it"
        tx = v.get("TX VFO"); cur = rig.cmd("v")[1].get("VFO")
        if hz and cur: st["vfo_freq"][cur] = hz
        if cur == tx: pass
        else:
            if KNOWN and RADIO.key == "ft891": st["vfo_freq"][tx] = radio.tx_vfo_hz()
            hz = st["vfo_freq"].get(tx, 0)
            if not hz and acc.reason(0): return f"split is on and the frequency of {tx} is not known yet: select it once"
    tx = hz
    if RADIO.caps["reports"] and not a.dummy: rpt_read()
    if st["rpt"]["shift"] in ("+", "-") and not st["lsb"]:
        tx = tx_freq(hz, rig.cmd("m")[1].get("Mode"))
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
                elif not a.no_audio_check:
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
                if RADIO.key == "ft891" and not a.dummy and re.fullmatch(r"[+;|,]?(S|\\set_split_vfo)( currVFO| VFO[AB])? 0( \w+)?", text.strip()):      # Hamlib 4.5.5 can't: RPRT -12
                    ok = radio.line("S 0 VFOA")[0] == 0
                    c.sendall((b"set_split_vfo: 0 VFOA\n" if text.strip()[:1] in "+;|," else b"") + (b"RPRT 0\n" if ok else b"RPRT -9\n")); continue
                if st["lsb"] and tok0(text) in ("F", "M", "V", "S", "I", "X", "G", "\\set_freq", "\\set_mode", "\\set_vfo", "\\set_split_vfo", "\\set_split_freq", "\\set_split_mode", "\\vfo_op"):
                    if st["keyed"]: c.sendall(b"RPRT -9\n"); continue
                    lsb_set(False, f"{name} sets the radio")             # an app such as WSJT-X has taken over
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
        t = time.time()
        while True:
            t += FRAME_MS / 1000; time.sleep(max(0, t - time.time()))
            d = inv_rx.process(tone) if st["lsb"] else tone
            if st["audio"] is not None: audio_send(b"A", d)
            if taps and not st["keyed"]: tap_feed(b"R", d)
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
        err = pr.stderr.read().decode(errors="replace").strip()
        log(f"RX audio stopped ({err or 'exit ' + str(pr.wait())}): retrying in 2 s"); time.sleep(2)

def tx_loop():
    """laptop -> Digirig sound card. Writes one 20 ms frame at a time into a 4 kB pipe, so audio reaches the radio
    within ~0.15 s (a full 64 kB pipe would add 0.7 s and cut off the end of every transmission)."""
    silence = bytes(FRAME)
    while True:
        dev = wait_card()
        log(f"TX audio to {dev}")
        if dev == "null":                      # test mode: play into nothing at the real pace
            class Paced:
                t = time.time()
                def write(self, b): Paced.t += len(b) / (a.rate * 2); time.sleep(max(0, Paced.t - time.time()))
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
                if st["lsb"] and r["mode"] != "PKTUSB" and not st["keyed"] and time.time() - st["lsb_at"] > 2: lsb_set(False, f"the radio is in {r['mode']}")
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
        aud = st["audio"] if st["audio_ip"] != ip else None
        cats = [v for v in st["cat_clients"].values() if v["ip"] not in (ip, "127.0.0.1")]
    log(f"TAKE CONTROL by {label}: closing {len(cats)} CAT connection(s)" + (" and the audio link" if aud else ""))
    if keyed and not (kb.startswith(ip + ":") or kb == "web:" + ip):
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
        kb = st["keyed_by"] or ""
        if st["keyed"]:
            k = kb[4:] if kb.startswith("web:") else kb.rsplit(":", 1)[0]
            if k != ip: return k
        if st["audio"] is not None and st["audio_ip"] != ip and time.time() - st["last_frame"] < 5: return st["audio_ip"]
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
AUD_NUM = ("nr", "low", "high", "shift", "notch_f", "notch_w", "bass", "treble", "fmsq", "comp", "presence", "txlow", "txhigh")
AUD_BOOL = ("anotch", "notch", "squelch", "sharp")
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
    if st["lsb"]: lsb_set(False, "a favorite was chosen")
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
        if j["mode"] == "DLSB": return lsb_on()
        if st["lsb"]:
            if st["keyed"]: return 1, "transmitting"
            lsb_leave(True, f"{j['mode']} chosen")
        return rig.cmd(f"M {j['mode']} 0")[0], ""
    if act == "vfo":
        if j["vfo"] not in ("VFOA", "VFOB"): return 1, "bad VFO"
        if st["keyed"]: return 1, "transmitting"
        if st["lsb"]: return 1, "leave LSB voice first (press another mode)"
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
        if st["lsb"]: return 1, "leave LSB voice first (press another mode)"
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
                  "answer": an if an and now - an["at"] < 8 else None, "bands": acc.locked_bands(), "trips": dict(acc.trips),
                  "tx_why": (lambda t: ("the repeater shift would transmit on " + acc.mhz(t) + ": " if t != hz else "") + tx_reason(t) if tx_reason(t) else "")(tx_freq(hz, st["rig"].get("mode"))) if hz and not st["rig"].get("split") else "" if cfg["tx"] else acc.reason(1),
                  "safety": sf if sf and now - sf["at"] < 15 else None, "swr_on": cfg["swr"]["on"], "swr_level": cfg["swr"]["level"],
                  "meter_ok": st["meter_ok"], "reset_admin": cfg["swr"]["reset"] == "admin", "stuck": st["stuck"]}
        cats = [{"ip": v["ip"], "name": host_name(v["ip"]), "since": int(now - v["since"])}
                for v in st["cat_clients"].values() if v["ip"] != "127.0.0.1"]
        panels = [{"ip": k, "name": v["name"]} for k, v in st["panels"].items() if now - v["seen"] < 5]
        return {"bridge": VERSION, "radio": {"key": RADIO.key, "name": RADIO.name, "min": RADIO.min_hz, "max": RADIO.max_hz, "caps": RADIO.caps, "ticks": RADIO.ticks, "problem": radio.check()}, "you": ip, "rig_ok": st["rig_ok"] and not st["cat_dead"], "rig": {**st["rig"], "freq": hz, "mode": "DLSB", "dial": st["rig"].get("freq")} if st["lsb"] else dict(st["rig"]), "lsb": st["lsb"], "rpt": {**st["rpt"], "tx": tx_freq(hz, st["rig"].get("mode")) if hz else 0},
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
            j = {"role": r, "admin_set": acc.has("admin"), "oper_set": acc.has("oper"), "radio": {"key": RADIO.key, "name": RADIO.name, "max": RADIO.max_hz}}
            if r == "admin":
                with acc.lock: j.update(cfg=json.loads(json.dumps(cfg)), trips=dict(acc.trips), problem=acc.problem, version=VERSION)
                with lock: j["rig"] = dict(st["rig"])
            return self.reply(200, j)
        if self.path in ("/", "/index.html"):
            try:
                with open(PANEL, "rb") as f: return self.reply(200, f.read(), "text/html; charset=utf-8")
            except OSError: return self.reply(404, b"panel.html missing", "text/plain")
        if self.path == "/api/state":
            ip, _ = self.ident(); return self.reply(200, state_for(ip, acc.role(self.token(), ip)))
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
            try: acc.update(j.get("cfg") or {})
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
    if st["keyed"]:
        try: rig_cmd("T 0", timeout=1)
        except OSError: pass
        if KNOWN:
            try: radio.release()
            except Exception: pass
    if rig_proc and rig_proc.poll() is None: rig_proc.terminate()
    log("bridge stopped"); os._exit(0)

if __name__ == "__main__":
    acc.load(); rpt_load()
    if acc.problem: log(acc.problem + ": no transmitting until the administration page is saved")
    signal.signal(signal.SIGTERM, on_stop); signal.signal(signal.SIGINT, on_stop)
    log(f"W5PL FT-897D network bridge {VERSION}" + (f", radio {RADIO.name}" if RADIO.key != "ft897d" else "") + (" (DUMMY test mode)" if a.dummy else ""))
    def supervised(fn):
        while True:                            # a bug in one part must not silently stop it (or the watchdog)
            try: fn()
            except Exception as e:
                import traceback; log(f"{fn.__name__} failed: {e!r}\n{traceback.format_exc()}"); time.sleep(2)
    for fn in (rig_loop, watchdog, cat_server, audio_server, rx_loop, tx_loop, poller, web_server):
        threading.Thread(target=supervised, args=(fn,), daemon=True).start()
    while True: time.sleep(3600)
