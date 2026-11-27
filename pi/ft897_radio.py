"""W5PL network radio - what differs between radios (used by ft897-netbridge.py).

FT-897D (also FT-897, FT-857): Hamlib does the everyday commands, raw 5-byte CAT (read only) the meters and the status checks.
FT-891: the bridge talks CAT text to it directly, through rigctld's raw command. Hamlib 4.5.5 can't do RIT, the repeater offset
or split off on it, and copies VFO A to VFO B at every mode change. Apps (WSJT-X...) still go through Hamlib.
Commands from Yaesu's "FT-891 CAT Operation Reference Book". Menu settings are only ever read, never written.
"""
import re, threading, time

class FT897:
    key = "ft897d"; name = "FT-897D"; model = 1043; models = (1043, 1023, 1022)
    baud = 38400; ptt = "RTS"; serial_hint = "the Digirig's CP2102 serial port"; baud_menu = "019 (CAT RATE)"
    min_hz, max_hz = 100_000, 470_000_000
    modes = ["LSB", "USB", "CW", "CWR", "AM", "FM", "WFM", "RTTY", "PKTUSB", "DLSB", "DCW"]        # DLSB = software LSB through DIG, DCW = CW keyer through DIG
    ticks = True                    # CAT leaks into the receive audio: keep the CAT line quiet while listening
    swr_level = None                # default step of the high-SWR shutdown (None = the one in ft897_access.py)
    rigctld_args = []
    caps = {"vfo": True, "swap": False, "dcs": ["dcs", "dsql"], "offset": True, "reports": False, "power": None, "lsb": True, "voice": ["PKTUSB", "FM", "WFM", "AM", "DLSB"]}
    problem = ""
    def __init__(self, link): self.link = link
    def raw(self, cmd, n): return parse_raw(self.link.cmd(f"W \\0x00\\0x00\\0x00\\0x00\\0x{cmd:02X} {n}", raw=True), n)
    def tx_status(self):
        """0xF7 TX status: bit 7 = receiving. "rx", "tx", or None = no answer"""
        b = self.raw(0xF7, 1)
        return None if not b else "rx" if b[0] & 0x80 else "tx"
    def alive(self):
        """0xE7 receive status, raw: Hamlib 4.5.5 hands out the last frequency and mode it knows even when the radio is off."""
        return bool(self.raw(0xE7, 1) or self.raw(0xE7, 1))
    def meters(self, still_keyed):
        """Not through Hamlib's levels: 4.5.5 returns the power as "ALC" and only a high-SWR flag as "SWR". Command 0xBD: byte 0 =
        power | ALC, byte 1 = SWR | MOD (4 bits each); it answers only while transmitting, so ask 0xF7 (bit 6 = high SWR) first."""
        t = self.raw(0xF7, 1)
        if not t or t[0] & 0x80: return {}
        r = {"po": (t[0] & 0x0F) / 15, "hi_swr": bool(t[0] & 0x40)}
        if not still_keyed(): return r                   # about to go back to receive
        m = self.raw(0xBD, 2)
        if m: r["po"] = max(r["po"], (m[0] >> 4) / 15); r["alc"] = (m[0] & 0x0F) / 15
        if len(m) > 1: r["swr"] = (m[1] >> 4) / 15
        r["raw"] = [t[0]] + m
        return r
    def smeter(self, v): return v                        # Hamlib's RAWSTR: 0..15 (S0..S9+60)
    def release(self): pass
    def check(self): return ""
    def dig_upper(self):
        """The DIG menu (038), from the radio's memory, read only: 5 = USER-U, 4 = USER-L. None = could not be read."""
        b = parse_raw(self.link.cmd("W \\0x00\\0x78\\0x00\\0x00\\0xBB 2", raw=True), 2)
        return b[0] >> 5 if b else None

class Other(FT897):
    """A Hamlib model this program knows nothing about: no meters, no un-key check, no high-SWR shutdown."""
    key = "other"; name = "radio"; models = ()
    modes = ["LSB", "USB", "CW", "CWR", "AM", "FM", "RTTY", "PKTUSB"]
    caps = {**FT897.caps, "lsb": False}
    def tx_status(self): return None
    def alive(self): return True
    def meters(self, still_keyed): return {}
    def dig_upper(self): return None

def parse_raw(out, nbytes):
    """Bytes from rigctld's answer to a raw command. Hamlib prints them as \\0xNN only if one of them is >= 0x80;
    otherwise as plain characters, cut off at the first zero byte. No answer from the radio = no line at all."""
    i = out.find(b"Reply: "); j = out.rfind(b"RPRT ")
    if i < 0 or j < 0 or out[j:].split()[1:2] != [b"0"]: return []
    body = out[i + 7:j]
    if not body.endswith(b"\n"): return []                        # timed out
    body = body[:-1]
    if body.startswith(b"\\0x"): return [int(x, 16) for x in re.findall(rb"\\0x([0-9A-Fa-f]{2})", body)]
    return (list(body) + [0] * nbytes)[:nbytes]

# ---------------------------------------------------------------- FT-891
TONES = [67.0,69.3,71.9,74.4,77.0,79.7,82.5,85.4,88.5,91.5,94.8,97.4,100.0,103.5,107.2,110.9,114.8,118.8,123.0,127.3,131.8,136.5,
         141.3,146.2,151.4,156.7,159.8,162.2,165.5,167.9,171.3,173.8,177.3,179.9,183.5,186.2,189.9,192.8,196.6,199.5,203.5,206.5,
         210.7,218.1,225.7,229.1,233.6,241.8,250.3,254.1]                    # the radio's table 1: number = place in the list
MD = {"LSB": "1", "USB": "2", "CW": "3", "FM": "4", "AM": "5", "RTTY": "6", "CWR": "7", "PKTLSB": "8", "RTTYR": "9", "FMN": "B", "PKTUSB": "C", "AMN": "D"}
DM = {v: k for k, v in MD.items()}
SWR_CAL = [(0, 1.0), (12, 1.0), (39, 1.35), (65, 1.5), (89, 2.0), (242, 5.0), (255, 5.5)]      # Hamlib's table for these radios
S_CAL = [(0, 0), (12, 1), (27, 2), (40, 3), (55, 4), (65, 5), (80, 6), (95, 7), (112, 8), (130, 9), (150, 10), (172, 11), (190, 12), (220, 13), (240, 14), (255, 15)]
def cal(t, x):
    for (x0, y0), (x1, y1) in zip(t, t[1:]):
        if x <= x1: return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 > x0 else y1
    return t[-1][1]
def is_fm(md): return md in ("4", "B")

class FT891:
    key = "ft891"; name = "FT-891"; model = 1036; models = (1036,)
    baud = 0; ptt = "CAT"; serial_hint = "the FT-891's own USB (CP2105, Enhanced port)"; baud_menu = "CAT RATE (05-06)"     # baud 0 = find it
    min_hz, max_hz = 30_000, 56_000_000
    modes = ["LSB", "USB", "CW", "CWR", "AM", "FM", "FMN", "RTTY", "PKTUSB", "PKTLSB"]
    ticks = False
    swr_level = 7                   # of 15; with this radio's meter about 2.4:1
    rigctld_args = ["-C", "serial_handshake=None,timeout=500,retry=1"]
    caps = {"vfo": False, "swap": True, "dcs": ["dsql"], "offset": False, "reports": True, "power": [5, 100], "lsb": False,
            "voice": ["LSB", "USB", "AM", "FM", "FMN", "PKTUSB", "PKTLSB"]}
    def __init__(self, link):
        self.link = link; self.batch = True; self.problem = ""; self.offs = {}; self.info = {}; self.info_at = 0.0; self.dcs = []; self.l = threading.RLock()
    def ask(self, cmd, n):
        """Sends CAT text and returns n characters of answer, or "" ."""
        out = self.link.cmd(f"W {cmd} {n}", raw=True)
        i = out.find(b"Reply: "); j = out.rfind(b"RPRT ")
        if i < 0 or j < 0 or out[j:].split()[1:2] != [b"0"]: return ""
        return out[i + 7:j].decode("ascii", "replace").strip("\0 \r\n")       # (Hamlib 4.5 ends it with a newline, 4.7 with a zero byte)
    def get(self, cmd, n):
        """One read command with an answer of exactly n characters, e.g. get("FA", 12) -> "014250000"."""
        a = self.ask(cmd + ";", n)
        return a[len(cmd):-1] if len(a) == n and a.startswith(cmd) and a.endswith(";") else None
    def put(self, cmd, read, n, want):
        """A set command, checked by reading it back (set commands have no answer). -> Hamlib-style code: 0, or -9 refused, -5 no answer"""
        a = self.ask(f"{cmd};{read};", n) if self.batch else ""
        if a.startswith(read) and a[len(read):-1] == want and len(a) == n: return 0
        if not self.batch: self.ask(cmd + ";", 0)
        time.sleep(0.12)
        v = self.get(read, n)
        if v is None: time.sleep(0.15); v = self.get(read, n)
        return 0 if v == want else -5 if v is None else -9
    # ---- safety
    def tx_status(self):
        v = self.get("TX", 4)
        return {"0": "rx", "1": "tx", "2": "tx"}.get(v)
    def alive(self):
        v = self.get("ID", 7) or self.get("ID", 7)
        if v and v.isdigit(): self.problem = "" if v == "0650" else f"this is not an FT-891 (it calls itself {v}): start the bridge with the right --radio"
        return bool(v)
    def check(self): return self.problem
    def release(self): self.ask("TX0;", 0)
    def meters(self, still_keyed):
        """Transmit meters, 0-255 each: RM5 power, RM4 ALC, RM6 SWR; RI0 = the radio's high-SWR flag."""
        a = self.ask("TX;RM5;RM4;RM6;RI0;", 30) if self.batch else ""
        m = re.fullmatch(r"TX([012]);RM5(\d{3});RM4(\d{3});RM6(\d{3});RI0([01]);", a)
        if m: tx, po, alc, swr, hi = m.groups()
        else:
            tx = self.get("TX", 4)
            if tx not in ("1", "2"): return {}
            po, alc, swr, hi = self.get("RM5", 7), self.get("RM4", 7), self.get("RM6", 7), self.get("RI0", 5)
            if self.batch and None not in (po, alc, swr, hi): self.batch = False          # it answers one at a time only
        if tx not in ("1", "2"): return {}
        num = lambda v: int(v) if v and v.isdigit() else None
        po, alc, swr = num(po), num(alc), num(swr)
        r = {"raw": [po, alc, swr]}
        if hi in ("0", "1"): r["hi_swr"] = hi == "1"
        if po is not None: r["po"] = po / 255
        if alc is not None: r["alc"] = alc / 255
        if swr is not None: r["swr"] = swr / 255; r["swr_ratio"] = round(cal(SWR_CAL, swr), 1)
        return r if po is not None and swr is not None else {}
    def smeter(self, v): return round(cal(S_CAL, v), 1)
    def dig_upper(self): return None
    # ---- everyday commands, in the words the bridge uses for Hamlib
    def read_info(self, age=0.0):
        """IF: frequency, clarifier, mode, tone mode and repeater shift in one answer."""
        if time.time() - self.info_at < age: return self.info
        a = self.ask("IF;", 28)
        m = re.fullmatch(r"IF(.{3})(\d{9})([+-])(\d{4})([01])0([1-9B-D])(.)([012])(\d\d)([012]);", a)
        if m: self.info = {"hz": int(m[2]), "rit": int(m[3] + m[4]) if m[5] == "1" else 0, "md": m[6], "ct": m[8], "os": m[10]}
        else:
            f, md = self.get("FA", 12), self.get("MD0", 5)
            self.info = {"hz": int(f), "md": md} if f and f.isdigit() else {}
        self.info_at = time.time() if self.info else 0.0
        return self.info
    def offset(self, hz):
        """The radio's repeater offset menu for this band (28 / 50 MHz), read only; the factory setting if it can't be read."""
        k = "28" if 28e6 <= hz < 30e6 else "50" if 50e6 <= hz < 54e6 else None
        if k is None: return 0
        if k not in self.offs:
            v = self.get({"28": "EX0904", "50": "EX0905"}[k], 11)        # (other firmware has other numbers: then no 4-digit answer)
            self.offs[k] = (int(v) * 1000, True) if v and v.isdigit() else ({"28": 100_000, "50": 1_000_000}[k], False)
        return self.offs[k][0]
    def rpt(self):
        """Repeater state as the radio reports it, for the panel and the transmit check."""
        i = self.read_info(0.3)
        if not i.get("hz") or not is_fm(i.get("md")): return None
        ct = self.get("CT0", 5); tone = self.get("CN00", 8); dc = self.get("CN01", 8) if ct == "3" else None
        r = {"shift": {"0": "0", "1": "+", "2": "-"}.get(i.get("os")), "offset": self.offset(i["hz"]), "mode": {"0": "none", "1": "tsql", "2": "tone", "3": "dsql"}.get(ct), "hz": i["hz"]}
        if tone and tone.isdigit() and int(tone) < len(TONES): r["tone"] = TONES[int(tone)]
        if dc and dc.isdigit() and int(dc) < len(self.dcs): r["dcs"] = self.dcs[int(dc)]
        return r
    def tx_vfo_hz(self):
        v = self.get("FB", 12); return int(v) if v and v.isdigit() else 0
    def power(self, w=None):
        if w is not None: return self.put(f"PC{w:03d}", "PC", 6, f"{w:03d}")
        v = self.get("PC", 6); return int(v) if v and v.isdigit() else None
    def swap(self):
        a, b = self.get("FA", 12), self.get("FB", 12)
        self.ask("SV;", 0); time.sleep(0.1); self.info_at = 0
        return 0 if None not in (a, b) and self.get("FA", 12) == b else -9
    def line(self, line):
        """-> (code, values) like Hamlib's extended answers, or None for a line this radio leaves to Hamlib (PTT)."""
        t = line.split(); c = t[0]; v = t[-1] if len(t) > 1 else ""
        with self.l:
            if c == "f": i = self.read_info(); return (0, {"Frequency": str(i["hz"])}) if i.get("hz") else (-5, {})
            if c == "m": i = self.read_info(0.25); return (0, {"Mode": DM.get(i.get("md"), "")}) if i.get("md") in DM else (-5, {})
            if c == "t":
                s = self.tx_status(); return (0, {"PTT": "1" if s == "tx" else "0"}) if s else (-5, {})
            if c == "v": return 0, {"VFO": "VFOA"}                        # what the display shows is always VFO A
            if c == "s":
                s = self.get("ST", 4)
                return (0, {"Split": "0" if s == "0" else "1", "TX VFO": "VFOA" if s == "0" else "VFOB"}) if s else (-5, {})
            if c == "j": i = self.read_info(0.25); return (0, {"RIT": str(i.get("rit", 0))}) if i else (-5, {})
            if c == "l":
                s = self.get("SM0", 7); return (0, {"value": str(int(s))}) if s and s.isdigit() else (-5, {})
            if c == "\\get_dcd":
                s = self.get("BY", 5); return (0, {"DCD": s[0]}) if s else (-5, {})
            self.info_at = 0
            if c == "F":
                hz = int(float(v)); return self.put(f"FA{hz:09d}", "FA", 12, f"{hz:09d}") if self.min_hz <= hz <= self.max_hz else -1, {}
            if c == "M":
                k = MD.get(t[1]); return (self.put("MD0" + k, "MD0", 5, k) if k else -1), {}
            if c == "S":                                               # split on / off; the transmit VFO is always B
                on = "1" if t[1] == "1" else "0"; return self.put("ST" + on, "ST", 4, on), {}
            if c == "J":
                hz = max(-9990, min(9990, int(v))); self.ask("RC;", 0)
                if hz: self.ask(f"R{'U' if hz > 0 else 'D'}{abs(hz):04d};", 0)
                r = self.put("CF0" + ("1" if hz else "0") + "0", "CF0", 6, ("1" if hz else "0") + "0")
                if r == 0: r = 0 if self.read_info().get("rit", 0) == hz else -9
                return r, {}
            if c == "R":
                k = {"0": "0", "+": "1", "-": "2"}.get(v); return (self.put("OS0" + k, "OS0", 5, k) if k else -1), {}
            if c == "O":                                               # set in the radio's menu: only checked here
                hz = self.offset(self.read_info().get("hz") or 0); return (0 if int(float(v)) == hz else -9), {}
            if c == "C":
                x = round(int(v) / 10, 1)
                if x not in TONES: return -1, {}
                n = f"{TONES.index(x):03d}"; return self.put("CN00" + n, "CN00", 8, n), {}
            if c == "U" and t[1] in ("TONE", "TSQL"):
                k = {"TONE": "2", "TSQL": "1"}[t[1]] if v == "1" else "0"; return self.put("CT0" + k, "CT0", 5, k), {}
            if c == "U" and t[1] == "LOCK": return self.put("LK" + v, "LK", 4, v), {}
            if c == "D": return (self.put("CT00", "CT0", 5, "0") if v == "0" else -11), {}
            if c == "\\set_dcs_sql":
                if int(v) not in self.dcs: return -1, {}
                n = f"{self.dcs.index(int(v)):03d}"; r = self.put("CN01" + n, "CN01", 8, n)
                return (r or self.put("CT03", "CT0", 5, "3")), {}
            if c == "V": return -11, {}
        return None

RADIOS = {"ft897d": FT897, "ft891": FT891}
def pick(radio, model):
    """--radio / --model -> profile class"""
    if radio: return RADIOS[radio]
    if not model: return FT897
    return next((c for c in RADIOS.values() if model in c.models), Other)

def find_baud(dev):
    """Asks an FT-891 for its identity at each CAT speed. 0 = no answer."""
    try: import serial
    except ImportError: return 0
    for b in (38400, 9600, 4800, 19200):
        try:
            with serial.Serial(dev, b, stopbits=serial.STOPBITS_TWO, timeout=0.35, write_timeout=1) as s:
                for _ in range(2):
                    s.reset_input_buffer(); s.write(b";ID;"); s.flush()
                    if re.search(rb"ID\d{4};", s.read_until(b"ID0650;", 24)): return b
        except (OSError, ValueError): return 0
    return 0
