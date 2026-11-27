"""W5PL FT-897D network radio - who may use the radio and where it may transmit (used by ft897-netbridge.py).

settings.json   what the administration page sets: band locks, blocked ranges, high-SWR shutdown, control rules
passwords.json  scrypt hashes of the administrator and operator passwords. Forgotten? Delete this file on the Pi.
"""
import hashlib, hmac, ipaddress, json, os, secrets, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "settings.json"); PASSWORDS = os.path.join(HERE, "passwords.json")
lock = threading.RLock()

# US band edges. Others edit them on the administration page.
BANDS = [("160", 1_800_000, 2_000_000), ("80", 3_500_000, 4_000_000), ("60", 5_300_000, 5_410_000), ("40", 7_000_000, 7_300_000),
         ("30", 10_100_000, 10_150_000), ("20", 14_000_000, 14_350_000), ("17", 18_068_000, 18_168_000),
         ("15", 21_000_000, 21_450_000), ("12", 24_890_000, 24_990_000), ("10", 28_000_000, 29_700_000),
         ("6", 50_000_000, 54_000_000), ("2m", 144_000_000, 148_000_000), ("70cm", 420_000_000, 450_000_000)]
LOCKS = ("txaudio", "tone", "repeaters")
TX_AUDIO = ("drive", "mic", "comp", "presence", "txlow", "txhigh")
DEFAULT = {"tx": True, "inside": True, "bands": [{"name": n, "lo": lo, "hi": hi, "tx": True, "note": ""} for n, lo, hi in BANDS],
           "blocks": [], "swr": {"on": True, "flag": True, "level": 4, "reads": 3, "reset": "anyone"},
           "tx_timeout": 300, "need_control": False, "answer_s": 30, "no_answer": "grant", "owner_timeout": 120,
           "max_drive": -1, "mic": None, "spk": None, "locks": {k: False for k in LOCKS}, "hosts": [],
           "modes": {}, "hide_locked": False,             # modes: mode -> reason, for the modes locked for transmitting
           "aprs": {"digi": "fillin", "aliases": "", "igate": "off", "server": "noam.aprs2.net"}, "maps_max_mb": 2000}

def num(v, lo, hi, d):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v: return d
    return int(max(lo, min(hi, v)))

def text(v, n): return str(v or "").strip()[:n]

def clean(j, old=None):
    """Valid settings only: anything missing or wrong keeps its old value."""
    c = json.loads(json.dumps(old or DEFAULT))
    if not isinstance(j, dict): return c
    for k in ("tx", "inside", "need_control", "hide_locked"):
        if isinstance(j.get(k), bool): c[k] = j[k]
    c["tx_timeout"] = num(j.get("tx_timeout"), 10, 600, c["tx_timeout"])
    c["answer_s"] = num(j.get("answer_s"), 10, 300, c["answer_s"])
    c["owner_timeout"] = num(j.get("owner_timeout"), 30, 3600, c["owner_timeout"])
    c["max_drive"] = num(j.get("max_drive"), -20, -1, c["max_drive"])
    c["maps_max_mb"] = num(j.get("maps_max_mb"), 50, 100_000, c["maps_max_mb"])
    for k, hi in (("mic", 35), ("spk", 37)):                 # None = leave the sound card as it is
        if k in j: c[k] = None if j[k] is None else num(j[k], 0, hi, c[k])
    if j.get("no_answer") in ("grant", "refuse"): c["no_answer"] = j["no_answer"]
    s = j.get("swr")
    if isinstance(s, dict):
        for k in ("on", "flag"):
            if isinstance(s.get(k), bool): c["swr"][k] = s[k]
        c["swr"]["level"] = num(s.get("level"), 1, 15, c["swr"]["level"]); c["swr"]["reads"] = num(s.get("reads"), 1, 20, c["swr"]["reads"])
        if s.get("reset") in ("anyone", "admin"): c["swr"]["reset"] = s["reset"]
    if isinstance(j.get("locks"), dict):
        for k in LOCKS:
            if isinstance(j["locks"].get(k), bool): c["locks"][k] = j["locks"][k]
    def rng(x):
        lo, hi = num(x.get("lo"), 0, 470_000_000, None), num(x.get("hi"), 0, 470_000_000, None)
        return (lo, hi) if lo is not None and hi is not None and lo < hi else None
    if isinstance(j.get("bands"), list) and len(j["bands"]) <= 40:
        out = []
        for x in j["bands"]:
            r = rng(x) if isinstance(x, dict) else None
            if r and text(x.get("name"), 12): out.append({"name": text(x["name"], 12), "lo": r[0], "hi": r[1], "tx": x.get("tx") is True, "note": text(x.get("note"), 80)})
        if out: c["bands"] = out
    if isinstance(j.get("blocks"), list) and len(j["blocks"]) <= 100:
        c["blocks"] = [{"lo": rng(x)[0], "hi": rng(x)[1], "note": text(x.get("note"), 80)} for x in j["blocks"] if isinstance(x, dict) and rng(x)]
    if isinstance(j.get("modes"), dict) and len(j["modes"]) <= 20:
        c["modes"] = {k: text(v, 80) for k, v in j["modes"].items() if isinstance(k, str) and k.isalnum() and len(k) <= 8}
    A = j.get("aprs")
    if isinstance(A, dict):
        if A.get("digi") in ("off", "fillin", "wide"): c["aprs"]["digi"] = A["digi"]
        if A.get("igate") in ("off", "rx", "rxtx"): c["aprs"]["igate"] = A["igate"]
        if "aliases" in A: c["aprs"]["aliases"] = ",".join(x for x in str(A["aliases"] or "").upper().replace(" ", ",").split(",") if x and len(x) <= 9)[:60]
        sv = text(A.get("server"), 80)
        if sv and all(ch.isalnum() or ch in ".-" for ch in sv): c["aprs"]["server"] = sv
    if isinstance(j.get("hosts"), list): c["hosts"] = [text(h, 100).lower() for h in j["hosts"] if text(h, 100)][:20]
    return c

cfg = json.loads(json.dumps(DEFAULT)); trips = {}          # trips: band -> {"why", "hz", "at"}; kept over a restart
problem = ""                                               # settings.json exists but can't be read: fail safe, no transmitting

APRS_SECRET = os.path.join(HERE, "aprs-secret.json")            # the APRS-IS passcode: only Dire Wolf's configuration uses it
def aprs_pass():
    try:
        with open(APRS_SECRET) as f: v = json.load(f).get("pass", "")
        return v if isinstance(v, str) and v.isdigit() else ""
    except (OSError, ValueError, AttributeError): return ""
def set_aprs_pass(v):
    v = str(v or "").strip()
    if v and not (v.isdigit() and len(v) <= 6): raise ValueError("the passcode is a number (up to 5 digits)")
    write(APRS_SECRET, {"pass": v})

def write(path, obj):
    tmp = path + ".tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: json.dump(obj, f, indent=1); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def load():
    global problem
    try:
        with open(SETTINGS) as f: j = json.load(f)
        if not isinstance(j, dict): raise ValueError("not a settings file")
    except FileNotFoundError: return
    except (OSError, ValueError) as e: problem = f"settings.json can't be read ({e})"; return
    with lock:
        cfg.update(clean(j)); trips.clear()
        for k, v in (j.get("trips") or {}).items():
            if isinstance(v, dict): trips[text(k, 12)] = {"why": text(v.get("why"), 80), "hz": num(v.get("hz"), 0, 470_000_000, 0), "at": num(v.get("at"), 0, 1 << 40, 0)}

def save():
    global problem
    with lock: write(SETTINGS, {**cfg, "trips": trips}); problem = ""

def update(j):
    with lock: cfg.update(clean(j, cfg)); save()

# ---------------------------------------------------------------- where the radio may transmit
def band_of(hz): return next((b for b in cfg["bands"] if b["lo"] <= hz <= b["hi"]), None)
def band_key(hz): b = band_of(hz); return b["name"] if b else "other"
def mhz(hz): return f"{hz / 1e6:.6f}".rstrip("0").rstrip(".") + " MHz"

def reason(hz):
    """Why transmitting on this frequency is not allowed; "" = it is."""
    with lock:
        if problem: return problem + ": save the administration page again"
        if not cfg["tx"]: return "transmitting is turned off on the administration page"
        if not hz: return "the frequency is not known" if (cfg["inside"] or cfg["blocks"] or trips or any(not b["tx"] for b in cfg["bands"])) else ""
        for b in cfg["blocks"]:
            if b["lo"] <= hz <= b["hi"]: return f"{mhz(hz)} is locked" + (f": {b['note']}" if b["note"] else "")
        b = band_of(hz)
        if b is None and cfg["inside"]: return f"{mhz(hz)} is outside the bands allowed for transmitting"
        if b and not b["tx"]: return f"{b['name']} is locked for transmitting" + (f": {b['note']}" if b["note"] else "")
        t = trips.get(band_key(hz))
        if t: return f"high SWR on {band_key(hz)} ({t['why']}): check the antenna, then press Reset"
    return ""

MODE_NAME = {"PKTUSB": "DIG", "PKTLSB": "DIG (LSB)", "DLSB": "LSB voice", "DCW": "CW keyer", "FMN": "FM narrow"}
def mode_reason(mode):
    """Why transmitting in this mode is not allowed; "" = it is (or the mode is not known)."""
    with lock:
        if not mode or mode not in cfg["modes"]: return ""
        n = cfg["modes"][mode]; return f"{MODE_NAME.get(mode, mode)} is locked for transmitting" + (f": {n}" if n else "")

def locked_bands():
    with lock: return {b["name"]: b["note"] or "locked" for b in cfg["bands"] if not b["tx"] or not cfg["tx"]}

def trip(hz, why):
    with lock:
        trips[band_key(hz)] = {"why": why, "hz": hz, "at": int(time.time())}
        try: save()
        except OSError: pass

def untrip(band):
    with lock:
        if trips.pop(band, None) is None: return False
        try: save()
        except OSError: pass
        return True

# ---------------------------------------------------------------- passwords and sign-ins
_pw = {"mtime": -1, "d": {}}; sessions = {}; fails = {}; LIFE = 12 * 3600
def passwords():
    with lock:
        try: m = os.stat(PASSWORDS).st_mtime_ns
        except OSError: m = 0
        if m != _pw["mtime"]:
            if _pw["mtime"] != -1: sessions.clear()             # changed or deleted: everyone signs in again
            d = {}
            if m:
                try:
                    with open(PASSWORDS) as f: d = json.load(f)
                    if not isinstance(d, dict): raise ValueError
                except (OSError, ValueError): d = {"admin": {"salt": "00", "hash": "unreadable"}}     # nobody gets in by a broken file
            _pw.update(mtime=m, d=d)
        return _pw["d"]
def has(which): return bool(passwords().get(which))
def _hash(pw, salt): return hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
def check(which, pw):
    e = passwords().get(which)
    return bool(e) and hmac.compare_digest(_hash(pw, e["salt"]), e["hash"])
def set_password(which, pw):
    """pw "" removes the operator password. Everyone else is signed out."""
    with lock:
        d = dict(passwords())
        if pw: s = secrets.token_hex(16); d[which] = {"salt": s, "hash": _hash(pw, s)}
        else: d.pop(which, None)
        write(PASSWORDS, d); sessions.clear(); _pw["mtime"] = -1; passwords()

def login(ip, pw):
    """-> (role or "", token or message)"""
    now = time.time()
    with lock:
        f = [t for t in fails.get(ip, []) if now - t < 300]
        if len(f) >= 5: return "", f"too many wrong passwords: wait {int(300 - (now - f[0])) // 60 + 1} min"
        fails[ip] = f + [now]                                   # counted first, so parallel guesses can't get past it
    role = next((w for w in ("admin", "oper") if check(w, pw)), "")
    if not role: return "", "wrong password"
    with lock:
        fails.pop(ip, None)
        for t in [t for t, s in sessions.items() if s["until"] < now]: sessions.pop(t, None)
        tok = secrets.token_urlsafe(32); sessions[tok] = {"role": role, "ip": ip, "until": now + LIFE}
    return role, tok

def role(tok, ip):
    passwords()
    with lock:
        s = sessions.get(tok or "")
        if not s or s["ip"] != ip or s["until"] < time.time(): return ""
        s["until"] = time.time() + LIFE
        return s["role"]
def logout(tok):
    with lock: sessions.pop(tok or "", None)

# ---------------------------------------------------------------- is this request from our own page?
def host_ok(host, hostname):
    """Only an address or a local name may be used to open the panel, so that a website can't point its own name at the Pi."""
    h = (host or "").strip().lower()
    if h.startswith("["): h = h[1:].split("]")[0]
    elif h.count(":") == 1: h = h.split(":")[0]
    try: ipaddress.ip_address(h); return True
    except ValueError: pass
    h = h.rstrip(".")
    return bool(h) and ("." not in h or h == hostname.lower() or h.endswith((".local", ".home.arpa")) or h in cfg["hosts"])
