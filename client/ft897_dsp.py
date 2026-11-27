"""W5PL FT-897D network radio - audio processing for the laptop client (plain Python, nothing to install).

RxDsp   receive: CAT tick remover -> manual notches (2) -> automatic notches -> passband -> noise reduction -> voice squelch ->
        levelling (AGC) -> bass / treble -> [neural noise reduction] -> limiter. Works at 8 kHz inside, 48 kHz 16-bit mono in and out.
Speech  transmit: low cut -> presence -> leveller -> soft clipping -> high cut -> limiter.
Both read their settings from a plain dict, so they can be changed while running (keys, or the web panel).
"""
import array, cmath, ctypes, math, os, struct, sys
from operator import mul

DEFAULTS = {
    # receive
    "volume": -12.0, "mute": False, "nr": 8.0, "low": 100, "high": 2800, "anotch": False, "notch": False,
    "notch_f": 1000, "notch_w": 100, "agc": "off", "bass": 0.0, "treble": 0.0, "squelch": False, "tick": True, "fmsq": 0,
    "neural": False,                                    # noise reduction by RNNoise instead of the standard method
    "sharp": True, "shift": 0,                          # sharp passband edges; shift moves the whole passband (Hz)
    "notch2": False, "notch2_f": 1500, "notch2_w": 100, # a second manual notch
    # transmit
    "drive": -5.0, "mic": 0.0, "comp": 8.0, "presence": 6.0, "txlow": 300, "txhigh": 2800,
}
RANGES = {"volume": (-40, 20), "fmsq": (0, 30), "nr": (0, 24), "low": (50, 2500), "high": (300, 3400), "shift": (-1000, 1000), "notch_f": (100, 3300),
          "notch_w": (20, 400), "notch2_f": (100, 3300), "notch2_w": (20, 400), "bass": (-12, 12), "treble": (-12, 12), "drive": (-20, -1), "mic": (-20, 20),
          "comp": (0, 14), "presence": (0, 12), "txlow": (80, 500), "txhigh": (2000, 3200)}
CHOICES = {"agc": ("off", "slow", "fast")}

def clean(new, old=None):
    """Valid settings only: unknown names and bad values are dropped, numbers are kept inside their range."""
    out = {}
    for k, v in (new or {}).items():
        if k in CHOICES:
            if v in CHOICES[k]: out[k] = v
        elif k in RANGES:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v: continue
            lo, hi = RANGES[k]; v = max(lo, min(hi, v))
            out[k] = int(v) if isinstance(DEFAULTS[k], int) else float(v)
        elif k in DEFAULTS and isinstance(v, bool): out[k] = v
    m = {**DEFAULTS, **(old or {}), **out}
    if m["high"] - m["low"] < 150:                          # never an empty passband
        if "low" in out and "high" not in out: out["low"] = m["high"] - 150
        else: out["high"] = min(3400, m["low"] + 150); out["low"] = min(m["low"], out["high"] - 150)
    return out

# ------------------------------------------------------------------------------------------------ filters
BUTTER4 = (0.5412, 1.3066)
BUTTER8 = (0.5098, 0.6013, 0.9000, 2.5629)

def biquad(kind, fs, f0, q=0.7071, db=0.0):
    """[b0, b1, b2, a1, a2, z1, z2] (RBJ cookbook)"""
    w = 2 * math.pi * min(f0, fs * 0.49) / fs; c, al = math.cos(w), math.sin(w) / (2 * q); A = 10 ** (db / 40)
    if kind == "lp": b = [(1 - c) / 2, 1 - c, (1 - c) / 2]; a = [1 + al, -2 * c, 1 - al]
    elif kind == "hp": b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]; a = [1 + al, -2 * c, 1 - al]
    elif kind == "notch": b = [1, -2 * c, 1]; a = [1 + al, -2 * c, 1 - al]
    elif kind == "peak": b = [1 + al * A, -2 * c, 1 - al * A]; a = [1 + al / A, -2 * c, 1 - al / A]
    else:
        s = 2 * math.sqrt(A) * al; p, m = A + 1, A - 1
        if kind == "lowshelf":
            b = [A * (p - m * c + s), 2 * A * (m - p * c), A * (p - m * c - s)]; a = [p + m * c + s, -2 * (m + p * c), p + m * c - s]
        else:
            b = [A * (p + m * c + s), -2 * A * (m + p * c), A * (p + m * c - s)]; a = [p - m * c + s, 2 * (m - p * c), p - m * c - s]
    return [b[0] / a[0], b[1] / a[0], b[2] / a[0], a[1] / a[0], a[2] / a[0], 0.0, 0.0]

def run(sec, xs):
    b0, b1, b2, a1, a2, z1, z2 = sec; out = []
    for x in xs:                                            # transposed direct form II
        y = b0 * x + z1
        z1 = b1 * x - a1 * y + z2; z2 = b2 * x - a2 * y
        out.append(y)
    sec[5] = z1 if abs(z1) > 1e-9 else 0.0; sec[6] = z2 if abs(z2) > 1e-9 else 0.0
    return out

def retune(old, new):
    """New coefficients, old state: no click when a slider moves."""
    if len(old) != len(new): return new
    for o, n in zip(old, new): n[5], n[6] = o[5], o[6]
    return new

# ------------------------------------------------------------------------------------------------ noise reduction
def _lsa_table():
    """exp(E1(v) / 2) for the log-spectral-amplitude gain, v = 0.001 .. 10 in steps of 0.05 in ln v."""
    t = []; v = 0.001
    while v < 10:
        if v < 1:
            s = -0.5772156649 - math.log(v); term = 1.0
            for k in range(1, 20): term *= -v / k; s -= term / k
        else:
            f = 0.0
            for k in range(30, 0, -1): f = k / (1 + k / (v + f))
            s = math.exp(-v) / (v + f)
        t.append(math.exp(s / 2)); v *= math.exp(0.05)
    return t
LSA = _lsa_table(); LOG_V0 = math.log(0.001)

PRESENT = 0.035        # share of frequency bins 10 dB over the noise (see RxDsp; noise alone stays below this)

def passband(s):
    """The passband actually used: low / high cut moved by the shift, kept inside 50-3400 Hz and at least 150 Hz wide."""
    sh = s.get("shift", 0) or 0; w = max(150, s["high"] - s["low"])
    lo = min(max(50, s["low"] + sh), 3400 - w); hi = min(3400, lo + w)
    return lo, hi

EDGE = 100        # Hz from fully open to fully closed at each sharp edge (raised cosine, -6 dB at the edge itself)
def edge_mask(lo, hi, n, fs):
    """Gain per FFT bin for a passband with sharp edges."""
    out = []
    for k in range(n // 2 + 1):
        f = k * fs / n; g = 1.0
        for e, up in ((lo, True), (hi, False)):
            x = (f - e) / EDGE if up else (e - f) / EDGE           # > 0 inside the passband
            g *= 0.0 if x <= -0.5 else 1.0 if x >= 0.5 else 0.5 + 0.5 * math.sin(math.pi * x)
        out.append(g)
    return out

class NoiseReducer:
    """Hiss reduction for voice at 8 kHz (256-point FFT, 50 % overlap): tracks the noise in every frequency bin with a
    speech-presence estimate and turns down bins that hold only noise (Ephraim-Malah log-spectral gain,
    decision-directed, so what is left does not tinkle), never below the chosen floor."""
    N, HOP = 256, 128
    def __init__(self):
        N = self.N
        self.win = [math.sin(math.pi * (i + 0.5) / N) for i in range(N)]           # sqrt-Hann, in and out
        self.rev = [int(f"{i:08b}"[::-1], 2) for i in range(N)]
        self.tw = [cmath.exp(-2j * math.pi * k / N) for k in range(N // 2)]
        self.hist = [0.0] * self.HOP; self.tail = [0.0] * self.HOP
        self.bins = N // 2 + 1
        self.noise = None; self.frames = 0
        self.spp = [0.5] * self.bins; self.prev = [1.0] * self.bins
        self.presence = 0.0                            # 0 = only noise .. 1 = clearly a signal (for squelch and levelling)
    def fft(self, x):
        N, tw = self.N, self.tw
        X = [x[i] for i in self.rev]; size = 2
        while size <= N:
            half = size // 2; step = N // size
            for k in range(half):
                w = tw[k * step]
                for i in range(k, N, size):
                    t = w * X[i + half]; u = X[i]
                    X[i] = u + t; X[i + half] = u - t
            size *= 2
        return X
    def frame(self, new, floor, lo=8, hi=90, mask=None):
        N, half, win = self.N, self.N // 2, self.win
        x = self.hist + new; self.hist = new
        X = self.fft([v * w for v, w in zip(x, win)])
        P = [X[k].real ** 2 + X[k].imag ** 2 + 1e-3 for k in range(self.bins)]
        self.frames += 1
        if self.noise is None: self.noise = P[:]
        nz, spp, prev = self.noise, self.spp, self.prev
        start = self.frames < 12                                          # first 0.2 s: learn the noise quickly
        exp, log, top = math.exp, math.log, len(LSA) - 1; pres = 0.0
        for k in range(self.bins):
            p, n = P[k], nz[k]; post = p / n
            q = 1 / (1 + 32.6 * exp(-min(50.0, post * 0.9693)))           # how likely is speech in this bin? (15 dB prior)
            if lo <= k < hi and post > 10: pres += 1
            if start: n = 0.8 * n + 0.2 * p
            else:
                spp[k] = 0.9 * spp[k] + 0.1 * q
                if spp[k] > 0.99: q = min(q, 0.99)
                n = 0.8 * n + 0.2 * (q * n + (1 - q) * p)
            nz[k] = n; post = min(1000.0, p / n)
            xi = 0.96 * prev[k] + 0.04 * max(post - 1, 0.0)
            r = xi / (1 + xi); v = r * post
            g = r * (LSA[min(top, max(0, int((log(v) - LOG_V0) * 20)))] if v > 0.001 else LSA[0]) if v < 10 else r
            if g > 1: g = 1.0
            prev[k] = g * g * post
            if g < floor: g = floor
            if mask is not None: g *= mask[k]
            X[k] *= g
            if 0 < k < half: X[N - k] = X[k].conjugate()
        self.presence += (pres / max(1, hi - lo) - self.presence) * 0.2
        y = self.fft([v.conjugate() for v in X])                          # inverse FFT by conjugation
        out = [(y[i].real / N) * win[i] for i in range(N)]
        res = [t + o for t, o in zip(self.tail, out)]
        self.tail = out[self.HOP:]
        return res

class NeuralNR:
    """Neural noise reduction: RNNoise (Xiph.Org / Jean-Marc Valin, BSD licence), a small network trained to tell speech
    from noise. Needs its library next to this file (rnnoise/librnnoise.dylib on a Mac, rnnoise.dll on Windows,
    librnnoise.so on Linux); without it the standard method is used. 48 kHz, 10 ms frames, adds 20-30 ms of delay.
    mix = how much of the original is kept (the Noise reduction slider: 0 dB = all of it, 24 dB = 6 %)."""
    FRAME = 480
    lib = None; why = ""
    @classmethod
    def load(cls):
        if cls.lib is not None or cls.why: return cls.lib
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rnnoise")
        name = {"darwin": "librnnoise.dylib", "win32": "rnnoise.dll"}.get(sys.platform, "librnnoise.so")
        try:
            lib = ctypes.CDLL(os.path.join(here, name))
            lib.rnnoise_create.restype = ctypes.c_void_p; lib.rnnoise_create.argtypes = [ctypes.c_void_p]
            lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
            lib.rnnoise_process_frame.restype = ctypes.c_float
            lib.rnnoise_process_frame.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
            if lib.rnnoise_get_frame_size() != cls.FRAME: raise OSError("unexpected frame size")
            cls.lib = lib
        except (OSError, AttributeError) as e: cls.why = f"{name} could not be loaded ({e})"
        return cls.lib
    def __init__(self):
        self.st = self.load().rnnoise_create(None)
        z = array.array("f", bytes(4 * self.FRAME))
        self.pend = array.array("f"); self.dry = [z, z]; self.vad = 0.0                # RNNoise answers 2 frames later
        self.out = (ctypes.c_float * self.FRAME)()
    def __del__(self):
        if getattr(self, "st", None) and self.lib: self.lib.rnnoise_destroy(self.st); self.st = None
    def process(self, xs, mix):
        """int16-scaled floats in; out the same, two frames (20 ms) later, in 480-sample steps (so sometimes empty)."""
        F = self.FRAME; self.pend.extend(xs); res = []
        while len(self.pend) >= F:
            fr = self.pend[:F]; del self.pend[:F]
            self.vad = self.lib.rnnoise_process_frame(self.st, self.out, (ctypes.c_float * F).from_buffer(fr))
            w = self.out; d = self.dry.pop(0); self.dry.append(fr)
            res += [w[i] + mix * (d[i] - w[i]) for i in range(F)] if mix > 0.001 else list(w)
        return res

# ------------------------------------------------------------------------------------------------ other receive stages
class TickFinder:
    """Finds the ticks that CAT data leaves in the receive audio (the Digirig cable carries both). Each is under 1 ms long and
    wide-band, while the radio's own audio ends at 3 kHz, so what sticks out above 8 kHz is a tick. Works at 48 kHz:
    bridges each one with a smooth curve and reports where it was, for TickRepair to rebuild properly. Only where the audio is
    quiet: louder speech hides a tick, and leaving it alone is then better than any repair."""
    DELAY, STEP = 144, 12                              # 3 ms look-ahead, 0.25 ms steps
    def __init__(self, fs):
        self.hp = [biquad("hp", fs, 8000, q) for q in BUTTER8]; self.buf = [0.0] * self.DELAY; self.flag = [False] * self.DELAY
        self.floor = 20.0; self.n = 0; self.level = [0.0] * self.DELAY; self.rest = []; self.count = 0
    def process(self, xs, on=True):
        """-> (samples, delayed by DELAY), [(start, end)] in absolute sample numbers of what came out"""
        hs = xs; S = self.STEP
        for sec in self.hp: hs = run(sec, hs)
        self.buf += xs; hs = self.rest + hs; base = len(self.flag); k = len(hs) // S
        for i in range(k):
            seg = hs[i * S:i * S + S]; e = math.sqrt(sum(map(mul, seg, seg)) / S)
            hit = on and e > 5 * self.floor + 8
            self.floor += (min(e, 3 * self.floor + 2) - self.floor) * (0.0015 if e > self.floor else 0.006)
            self.flag += [hit] * S; self.level += [e] * S
        self.rest = hs[k * S:]
        n = min(len(self.buf), len(self.flag)) - self.DELAY; spans = []
        if n <= 0: return [], []
        buf, flag = self.buf, self.flag; i = 0; lim = len(flag) - S - 4
        while i < n:
            if not flag[i]: i += 1; continue
            j = i
            while j < lim and any(flag[j:j + S + 1]): j += 1      # joins pieces less than 0.25 ms apart
            a, b = max(2, i - 10), min(j + 10, lim)
            peak = max(self.level[i:max(i + 1, j)]); near = buf[max(0, a - 240):a]
            loud = math.sqrt(sum(map(mul, near, near)) / max(1, len(near))) > 0.8 * peak + 40
            if b - a <= 130 and b + 2 < len(buf) and not loud:  # (a longer burst is not a tick: leave it alone)
                y0, y1 = buf[a - 1], buf[b]; m0 = (buf[a - 1] - buf[a - 2]); m1 = (buf[b + 1] - buf[b]); L = b - a + 1
                m0 = max(-600.0, min(600.0, m0)) * 0.3 * L; m1 = max(-600.0, min(600.0, m1)) * 0.3 * L
                for t in range(a, b):
                    u = (t - a + 1) / L; u2 = u * u; u3 = u2 * u
                    buf[t] = (2 * u3 - 3 * u2 + 1) * y0 + (u3 - 2 * u2 + u) * m0 + (-2 * u3 + 3 * u2) * y1 + (u3 - u2) * m1
                spans.append((self.n + a, self.n + b)); self.count += 1
            for t in range(i, max(i + 1, min(j, len(flag)))): flag[t] = False
            i = max(j, i + 1)
        out = buf[:n]; self.buf = buf[n:]; self.flag = flag[n:]; self.level = self.level[n:]; self.n += n
        return out, spans

class TickRepair:
    """Rebuilds the marked samples at 8 kHz from the audio around them (least-squares autoregressive interpolation)."""
    P, HIST, AHEAD = 14, 256, 40
    def __init__(self): self.buf = [0.0] * self.HIST; self.pos = 0; self.todo = []      # buf[HIST] is sample number pos
    def model(self, x):
        P = self.P; n = len(x); rr = [sum(map(mul, x[k:], x[:n - k])) for k in range(P + 1)]
        e = rr[0] * 1.0001 + 1.0; a = [1.0] + [0.0] * P
        for i in range(1, P + 1):
            k = -(rr[i] + sum(a[j] * rr[i - j] for j in range(1, i))) / e
            a[1:i + 1] = [a[j] + k * a[i - j] for j in range(1, i)] + [k]
            e *= 1 - k * k
            if e <= 0: break
        return a
    def fix(self, i, j):
        P, x, n = self.P, self.buf, j - i
        a = self.model(x[i - 200:i]); b = [sum(a[k] * a[k + l] for k in range(P + 1 - l)) for l in range(P + 1)]
        M = [[b[abs(p - q)] if abs(p - q) <= P else 0.0 for q in range(n)] for p in range(n)]; v = []
        for p in range(n):
            t = i + p; acc = 0.0
            for l in range(1, P + 1):
                if t - l < i: acc += b[l] * x[t - l]
                if t + l >= j: acc += b[l] * x[t + l]
            v.append(-acc)
        for c in range(n):                                     # symmetric positive definite: plain elimination
            d = M[c][c]
            if d <= 1e-12: return
            for q in range(c + 1, n):
                w = M[q][c] / d
                if w:
                    row, top = M[q], M[c]
                    for z in range(c, n): row[z] -= w * top[z]
                    v[q] -= w * v[c]
        for c in range(n - 1, -1, -1):
            v[c] = (v[c] - sum(M[c][z] * v[z] for z in range(c + 1, n))) / M[c][c]
        lim = 1.5 * max(map(abs, x[i - 100:i] + x[j:j + P]), default=0) + 1
        x[i:j] = [max(-lim, min(lim, y)) for y in v]
    def process(self, ys, spans):
        self.buf += ys; self.todo += spans
        end = self.pos + len(self.buf) - self.HIST
        while self.todo and self.todo[0][1] + self.P + 2 <= end:
            s, e = self.todo.pop(0); i, j = s - self.pos + self.HIST, e - self.pos + self.HIST
            if 2 <= j - i <= 24 and i >= 220: self.fix(i, j)
        n = len(self.buf) - self.HIST - self.AHEAD
        if n <= 0: return []
        out = self.buf[self.HIST:self.HIST + n]; self.buf = self.buf[n:]; self.pos += n
        return out

class AutoNotch:
    """Finds steady tones (carriers, whistles) and puts a narrow notch on each, up to four. A tone must stand
    8 dB out of its surroundings in 88 % of the last second or two: voices move and pause too much for that."""
    N, MAX = 1024, 4
    def __init__(self):
        N = self.N
        self.win = [0.5 - 0.5 * math.cos(2 * math.pi * i / N) for i in range(N)]
        self.rev = [int(f"{i:010b}"[::-1], 2) for i in range(N)]
        self.tw = [cmath.exp(-2j * math.pi * k / N) for k in range(N // 2)]
        self.buf = [0.0] * N; self.new = 0; self.score = [0.0] * (N // 2); self.notches = {}     # slot -> [freq, sections, misses]
    fft = NoiseReducer.fft
    def analyse(self):
        N = self.N; X = self.fft([a * b for a, b in zip(self.buf, self.win)])
        D = [10 * math.log10(X[k].real ** 2 + X[k].imag ** 2 + 1e-3) for k in range(N // 2)]; sc = self.score
        for k in range(14, 440):                                         # 110 Hz .. 3.4 kHz
            near = sorted(D[max(0, k - 14):k - 3] + D[k + 4:k + 15]); d = D[k] - near[len(near) // 2]
            sc[k] += ((1.0 if d > 8 else 0.0) - sc[k]) * 0.06             # 64 ms steps: about 1 s
        peaks = []
        for k in range(15, 439):
            if sc[k] > 0.88 and D[k] >= D[k - 1] and D[k] >= D[k + 1]:
                a, b, c = D[k - 1], D[k], D[k + 1]; den = a - 2 * b + c
                off = 0.5 * (a - c) / den if den < -1e-6 else 0.0
                peaks.append((D[k], (k + max(-0.5, min(0.5, off))) * 8000 / N))
        peaks.sort(reverse=True); peaks = peaks[:self.MAX]
        for slot, n in list(self.notches.items()):
            hit = [p for p in peaks if abs(p[1] - n[0]) < 12]
            if hit: peaks.remove(hit[0]); n[2] = 0; f = n[0] + (hit[0][1] - n[0]) * 0.2
            else:
                n[2] += 1; f = n[0]
                if n[2] > 24: del self.notches[slot]; continue
            if abs(f - n[0]) > 0.2: n[0] = f; n[1] = retune(n[1], self.make(f))
        for sc_, f in peaks:
            free = [i for i in range(self.MAX) if i not in self.notches]
            if free: self.notches[free[0]] = [f, self.make(f), 0]
    @staticmethod
    def make(f): return [biquad("notch", 8000, f, f / 45), biquad("notch", 8000, f, f / 30)]
    def process(self, xs):
        self.buf = (self.buf + list(xs))[-self.N:]; self.new += len(xs)
        if self.new >= 512: self.new = 0; self.analyse()
        for n in self.notches.values():
            for sec in n[1]: xs = run(sec, xs)
        return xs

class Leveller:
    """Keeps weak and strong stations at about the same loudness. Adapts only while a signal is present, so the noise
    between words is not turned up."""
    def __init__(self): self.env = 0.0; self.g = 1.0
    def process(self, xs, speed, presence):
        if speed == "off":
            if abs(self.g - 1) < 0.01: self.g = 1.0; self.env = 0.0; return xs
            want, up, active = 1.0, 0.0006, True
        else: want, up, active = None, (0.00004 if speed == "slow" else 0.0002), presence > PRESENT
        env, g = self.env, self.g; target = 32768 * 10 ** (-14 / 20); lo = 0.1; out = []
        for x in xs:
            ax = x if x >= 0 else -x
            env += (ax - env) * (0.02 if ax > env else 0.0008)
            if want is not None: gd = want
            else: gd = max(lo, min(6.0, target / env)) if env > 30 else g
            if gd < g: g += (gd - g) * 0.01
            elif active: g += (gd - g) * up
            out.append(x * g)
        self.env, self.g = env, g
        return out

class Squelch:
    """Voice squelch: mutes (-40 dB) when nothing stands out of the noise for about a second."""
    def __init__(self): self.g = 1.0; self.hang = 0
    def process(self, xs, on, presence):
        if not on:
            self.hang = 0
            if self.g > 0.999: self.g = 1.0; return xs
            goal = 1.0
        else:
            if presence > PRESENT: self.hang = 60              # blocks of 16 ms
            elif self.hang: self.hang -= 1
            goal = 1.0 if self.hang else 0.01
        g = self.g; k = 0.02 if goal > g else 0.002; out = []
        for x in xs: g += (goal - g) * k; out.append(x * g)
        self.g = g
        return out

class FmSquelch:
    """Noise squelch for FM. With no signal an FM receiver hisses above the voice too; a signal quiets that, and speech
    does not reach it. Measured on the FT-897's data jack, 3.4-6 kHz: -49 dBFS with no signal, -79 dBFS on a full-quieting
    one (the jack ignores the radio's own squelch). Setting 0 = off, 1..30: opens below -46 - setting dBFS. SSB, AM and CW
    audio has nothing up there, so it stays open on them. The median over 1.5 ms pieces ignores CAT ticks."""
    def __init__(self, fs):
        self.dec = fs // 16000; self.ph = 0
        self.f = [biquad("hp", 16000, 3400, q) for q in BUTTER8] + [biquad("lp", 16000, 6000)]
        self.lvl = -80.0; self.open = True; self.g = 1.0; self.rest = []; self.parts = []
    def measure(self, xs, setting):
        ys = xs[self.ph::self.dec]; self.ph = (self.ph - len(xs)) % self.dec
        for sec in self.f: ys = run(sec, ys)
        ys = self.rest + ys; k = len(ys) // 24
        self.parts += [sum(map(mul, ys[i * 24:i * 24 + 24], ys[i * 24:i * 24 + 24])) / 24 for i in range(k)]; self.rest = ys[k * 24:]
        while len(self.parts) >= 13:
            p = sorted(self.parts[:13])[6]; del self.parts[:13]
            db = 10 * math.log10(p / 32768 ** 2 + 1e-12) + 0.2
            self.lvl += (db - self.lvl) * (0.5 if db > self.lvl else 0.35)
        thr = -46 - setting
        if not setting: self.open = True
        elif self.open: self.open = self.lvl < thr + 2
        else: self.open = self.lvl < thr
    def gate(self, xs):
        goal = 1.0 if self.open else 0.0; g = self.g
        if g == goal: return xs if g else [0.0] * len(xs)
        out = []
        for x in xs: g += (goal - g) * 0.03; out.append(x * g)
        self.g = goal if abs(g - goal) < 0.003 else g
        return out

class RxDsp:
    def __init__(self, settings, fs=48000):
        self.s = settings; self.fs = fs; self.dec = fs // 8000
        self.aa = [biquad("lp", fs, 3500, q) for q in BUTTER8]; self.up = [biquad("lp", fs, 3500, q) for q in BUTTER8]
        self.phase = 0; self.pend = []; self.key = None; self.tf, self.tr, self.fm = TickFinder(fs), TickRepair(), FmSquelch(fs)
        self.hp, self.lp, self.nf, self.eq = [], [], [], []
        self.an, self.nr, self.lev, self.sq = AutoNotch(), NoiseReducer(), Leveller(), Squelch()
        self.nn = None
        self.reconfigure()
    def reconfigure(self):
        s = self.s
        key = (s["low"], s["high"], s.get("shift", 0), s.get("sharp", True), s["notch"], s["notch_f"], s["notch_w"], s.get("notch2"), s.get("notch2_f"), s.get("notch2_w"), s["bass"], s["treble"])
        if key == self.key: return
        self.key = key
        lo, hi = passband(s); self.edges = (lo, hi)
        if s.get("sharp", True):                  # the FFT mask makes the edges; the filters only clear what is far outside
            self.mask = edge_mask(lo, hi, NoiseReducer.N, 8000)
            self.hp = retune(self.hp, [biquad("hp", 8000, max(40, lo - 150), q) for q in BUTTER4])
            self.lp = retune(self.lp, [biquad("lp", 8000, min(3700, hi + 250), q) for q in BUTTER8])
        else:
            self.mask = None
            self.hp = retune(self.hp, [biquad("hp", 8000, lo, q) for q in BUTTER4])
            self.lp = retune(self.lp, [biquad("lp", 8000, hi, q) for q in BUTTER8])
        self.nf = retune(self.nf, [biquad("notch", 8000, s[k + "_f"], max(0.5, s[k + "_f"] / s[k + "_w"])) for k in ("notch", "notch2") if s.get(k)])
        eq = []
        if abs(s["bass"]) > 0.1: eq.append(biquad("lowshelf", 8000, 500, 0.7071, s["bass"]))
        if abs(s["treble"]) > 0.1: eq.append(biquad("highshelf", 8000, 1500, 0.7071, s["treble"]))
        self.eq = retune(self.eq, eq)
    def process(self, data):
        """48 kHz 16-bit in, 48 kHz 16-bit out (comes out in 16 ms steps, so sometimes empty)."""
        s = self.s; self.reconfigure()
        xs = [float(v) for v in struct.unpack(f"<{len(data) // 2}h", data)]
        self.fm.measure(xs, s["fmsq"])
        xs, spans = self.tf.process(xs, s["tick"])
        for sec in self.aa: xs = run(sec, xs)
        ys = xs[self.phase::self.dec]; self.phase = (self.phase - len(xs)) % self.dec
        # 8 kHz sample m is input sample 6 m; the low-pass above delays by about 0.23 ms and smears a little
        ys = self.tr.process(ys, [(int((a + 4) / self.dec), int((b + 34) / self.dec) + 1) for a, b in spans])
        for sec in self.nf: ys = run(sec, ys)
        if s["anotch"]: ys = self.an.process(ys)
        elif self.an.notches: self.an = AutoNotch()
        for sec in self.hp: ys = run(sec, ys)
        for sec in self.lp: ys = run(sec, ys)
        self.pend.extend(ys); out = []
        hop = NoiseReducer.HOP; floor = 10 ** (-s["nr"] / 20)
        neural = s.get("neural") and s["nr"] > 0 and NeuralNR.load() is not None
        if neural and self.nn is None: self.nn = NeuralNR()
        elif not neural and self.nn is not None: self.nn = None
        nfloor = 1.0 if neural else floor                                   # neural: the standard method only measures
        lo = max(2, int(self.edges[0] / 31.25) + 1); hi = min(105, max(lo + 4, int(self.edges[1] * 0.9 / 31.25)))
        while len(self.pend) >= hop:
            if self.fm.g == 0 and not self.fm.open:                        # closed: silence, and the noise reduction learns nothing
                del self.pend[:hop]; out += [0.0] * hop; continue
            b = self.nr.frame(self.pend[:hop], nfloor, lo, hi, self.mask); del self.pend[:hop]
            b = self.fm.gate(b)
            b = self.sq.process(b, s["squelch"], self.nr.presence)
            b = self.lev.process(b, s["agc"], self.nr.presence)
            for sec in self.eq: b = run(sec, b)
            out += b
        if not out: return b""
        d = self.dec; zs = [0.0] * (len(out) * d)
        zs[::d] = [v * d for v in out]
        for sec in self.up: zs = run(sec, zs)
        if self.nn is not None:
            zs = self.nn.process(zs, floor)
            if not zs: return b""
        return struct.pack(f"<{len(zs)}h", *[int(v) if -26000 < v < 26000 else int(math.copysign(26000 + 6000 * math.tanh((abs(v) - 26000) / 6000), v))
                                             for v in zs])

# ------------------------------------------------------------------------------------------------ transmit
class Speech:
    """Speech processor for voice through the radio's DATA jack (which bypasses the radio's own processor): low cut
    (4th order; the radio's filter removes voice bass anyway, and it would use up the peaks) -> presence around 2 kHz ->
    leveller (fast down, slow up; does not lift background noise) -> soft clipping ("comp" dB: denser speech) ->
    high cut -> limiter at "drive" dBFS."""
    def __init__(self, settings, fs):
        self.s = settings; self.fs = fs; self.key = None; self.pre_eq, self.post = [], []
        k = lambda sec: 1 - math.exp(-1 / (sec * fs))
        self.att, self.rel, self.down, self.up = k(0.002), k(0.12), k(0.005), k(0.5)
        self.ref = 32767 * 10 ** (-10 / 20)            # internal working level, then scaled to the ceiling
        self.target = self.ref * 0.8
        self.gate = 32767 * 10 ** (-50 / 20)           # quieter than this = not speech: don't turn it up
        self.maxg = 10 ** (18 / 20)                    # at most +18 dB for a quiet voice
        self.env = 0.0; self.g = 1.0
        self.reset_stats()
    def reset_stats(self): self.sq_in = self.sq_out = 0.0; self.pk_in = self.pk_out = 0.0; self.n = 0
    def reconfigure(self):
        s = self.s; key = (s["txlow"], s["txhigh"], s["presence"])
        if key == self.key: return
        self.key = key; fs = self.fs
        self.pre_eq = retune(self.pre_eq, [biquad("hp", fs, s["txlow"], q) for q in BUTTER4] + [biquad("peak", fs, 2000, 0.8, s["presence"])])
        self.post = retune(self.post, [biquad("lp", fs, s["txhigh"], q) for q in BUTTER4])
    def process(self, data):
        self.reconfigure(); s = self.s
        n = len(data) // 2
        raw = struct.unpack(f"<{n}h", data); pre = 10 ** (s["mic"] / 20)
        xs = [x * pre for x in raw]
        self.sq_in += sum(map(mul, xs, xs)); self.pk_in = max(self.pk_in, max(map(abs, raw), default=0))
        for sec in self.pre_eq: xs = run(sec, xs)
        env, g, att, rel, down, up = self.env, self.g, self.att, self.rel, self.down, self.up
        target, gate, maxg = self.target, self.gate, self.maxg; k = 10 ** (s["comp"] / 20) / self.ref
        tanh = math.tanh; ys = []
        for y in xs:
            ay = y if y >= 0 else -y
            env += (ay - env) * (att if ay > env else rel)
            gd = target / env if env > gate else g                        # pauses: hold the gain (no noise pumping)
            if gd > maxg: gd = maxg
            g += (gd - g) * (down if gd < g else up)
            ys.append(tanh(y * g * k))
        self.env, self.g = env, g
        for sec in self.post: ys = run(sec, ys)
        c = 32767 * 10 ** (s["drive"] / 20)
        out = [int(c * max(-1.0, min(1.0, 0.95 * y))) for y in ys]        # clip only where the high cut overshoots
        self.sq_out += sum(map(mul, out, out)); self.pk_out = max(self.pk_out, max(map(abs, out), default=0)); self.n += n
        return struct.pack(f"<{n}h", *out)
    def stats(self):
        db = lambda v: round(20 * math.log10(max(v, 1) / 32768), 1); n = max(1, self.n)
        return {"mic": db((self.sq_in / n) ** 0.5), "mic_peak": db(self.pk_in), "sent": db((self.sq_out / n) ** 0.5), "sent_peak": db(self.pk_out)}
    def report(self):
        if not self.n: return ""
        t = self.stats(); self.reset_stats()
        return f"mic {t['mic']:.0f} dBFS average / {t['mic_peak']:.0f} peak -> sent {t['sent']:.0f} / {t['sent_peak']:.0f} dBFS"
