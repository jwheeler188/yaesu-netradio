#!/usr/bin/env python3
"""W5PL FT-897D network radio - laptop audio client (macOS, Windows, Linux).

Connects to ft897-netbridge.py on the Pi and
  - plays the radio's receive audio into a virtual audio cable (macOS: BlackHole 2ch, Windows: CABLE Input), and
  - while the radio is keyed, records the app's transmit audio from it (macOS: BlackHole 2ch, Windows: CABLE Output)
    and sends it to the radio.
CAT/PTT does not go through this program: point WSJT-X / VARA / Pat at Hamlib NET rigctl PI-ADDRESS:4533.

While the radio is keyed, receive audio is muted (so the cable carries only the app's transmit audio).
Reconnects by itself after Wi-Fi drops or a Pi restart. Stop with Ctrl+C.
"""
import argparse, array, atexit, collections, json, math, os, queue, socket, struct, sys, threading, time, platform, datetime, urllib.request, wave
import pyaudio
from ft897_dsp import DEFAULTS, NeuralNR, RxDsp, Speech, clean

VERSION = "2026.11.26c"
if sys.platform == "darwin": DEF_PLAY, DEF_REC = "BlackHole 2ch", "BlackHole 2ch"
elif sys.platform == "win32": DEF_PLAY, DEF_REC = "CABLE Input", "CABLE Output"
else: DEF_PLAY, DEF_REC = "", ""

p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.ArgumentDefaultsHelpFormatter)
SERVER = ""                                      # the Pi's address if server.txt (next to this folder) has none; "" = ask
SERVER_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.txt")
def saved_server():
    try:
        with open(SERVER_FILE) as f: return f.read().strip() or SERVER
    except OSError: return SERVER
p.add_argument("--server", default=saved_server(), help="address of the Pi running ft897-netbridge.py")
p.add_argument("--open-panel", action="store_true", help="open the web panel in the browser")
p.add_argument("--port", type=int, default=4534, help="bridge audio port")
p.add_argument("--rx-audio", default=DEF_PLAY, metavar="NAME", help="device to play receive audio to (part of its name, "
               "or 'default' = the system's output device)")
p.add_argument("--tx-audio", default=DEF_REC, metavar="NAME", help="device to record transmit audio from (part of its "
               "name, or 'default' = the system's input device)")
p.add_argument("--voice", action="store_true", help="voice: use the system's default output and input (your headset) "
               "instead of BlackHole / VB-CABLE; key with the web panel")
p.add_argument("--rx-buffer", type=int, default=0, metavar="MS", help="receive jitter buffer (default 200 ms, 400 ms with --voice)")
p.add_argument("--tx-buffer", type=int, default=0, metavar="MS", help="transmit jitter buffer on the Pi "
               "(default 160 ms, 300 ms with --voice: fewer dropouts on a busy Wi-Fi, a little more delay)")
p.add_argument("--voice-peak", type=float, default=None, metavar="DBFS", help="voice: transmit drive = where speech peaks are "
               "limited (default: the last one used, at first -5; the radio's ALC starts at about -10). Press ] or [ in this "
               "window to change it")
p.add_argument("--mic-gain", type=float, default=None, metavar="DB", help="voice: extra microphone gain before processing")
p.add_argument("--no-processing", action="store_true", help="voice: send the microphone as it is (no leveller/limiter)")
p.add_argument("--rx-gain", type=float, default=None, metavar="DB", help="receive volume (default 0 for WSJT-X etc.; with --voice "
               "the last one used, at first -12). With --voice, press + or - in this window to change it")
p.add_argument("--nr", choices=["off", "mild", "medium", "strong"], default=None, help="voice: receive noise reduction "
               "(default: the last one used, at first mild). Press n in this window to change it")
p.add_argument("--rec-dir", default=os.path.join(os.path.expanduser("~"), "FT897 Recordings"), metavar="FOLDER",
               help="voice: folder for recordings made with the r key")
p.add_argument("--list", action="store_true", help="list audio devices and exit")
p.add_argument("-v", "--verbose", action="store_true", help="log buffer under/overruns")
a = p.parse_args()
if not a.server and not a.list:
    a.server = input("Address of the Raspberry Pi (for example 192.168.1.50): ").strip()
    if not a.server: sys.exit("no address given")
    try:
        with open(SERVER_FILE, "w") as f: f.write(a.server + "\n")
        print("Saved in server.txt")
    except OSError: pass
if a.open_panel:
    import webbrowser; webbrowser.open(f"http://{a.server}:8080/")
if a.voice: a.rx_audio = a.tx_audio = "default"
if not a.tx_buffer: a.tx_buffer = 300 if a.voice else 160
if not a.rx_buffer: a.rx_buffer = 400 if a.voice else 200

GAIN_FILE = os.path.join(os.path.expanduser("~"), ".ft897-audio.json")       # remembers the voice volume
def load_settings():
    """Voice audio settings: the saved ones, then what the command line says."""
    try:
        with open(GAIN_FILE) as f: saved = json.load(f)
        if not isinstance(saved, dict): saved = {}
    except (OSError, ValueError): saved = {}
    if "rx_gain" in saved and "volume" not in saved: saved["volume"] = saved["rx_gain"]       # files from older versions
    if isinstance(saved.get("nr"), str): saved["nr"] = float(NR_NAMES.get(saved["nr"], 8))
    s = {**DEFAULTS, **clean({k: float(v) if isinstance(DEFAULTS.get(k), float) and isinstance(v, int) and not isinstance(v, bool) else v
                              for k, v in saved.items()})}
    s["mute"] = False
    cli = {"volume": a.rx_gain, "nr": NR_NAMES.get(a.nr), "drive": a.voice_peak, "mic": a.mic_gain}
    s.update(clean({k: v for k, v in cli.items() if v is not None}, s))
    return s

NR_NAMES = {"off": 0.0, "mild": 8.0, "medium": 13.0, "strong": 20.0}
SET = load_settings()                                   # live: changed by the keys and by the web panel
if not a.voice: SET["volume"] = a.rx_gain or 0.0        # WSJT-X etc.: plain audio, no processing

def log(msg): print(f"{datetime.datetime.now():%H:%M:%S} {msg}", flush=True)

pa = pyaudio.PyAudio()
if a.list:
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        print(f"{i:3d}  in {d['maxInputChannels']:2d}  out {d['maxOutputChannels']:2d}  {d['name']}")
    sys.exit(0)

def find_dev(name, want_input):
    if not name or name.lower() == "default": return None    # the system's default device (e.g. your headset)
    key = "maxInputChannels" if want_input else "maxOutputChannels"
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if name.lower() in d["name"].lower() and d[key] > 0: return i
    hint = {"darwin": "install BlackHole 2ch (brew install --cask blackhole-2ch) and restart the Mac",
            "win32": "install VB-Audio Virtual Cable (vb-audio.com/Cable) and restart Windows"}.get(sys.platform, "")
    sys.exit(f"audio device '{name}' not found ({'input' if want_input else 'output'}). {hint}\n"
             f"See the list with --list, choose with --rx-audio / --tx-audio.")

PLAY_DEV, REC_DEV = find_dev(a.rx_audio, False), find_dev(a.tx_audio, True)

st = {"sock": None, "keyed": False, "rate": 48000, "discard_until": 0.0, "out_rate": 48000, "in_rate": 48000}

class Resampler:
    """Linear-interpolation sample-rate converter for 16-bit mono, state kept across blocks. Used when a device
    can't run at the link's 48 kHz, e.g. AirPods / Bluetooth headsets in headset mode (16 or 24 kHz). Speech and
    the radio's audio are under 3 kHz, so this is clean enough; a one-pole low-pass guards against aliasing when
    going down."""
    def __init__(self, src, dst):
        self.step = src / dst; self.pos = 0.0; self.prev = 0.0
        self.lp = 1 - math.exp(-2 * math.pi * 0.4 * dst / src) if dst < src else None; self.z = 0.0
    def process(self, data):
        xs = struct.unpack(f"<{len(data) // 2}h", data)
        if self.lp:
            z, k, ys = self.z, self.lp, []
            for x in xs: z += (x - z) * k; ys.append(z)
            self.z = z; xs = ys
        buf = [self.prev, *xs]; out = []; pos = self.pos; last = len(buf) - 1; step = self.step
        while pos < last:
            i = int(pos); f = pos - i
            out.append(int(buf[i] + (buf[i + 1] - buf[i]) * f))
            pos += step
        self.pos = pos - last; self.prev = buf[-1]
        return struct.pack(f"<{len(out)}h", *out)

def rx_volume(data):
    if SET["mute"]: return bytes(len(data))
    g = 10 ** (SET["volume"] / 20)
    if g == 1: return data
    return array.array("h", [max(-32768, min(32767, int(x * g))) for x in array.array("h", data)]).tobytes()

def save_settings():
    if not a.voice: return
    try:
        with open(GAIN_FILE, "w") as f: json.dump({k: v for k, v in SET.items() if k != "mute"}, f)
    except OSError: pass

save_at = [0.0]
def change(new, say=True):
    """Settings from a key or the panel. Takes effect at once; saved a second after the last change."""
    ok = clean(new, SET); SET.update(ok); save_at[0] = time.time() + 1
    if say and ok: log("  ".join(f"{NAMES.get(k, k)} {v:+.0f} dB" if k in NAMES else f"{k} {v}" for k, v in ok.items()))
    send_status()

NAMES = {"volume": "receive volume", "nr": "noise reduction", "drive": "transmit drive", "mic": "mic gain"}

def next_nr():
    lv = [0.0, 8.0, 13.0, 20.0]; change({"nr": next((x for x in lv if x > SET["nr"] + 0.5), 0.0)})

def send_status():
    """Settings and state for the web panel (through the bridge)."""
    if st["sock"] is None: return
    d = {"voice": bool(a.voice), "client": VERSION, "rec": recorder.files is not None, "stalls": st.get("rx_underruns", 0),
         "tone": st["tone"].state() if st.get("tone") is not None else "", "out": st.get("out_name"), "in": st.get("in_name"), "in_rate": st["in_rate"], "out_rate": st["out_rate"]}
    if a.voice:
        with rxlock: d["rx_ms"] = round(len(rxbuf) / 2 / st["out_rate"] * 1000)
        d["set"] = SET; d["tx"] = st.get("tx_stats"); d["neural_ok"] = NeuralNR.load() is not None
        x = st.get("dsp")
        if x is not None: d["notches"] = [round(n[0]) for n in list(x.an.notches.values())] if SET["anotch"] else []; d["presence"] = round(x.nr.presence, 3); d["sq_open"] = x.sq.g > 0.5; d["fm_lvl"] = round(x.fm.lvl, 1); d["fm_open"] = x.fm.open
    try: sendq.put_nowait(frame(b"S", json.dumps(d).encode()))
    except queue.Full: pass

def housekeeping():
    n = 0
    while True:
        time.sleep(0.25); n += 1
        tone = st.get("tone")
        if tone is not None and not st["keyed"] and tone.n == 0 and time.time() - tone.at > 5: st["tone"] = None; log("test tone dropped: the radio was not keyed")
        if save_at[0] and time.time() > save_at[0]: save_at[0] = 0.0; save_settings()
        if st["keyed"] and speech is not None and speech.n:
            st["tx_stats"] = speech.stats()
            send_status()
        elif n % 4 == 0: send_status()

def control(d):
    """Command from the web panel: {"set": {...}} or {"act": "rec" | "reset"}."""
    try: j = json.loads(d)
    except ValueError: return
    if not a.voice or not isinstance(j, dict): return
    if isinstance(j.get("set"), dict): change(j["set"], say=False)
    if j.get("act") == "rec": threading.Thread(target=lambda: (recorder.toggle(), send_status()), daemon=True).start()
    if j.get("act") == "tone":
        if st["keyed"]: log("test tone: not while transmitting")
        else: st["tone"] = Tone(); log(f"test tone armed: 1 kHz, 3 s, {SET['drive']:+.0f} dBFS"); send_status()
    if j.get("act") == "toneoff":
        tone = st.get("tone")                          # while still keyed: silence until the un-key, never the open microphone
        if tone is not None and st["keyed"]: tone.n = max(tone.n, tone.LEAD + tone.LEN)
        else: st["tone"] = None
        send_status()
    if j.get("act") == "reset": change(dict(DEFAULTS)); log("audio settings reset")
    if j.get("act") == "quit":                         # the panel's "Stop voice client" button
        if st["keyed"]: log("stop from the web panel: not while transmitting")
        else:
            log("stopped from the web panel")
            try: recorder.close(); save_settings() if save_at[0] else None
            finally: os._exit(0)                       # (the main thread may be waiting on the network)

class Recorder:
    """r key: saves the last 30 s and everything from then on (until r again, or 10 minutes) as two 48 kHz WAV files:
    ..._raw.wav = the radio's audio untouched, ..._heard.wav = after the filter and noise reduction (without the volume)."""
    PRE, MAX = 30 * 96000, 600 * 96000                # bytes
    def __init__(self):
        self.lock = threading.Lock(); self.pre = {k: collections.deque() for k in ("raw", "heard")}
        self.prelen = {"raw": 0, "heard": 0}; self.files = None; self.count = 0
    def add(self, kind, d):
        with self.lock:
            if self.files is None:
                q = self.pre[kind]; q.append(d); self.prelen[kind] += len(d)
                while self.prelen[kind] > self.PRE: self.prelen[kind] -= len(q.popleft())
                return
            try:
                self.files[kind].writeframes(d)
                if kind == "raw": self.count += len(d)
                if self.count >= self.MAX: self._stop("10 minutes")
            except OSError as e: self._stop(f"{e}")
    def toggle(self):
        freq = None if self.files else panel_freq()   # (asks the Pi: not while holding the lock)
        with self.lock:
            if self.files is not None: return self._stop("")
            base = os.path.join(a.rec_dir, f"{datetime.datetime.now():%Y-%m-%d_%H%M%S}" + (f"_{freq}" if freq else ""))
            files = {}
            try:
                os.makedirs(a.rec_dir, exist_ok=True)
                for kind, q in self.pre.items():
                    w = wave.open(f"{base}_{kind}.wav", "wb"); files[kind] = w
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
                    w.writeframes(b"".join(q))
            except OSError as e:
                for w in files.values():
                    try: w.close()
                    except OSError: pass
                return log(f"cannot record: {e}")
            self.count = self.prelen["raw"]; self.files = files; self.base = base
            for k in self.pre: self.pre[k].clear(); self.prelen[k] = 0
            log(f"RECORDING, with the last {self.count / 96000:.0f} s (r again to stop): {base}_raw.wav / _heard.wav")
    def _stop(self, why):
        for w in self.files.values():
            try: w.close()
            except OSError: pass
        self.files = None
        log(f"recording stopped{' (' + why + ')' if why else ''}: {self.count / 96000:.0f} s in {self.base}_raw.wav / _heard.wav")
    def close(self):
        with self.lock:
            if self.files is not None: self._stop("")

def panel_freq():
    """Frequency and mode for the file name, if the bridge knows them."""
    try:
        with urllib.request.urlopen(f"http://{a.server}:8080/api/state", timeout=1.5) as u: j = json.load(u)
        return f"{j['rig']['freq'] / 1e6:.4f}MHz_{j['rig']['mode'].replace('PKTUSB', 'DIG')}" if j["rig_age"] < 5 else None
    except Exception: return None

recorder = Recorder()

def volume_keys():
    """Single key presses in this window: + louder, - quieter (3 dB steps), n noise reduction, r record, ] [ transmit drive."""
    if sys.platform == "win32":
        import msvcrt
        getkey = msvcrt.getwch
    else:
        import termios, tty
        fd = sys.stdin.fileno(); old = termios.tcgetattr(fd)
        atexit.register(termios.tcsetattr, fd, termios.TCSADRAIN, old)
        tty.setcbreak(fd)
        getkey = lambda: os.read(fd, 1).decode("ascii", "ignore")
    while True:
        k = getkey()
        if k == "" and sys.platform != "win32": return
        if k in ("+", "="): change({"volume": SET["volume"] + 3})
        elif k in ("-", "_"): change({"volume": SET["volume"] - 3})
        elif k in ("n", "N"): next_nr()
        elif k in ("r", "R"): recorder.toggle()
        elif k == "]": change({"drive": SET["drive"] + 1})
        elif k == "[": change({"drive": SET["drive"] - 1})

rxbuf = bytearray(); rxlock = threading.Lock(); rx_playing = [False]
sendq = queue.Queue(maxsize=200)

def frame(t, payload=b""): return t + struct.pack(">H", len(payload)) + payload

def recv_exact(s, n):
    b = bytearray()
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d: raise ConnectionError("bridge closed the connection")
        b += d
    return bytes(b)

voice_rx = {"target": a.rx_buffer, "fade": 0.0}

def voice_play(n):
    """Voice playback (called with rxlock held). Unlike the FT8 path, which must keep exact time, it never cuts audio out
    to catch up: after a network stall it keeps the backlog as a cushion and plays 2 % fast until it is used up.
    Each time it runs dry the cushion grows by 200 ms (up to 1.2 s). Starts and stops are faded, not clicked."""
    rate2 = st["out_rate"] * 2; need = n * 2; target = rate2 * voice_rx["target"] // 1000 // 2 * 2
    if not rx_playing[0] and len(rxbuf) >= target: rx_playing[0] = True; voice_rx["fade"] = 0.0
    if not rx_playing[0]: return bytes(need)
    if len(rxbuf) > rate2 * 5 // 2: del rxbuf[:len(rxbuf) - target]       # 2.5 s behind: give up and jump
    take = n + n // 50 if len(rxbuf) > target + rate2 // 5 else n         # > 200 ms over: 2 % fast
    if len(rxbuf) >= take * 2:
        xs = array.array("h", bytes(rxbuf[:take * 2])); del rxbuf[:take * 2]
        if take != n:
            k = (take - 1) / max(1, n - 1); last = take - 1
            ys = array.array("h")
            for i in range(n):
                p = i * k; j = min(int(p), last - 1); ys.append(int(xs[j] + (xs[j + 1] - xs[j]) * (p - j)))
            xs = ys
        if voice_rx["fade"] < 1:
            g = voice_rx["fade"]; step = 1 / (0.03 * st["out_rate"])
            for i in range(n):
                xs[i] = int(xs[i] * g); g = min(1.0, g + step)
            voice_rx["fade"] = g
        return xs.tobytes()
    xs = array.array("h", bytes(rxbuf)); rxbuf.clear(); rx_playing[0] = False   # ran dry: fade out what is left
    m = len(xs)
    for i in range(m): xs[i] = int(xs[i] * (m - i) / m)
    st["rx_underruns"] = st.get("rx_underruns", 0) + 1
    voice_rx["target"] = min(1200, voice_rx["target"] + 200)
    log(f"network stall: receive buffer raised to {voice_rx['target']} ms (Wi-Fi hiccup on this computer, see README)")
    return xs.tobytes() + bytes(need - m * 2)

def play_cb(in_data, n, info, status):
    need = n * 2
    with rxlock:
        if st["keyed"]:
            rxbuf.clear(); rx_playing[0] = False
            return bytes(need), pyaudio.paContinue
        if a.voice: return voice_play(n), pyaudio.paContinue
        rate2 = st["out_rate"] * 2
        if not rx_playing[0] and len(rxbuf) >= rate2 * a.rx_buffer // 1000: rx_playing[0] = True
        if len(rxbuf) > rate2 * (a.rx_buffer + 400) // 1000:   # far behind (Wi-Fi burst): catch up
            del rxbuf[:len(rxbuf) - rate2 * a.rx_buffer // 1000]
            if a.verbose: log("RX audio: caught up after a burst")
        if rx_playing[0] and len(rxbuf) >= need:
            out = bytes(rxbuf[:need]); del rxbuf[:need]
        else:
            if rx_playing[0]:
                st["rx_underruns"] = st.get("rx_underruns", 0) + 1
                if a.verbose: log("RX audio underrun")
            rx_playing[0] = False; out = bytes(need)
    return out, pyaudio.paContinue

speech = Speech(SET, 48000) if a.voice and not a.no_processing else None

class Tone:
    """One 1 kHz test tone at the drive level, instead of the microphone: 0.7 s of silence (so a repeater is up), 3 s of
    tone, then silence until the radio is un-keyed. Armed by the panel; forgotten if the radio is not keyed within 5 s."""
    LEAD, LEN, RAMP = 33600, 144000, 960
    def __init__(self): self.n = 0; self.at = time.time(); self.level = 32767 * 10 ** (SET["drive"] / 20)
    def make(self, count):
        out = []; L, E = self.LEAD, self.LEAD + self.LEN
        for i in range(self.n, self.n + count):
            if L <= i < E:
                g = min(1.0, (i - L) / self.RAMP, (E - 1 - i) / self.RAMP)
                out.append(int(self.level * g * math.sin(2 * math.pi * 1000 * (i - L) / 48000)))
            else: out.append(0)
        self.n += count
        return struct.pack(f"<{count}h", *out)
    def state(self): return "armed" if self.n == 0 else "sending" if self.n < self.LEAD + self.LEN else "done"

def rec_cb(in_data, n, info, status):
    if st["keyed"] and st["sock"] is not None and time.time() >= st["discard_until"]:
        if st.get("rs_tx") is not None: in_data = st["rs_tx"].process(in_data)
        tone = st.get("tone")
        if tone is not None: in_data = tone.make(len(in_data) // 2)
        elif speech is not None: in_data = speech.process(in_data)
        try: sendq.put_nowait(frame(b"A", in_data))
        except queue.Full: pass
    return None, pyaudio.paContinue

def sender():
    last = 0.0
    while True:
        try: f = sendq.get(timeout=0.5)
        except queue.Empty: f = None
        s = st["sock"]
        if s is None: continue
        if f is None or time.time() - last > 0.5:            # heartbeat so the bridge knows we are alive
            if not st["keyed"] or f is None:
                try: s.sendall(frame(b"P")); last = time.time()
                except OSError: pass
        if f is not None:
            try: s.sendall(f); last = time.time()
            except OSError: pass

def rescan():
    """Re-read the device list: headsets come and go, and change their format."""
    global pa, PLAY_DEV, REC_DEV
    pa.terminate(); pa = pyaudio.PyAudio()
    PLAY_DEV, REC_DEV = find_dev(a.rx_audio, False), find_dev(a.tx_audio, True)

def devices():
    return (pa.get_device_info_by_index(PLAY_DEV) if PLAY_DEV is not None else pa.get_default_output_device_info(),
            pa.get_device_info_by_index(REC_DEV) if REC_DEV is not None else pa.get_default_input_device_info())

def default_ids():
    """The Mac's default output and input device ids, straight from CoreAudio (PortAudio only looks once). None elsewhere."""
    if sys.platform != "darwin": return None
    try:
        import ctypes
        ca = default_ids.ca = getattr(default_ids, "ca", None) or ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        out = []
        for sel in (b"dOut", b"dIn "):
            addr = (ctypes.c_uint32 * 3)(int.from_bytes(sel, "big"), int.from_bytes(b"glob", "big"), 0)
            val, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
            if ca.AudioObjectGetPropertyData(ctypes.c_uint32(1), ctypes.byref(addr), ctypes.c_uint32(0), None, ctypes.byref(size), ctypes.byref(val)): return None
            out.append(val.value)
        return tuple(out)
    except (OSError, AttributeError): return None

def follow_device(sock, stop):
    """Voice on the Mac: when another sound output or input is chosen (AirPods connected ...), end this session; the next one
    opens the new device. Not while transmitting. The start-up mode switch of a headset is left to settle first."""
    if stop.wait(4): return
    base = default_ids(); seen = 0
    while base and not stop.wait(1):
        now = default_ids()
        seen = seen + 1 if now and now != base else 0
        if seen >= 2 and not st["keyed"]:
            log("the computer's sound device changed: switching over")
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            return

def settle_headset():
    """Opening the mic switches AirPods / Bluetooth headsets from stereo 48 kHz to headset mode (mono 24 or 16 kHz).
    PortAudio keeps the old description, and a stream opened with that plays silence. So: turn the mic on until the
    switch is done, then re-read the devices while the headset is still in headset mode."""
    odev, idev = devices()
    rate = int(idev["defaultSampleRate"])
    m = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True, input_device_index=idev["index"],
                frames_per_buffer=rate // 50, stream_callback=lambda *_: (None, pyaudio.paContinue))
    time.sleep(2.0)
    m.stop_stream(); m.close()
    rescan()

def session():
    rescan()
    if a.voice: settle_headset()
    s = socket.create_connection((a.server, a.port), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.sendall(frame(b"H", json.dumps({"name": f"{platform.node()} ({sys.platform})", "client": VERSION, "tx_buffer_ms": a.tx_buffer}).encode()))
    s.settimeout(10)
    h = recv_exact(s, 3); t = h[:1]; body = recv_exact(s, struct.unpack(">H", h[1:])[0])
    if t == b"B": raise PermissionError(body.decode())
    info = json.loads(body)
    st["rate"] = info["rate"]
    ms = info.get("frame_ms", 20)
    odev, idev = devices()
    rate_for = lambda dev: int(dev["defaultSampleRate"])   # the device's own rate (AirPods headset mode: 24 kHz)
    st["out_rate"] = rate_for(odev); st["in_rate"] = rate_for(idev)
    st["rs_rx"] = Resampler(st["rate"], st["out_rate"]) if st["out_rate"] != st["rate"] else None
    st["rs_tx"] = Resampler(st["in_rate"], st["rate"]) if st["in_rate"] != st["rate"] else None
    st["dsp"] = RxDsp(SET, st["rate"]) if a.voice and not a.no_processing and st["rate"] == 48000 else None
    st["out_name"], st["in_name"] = odev["name"], idev["name"]
    inp = pa.open(format=pyaudio.paInt16, channels=1, rate=st["in_rate"], input=True, input_device_index=idev["index"],
                  frames_per_buffer=st["in_rate"] * ms // 1000, stream_callback=rec_cb)
    with rxlock: rxbuf.clear(); rx_playing[0] = False
    out = pa.open(format=pyaudio.paInt16, channels=1, rate=st["out_rate"], output=True, output_device_index=odev["index"],
                  frames_per_buffer=st["out_rate"] * ms // 1000, stream_callback=play_cb)
    if a.voice:                        # throw away what arrived meanwhile, so playback starts cleanly
        st["skip_until"] = time.time() + 0.3
    log(f"connected to {a.server}:{a.port} (bridge {info.get('bridge')}): RX audio -> {odev['name']} "
        f"({st['out_rate']} Hz), TX audio <- {idev['name']} ({st['in_rate']} Hz)")
    if a.voice:
        for what, dev in (("output", odev), ("input", idev)):
            if any(v in dev["name"].lower() for v in ("blackhole", "cable", "vb-audio")):
                log(f"*** WARNING: this computer's sound {what} is {dev['name']}, a virtual cable, not a headset. "
                    f"Pick your headset in the sound settings" + (": this switches over by itself. ***" if sys.platform == "darwin"
                    else " and start this again. ***"))
    st["sock"] = s; stop = threading.Event()
    if a.voice: threading.Thread(target=follow_device, args=(s, stop), daemon=True).start()
    try:
        while True:
            h = recv_exact(s, 3); t = h[:1]; n = struct.unpack(">H", h[1:])[0]
            d = recv_exact(s, n) if n else b""
            if t == b"A":
                if not out.is_active(): raise ConnectionError("audio device stopped (headset disconnected?)")
                if not st["keyed"] and time.time() >= st.get("skip_until", 0):
                    if a.voice and st["rate"] == 48000: recorder.add("raw", d)
                    if st["dsp"] is not None:
                        d = st["dsp"].process(d)
                        if not d: continue
                        recorder.add("heard", d)
                    if st["rs_rx"] is not None: d = st["rs_rx"].process(d)
                    d = rx_volume(d)
                    with rxlock: rxbuf.extend(d)
            elif t == b"C": control(d)
            elif t == b"K":
                on = d == b"1"
                st["discard_until"] = time.time() + 0.05      # BlackHole still holds a little RX audio
                if not on: st["tone"] = None                   # one transmission only
                with rxlock: st["keyed"] = on; rxbuf.clear(); rx_playing[0] = False
                s.sendall(frame(b"k", d))
                log("TX" if on else "RX" + (f"  ({speech.report()})" if speech is not None and speech.n else ""))
    finally:
        st["sock"] = None; st["keyed"] = False; st["tone"] = None; stop.set()
        for x in (out, inp):
            try: x.stop_stream(); x.close()
            except Exception: pass
        try: s.close()
        except OSError: pass

def main():
    log(f"W5PL FT-897D audio client {VERSION} -> {a.server}:{a.port}")
    if a.voice: log("neural noise reduction: " + ("available" if NeuralNR.load() else "not available, " + NeuralNR.why))
    threading.Thread(target=sender, daemon=True).start()
    threading.Thread(target=housekeeping, daemon=True).start()
    if a.voice and sys.stdin is not None and sys.stdin.isatty():
        log(f"receive volume {SET['volume']:+.0f} dB (+ / - to change), noise reduction {SET['nr']:.0f} dB (n to change), r to record, "
            f"transmit drive {SET['drive']:+.0f} dBFS ([ / ] to change). All settings are in the web panel too.")
        threading.Thread(target=volume_keys, daemon=True).start()
    while True:
        try: session()
        except PermissionError as e: log(f"{e}: retrying in 10 s"); time.sleep(10); continue
        except KeyboardInterrupt: raise
        except Exception as e: log(f"not connected ({e}): retrying in 2 s")
        time.sleep(2)

try: main()
except KeyboardInterrupt: log("stopped")
finally: recorder.close(); save_settings() if save_at[0] else None
