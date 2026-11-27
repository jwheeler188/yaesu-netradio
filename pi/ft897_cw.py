"""W5PL FT-897D network radio - CW through DIG (used by ft897-netbridge.py).

The FT-897 can't send CW over CAT, and keying its KEY jack needs a wire. Instead the radio sits in DIG (USER-U) with its dial
`pitch` Hz below the carrier, and the Pi plays a keyed sine at `pitch` Hz into the Digirig: on the air that is one carrier at
dial + pitch, switched on and off, the same signal as CW from the KEY jack. The edges are shaped (5 ms raised cosine) so the
keying is click-free and the signal stays narrow.

Timeline: every key-down and key-up is an event at an absolute time (time.time() on the Pi). The panel stamps the operator's
key presses with its own clock; the first press of a sending session fixes the offset between the two clocks plus a playout
delay, so the network's ups and downs never change the length of a dit. Typed text is turned into events here.

Decoder: the received audio around the pitch, 8 kHz, a 20 ms sliding DFT every 5 ms, an adaptive threshold, and a dit length
that follows the other station's speed.
"""
import cmath, math, threading, time

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
    "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
    "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...", "8": "---..",
    "9": "----.", ".": ".-.-.-", ",": "--..--", "?": "..--..", "/": "-..-.", "=": "-...-", "+": ".-.-.", "-": "-....-",
    "'": ".----.", "(": "-.--.", ")": "-.--.-", ":": "---...", ";": "-.-.-.", '"': ".-..-.", "@": ".--.-.", "!": "-.-.--",
    "&": ".-...",
}
PROSIGNS = {"AR": ".-.-.", "SK": "...-.-", "BT": "-...-", "KN": "-.--.", "AS": ".-...", "BK": "-...-.-", "SN": "...-.", "CT": "-.-.-"}
DECODE = {v: k for k, v in MORSE.items()}
DECODE.update({".-.-.": "<AR>", "...-.-": "<SK>", "-.--.": "<KN>", "-...-.-": "<BK>", "........": "<HH>"})
DECODE["-...-"] = "="

DELAY = 0.25          # s from a key press on the panel to the radio: room for network jitter
EDGE = 0.005          # s rise and fall


def clean_text(s, limit=500):
    """Upper case; only what Morse can send; prosigns written <AR>. Unknown characters are dropped."""
    out, i, s = [], 0, str(s or "").upper()
    while i < len(s) and len(out) < limit:
        if s[i] == "<":
            j = s.find(">", i)
            if j > i and s[i + 1:j] in PROSIGNS: out.append(s[i:j + 1]); i = j + 1; continue
        if s[i] in MORSE: out.append(s[i])
        elif s[i] in " \n\t": out.append(" ")
        i += 1
    return "".join(out)


def tokens(s):
    """'CQ <AR>' -> ['C', 'Q', ' ', '<AR>']"""
    out, i = [], 0
    while i < len(s):
        if s[i] == "<":
            j = s.find(">", i)
            if j > i: out.append(s[i:j + 1]); i = j + 1; continue
        out.append(s[i]); i += 1
    return out


def code_of(tok):
    return PROSIGNS.get(tok[1:-1]) if tok.startswith("<") else MORSE.get(tok)


def char_events(tok, t, wpm, farns=0):
    """Key events for one character starting at t. -> (events, time after it incl. the gap to the next one)
    farns: a slower overall speed (Farnsworth): characters at wpm, gaps stretched."""
    dit = 1.2 / wpm
    gap = dit if not farns or farns >= wpm else (60 / farns - 31 * dit) / 19        # (PARIS: 31 dit-times of characters, 19 of gaps)
    if tok == " ": return [], t + 4 * gap       # word space: 7 in all with the 3 after the previous character
    code = code_of(tok)
    if not code: return [], t
    ev = []
    for k, c in enumerate(code):
        n = 3 if c == "-" else 1
        ev.append((t, True)); ev.append((t + n * dit, False))
        t += n * dit + dit
    return ev, t - dit + 3 * gap


class Timeline:
    """Key events in Pi time, in order. Thread-safe."""
    def __init__(self):
        self.ev = []; self.l = threading.Lock(); self.offset = None; self.seen = 0.0; self.down_since = None
        self.end = 0.0                          # time of the last event
    def clear(self, now):
        with self.l:
            st = self.state_at(now)
            self.ev = [(now, False)] if st else []
            self.end = now; self.offset = None; self.down_since = None
    def state_at(self, t):
        """Key down at t? (lock held or not: read only)"""
        s = False
        for et, d in self.ev:
            if et > t: break
            s = d
        return s
    def add_panel(self, evs, now, delay=DELAY):
        """Events from the panel: [[ms, 1|0], ...] on the panel's clock. -> number accepted"""
        n = 0
        with self.l:
            self.seen = now
            for x in evs:
                try: t, d = float(x[0]) / 1000, bool(x[1])
                except (TypeError, ValueError, IndexError): continue
                if self.offset is None or not self.ev and now - self.end > 2: self.offset = now + delay - t
                at = t + self.offset
                if at < now + 0.02:              # arrived late: everything from here on a little later
                    self.offset += now + 0.03 - at; at = now + 0.03
                if at < self.end: at = self.end   # never out of order
                self.ev.append((at, d)); self.end = at; n += 1
                if d: self.down_since = self.down_since or at
                else: self.down_since = None
        return n
    def add(self, evs):
        with self.l:
            for at, d in evs:
                at = max(at, self.end); self.ev.append((at, d)); self.end = at
    def prune(self, before):
        """Drops events already played (keeps the last one: it is the current state)."""
        with self.l:
            k = 0
            while k + 1 < len(self.ev) and self.ev[k + 1][0] <= before: k += 1
            if k: del self.ev[:k]
            if self.ev and len(self.ev) == 1 and not self.ev[0][1] and self.ev[0][0] <= before: self.ev = []
    def busy(self, now):
        """Anything still to play, or the key down."""
        with self.l: return bool(self.ev) and (self.end > now or self.ev[-1][1])
    def last_up(self):
        with self.l: return self.end if self.ev and not self.ev[-1][1] else None
    def force_up(self, at):
        with self.l:
            if self.ev and self.ev[-1][1]: at = max(at, self.end); self.ev.append((at, False)); self.end = at
            self.down_since = None


class Tone:
    """The keyed sine for the sound card: frame(t0, n) gives n samples S16LE whose first one is heard at time t0."""
    def __init__(self, rate=48000):
        self.rate = rate; self.phase = 0.0; self.env = 0.0
        self.table = [math.sin(2 * math.pi * i / 4096) for i in range(4097)]
    def frame(self, t0, n, timeline, pitch, amp):
        rate = self.rate; step = 1.0 / (EDGE * rate); inc = pitch * 4096 / rate
        with timeline.l: ev = list(timeline.ev)
        # key state at each sample: walk the events
        k = 0; s = False
        while k < len(ev) and ev[k][0] <= t0: s = ev[k][1]; k += 1
        nxt = int((ev[k][0] - t0) * rate) if k < len(ev) else n + 1
        out = bytearray(2 * n); tab = self.table; ph = self.phase; env = self.env; full = int(amp * 32767)
        for i in range(n):
            while i >= nxt:
                s = ev[k][1]; k += 1
                nxt = int((ev[k][0] - t0) * rate) if k < len(ev) else n + 1
            if s and env < 1.0: env = min(1.0, env + step)
            elif not s and env > 0.0: env = max(0.0, env - step)
            if env > 0.0:
                g = 0.5 - 0.5 * math.cos(math.pi * env)           # raised cosine edge
                v = int(full * g * tab[int(ph)])
                out[2 * i] = v & 0xFF; out[2 * i + 1] = (v >> 8) & 0xFF
            ph += inc
            if ph >= 4096: ph -= 4096
        self.phase = ph; self.env = env
        return bytes(out)


class Decoder:
    """Feed 8 kHz samples (list of ints); text comes out in self.text. f = the tone it listens to."""
    RATE = 8000; BLK = 40                        # 5 ms blocks; 4 of them = the 20 ms window
    def __init__(self, f=700, wpm=20, auto=True):
        self.f = float(f); self.auto = auto; self.dit = 1.2 / wpm; self.reset_signal()
        self.text = ""; self.n = 0; self.buf = []; self.hist = []; self.cand = None
        self.l = threading.Lock()
    SQ = 4.0                                     # marks must average this many times the noise (amplitude) to be copied
    def reset_signal(self):
        self.win = []; self.noise = None; self.peak = 0.0; self.on = False; self.run = 0; self.code = ""; self.spaced = 2
        self.level = 0.0; self.snr = 0.0; self.sig = 0.0; self.msum = 0.0; self.gaps = []; self.open = False; self.quiet = 0.0
    def set_f(self, f):
        if abs(f - self.f) > 1: self.f = float(f); self.reset_signal()
    def wpm(self): return round(1.2 / self.dit)
    def emit(self, s):
        with self.l: self.text = (self.text + s)[-600:]
    def feed(self, smp):
        self.buf.extend(smp); self.hist.extend(smp)
        if len(self.hist) > 4096: del self.hist[:len(self.hist) - 4096]
        B = self.BLK; w = 2 * math.pi * self.f / self.RATE
        while len(self.buf) >= B:
            blk = self.buf[:B]; del self.buf[:B]
            z = 0j; n0 = self.n
            c, s = math.cos(w), math.sin(w)
            ph = cmath.exp(-1j * w * n0)
            rot = complex(c, -s)
            for x in blk: z += x * ph; ph *= rot
            self.n += B
            self.win.append(z)
            if len(self.win) > 4: self.win.pop(0)
            self.step(abs(sum(self.win)))
    def step(self, m):
        dt = self.BLK / self.RATE
        if self.noise is None: self.noise = m; self.peak = m
        # noise: the average level with the key up (leaning low). sig: the average level of recent marks (falls back to the peak
        # until marks have been seen). Copy only while marks stand clearly above the noise: noise spikes are short and weak.
        if not self.on: self.noise += (m - self.noise) * (0.05 if m < self.noise else 0.01)
        self.peak = m if m > self.peak else self.peak + (m - self.peak) * 0.005
        self.peak = max(self.peak, self.noise)
        ref = self.sig if self.sig > self.noise else self.peak
        self.level = m; self.snr = ref / max(self.noise, 1e-9)
        span = ref - self.noise
        hi, lo = self.noise + 0.55 * span, self.noise + 0.35 * span
        on = self.on
        if self.snr < 2.5: on = False
        elif not on and m > hi: on = True
        elif on and m < lo: on = False
        if on: self.msum += m
        if on != self.on:
            length = self.run * dt
            if self.on: self.mark(length, self.msum / max(self.run, 1))
            else: self.gap(length)
            self.on = on; self.run = 0; self.msum = 0.0
        self.run += 1
        if not self.on:
            self.space(self.run * dt)
            if self.run * dt > 4: self.sig += (self.noise - self.sig) * 0.002      # long silence: the station has gone
    def mark(self, t, level):
        if t < 0.25 * self.dit or t < 0.012: return              # a click or a noise spike
        if t > 12 * self.dit: return                             # a carrier, not Morse
        self.sig = level if self.sig <= self.noise else 0.7 * self.sig + 0.3 * level
        self.open = self.sig > self.SQ * self.noise
        if not self.open: self.code = ""; return
        if t < 2 * self.dit: self.code += "."; self.dit = 0.85 * self.dit + 0.15 * t
        else: self.code += "-"; self.dit = 0.85 * self.dit + 0.15 * t / 3
        self.dit = min(1.2 / 5, max(1.2 / 60, self.dit)); self.spaced = 0
        if len(self.code) > 9: self.code = ""
    def gap(self, t):
        """A gap just ended: learn this station's letter spacing (many hand senders leave far more than 3 dits)."""
        if self.open and t > 2 * self.dit and t < 40 * self.dit:
            self.gaps.append(t); del self.gaps[:-30]
    def word_gap(self):
        if len(self.gaps) < 6: return 5 * self.dit
        g = sorted(self.gaps); cg = g[int(len(g) * 0.35)]
        return max(5 * self.dit, 1.2 * cg)
    def space(self, t):
        if self.code and t > 2 * self.dit:
            if self.open: self.emit(DECODE.get(self.code, "*"))
            self.code = ""; self.spaced = 1
        elif self.spaced == 1 and t > self.word_gap():
            self.emit(" "); self.spaced = 2
    def track(self, centre):
        """Called every 0.5 s with auto on: moves to a clearly stronger tone near the pitch once it is seen twice."""
        f, r, here = self.find(centre)
        if f is None or r < 8 or here > 0.25 or abs(f - self.f) <= 15: self.cand = None; return
        if self.cand is not None and abs(self.cand - f) <= 20: self.set_f(f); self.cand = None
        else: self.cand = f
    def find(self, centre, span=200, step=20):
        """The strongest tone within centre +- span in the last 0.25 s: -> (Hz, how much stronger than the median)"""
        x = self.hist[-2048:]
        if len(x) < 1024: return None, 0, 1
        pw = []
        for k in range(-span // step, span // step + 1):
            f = centre + k * step; w = 2 * math.pi * f / self.RATE; c = 2 * math.cos(w); s1 = s2 = 0.0
            for v in x: s0 = v + c * s1 - s2; s2 = s1; s1 = s0
            pw.append((s1 * s1 + s2 * s2 - c * s1 * s2, f))
        best = max(pw); med = sorted(p for p, _ in pw)[len(pw) // 2]
        here = max((p for p, f in pw if abs(f - self.f) <= step / 2 + 1), default=0)      # the tone it listens to now, against the best
        return best[1], best[0] / max(med, 1e-9), here / max(best[0], 1e-9)
