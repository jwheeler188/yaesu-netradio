"""W5PL FT-897D network radio - APRS packets (used by ft897-netbridge.py with Dire Wolf).

Dire Wolf is the modem, digipeater and iGate. The bridge talks to it over KISS TCP: frames in, frames out. This module
turns AX.25 frames into readable packets and back, and reads the APRS formats that matter in practice: positions
(plain, compressed and Mic-E), messages with their acks, status, objects and items. Unknown formats are kept as text.
"""
import math, re, time

# ---------------------------------------------------------------- KISS
FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


def kiss_wrap(frame, port=0):
    out = bytearray([FEND, (port & 0x0F) << 4])
    for b in frame:
        if b == FEND: out += bytes([FESC, TFEND])
        elif b == FESC: out += bytes([FESC, TFESC])
        else: out.append(b)
    out.append(FEND)
    return bytes(out)


class KissReader:
    """Feed bytes from the KISS socket; whole data frames come out of feed()."""
    def __init__(self): self.buf = bytearray(); self.inframe = False
    def feed(self, data):
        out = []
        for b in data:
            if b == FEND:
                if self.buf and (self.buf[0] & 0x0F) == 0: out.append(self.unescape(bytes(self.buf[1:])))
                self.buf = bytearray(); continue
            self.buf.append(b)
            if len(self.buf) > 4096: self.buf = bytearray()
        return out
    @staticmethod
    def unescape(b):
        return b.replace(bytes([FESC, TFEND]), bytes([FEND])).replace(bytes([FESC, TFESC]), bytes([FESC]))


# ---------------------------------------------------------------- AX.25 UI frames
def _addr_dec(b):
    call = "".join(chr(c >> 1) for c in b[:6]).strip(); ssid = (b[6] >> 1) & 0x0F
    return call + (f"-{ssid}" if ssid else ""), bool(b[6] & 0x80), bool(b[6] & 0x01)


def _addr_enc(call, last=False, h=False):
    c, _, s = call.upper().partition("-")
    if not re.fullmatch(r"[A-Z0-9]{1,6}", c) or (s and not (s.isdigit() and int(s) <= 15)): raise ValueError(f"bad address {call!r}")
    return bytes(ord(x) << 1 for x in c.ljust(6)) + bytes([0x60 | (int(s or 0) << 1) | (0x80 if h else 0) | (1 if last else 0)])


def ax25_decode(f):
    """-> dict(src, dst, path [str, '*' after used digis], info str) or None"""
    if len(f) < 16: return None
    addrs = []; i = 0
    while i + 7 <= len(f):
        a, hbit, last = _addr_dec(f[i:i + 7]); addrs.append((a, hbit)); i += 7
        if last: break
    if len(addrs) < 2 or i + 2 > len(f) or f[i] != 0x03 or f[i + 1] != 0xF0: return None
    path = [a + ("*" if h else "") for a, h in addrs[2:]]
    return {"dst": addrs[0][0], "src": addrs[1][0], "path": path, "info": f[i + 2:].decode("latin-1").rstrip("\r\n")}


def ax25_encode(src, dst, path, info):
    addrs = [dst, src] + list(path)
    out = b"".join(_addr_enc(a, i == len(addrs) - 1) for i, a in enumerate(addrs))
    return out + bytes([0x03, 0xF0]) + info.encode("latin-1", "replace")


def tnc2(p):
    return f"{p['src']}>{p['dst']}" + "".join("," + x for x in p["path"]) + ":" + p["info"]


def parse_tnc2(line):
    """'SRC>DST,PATH:info' (APRS-IS) -> the same dict as ax25_decode"""
    head, sep, info = line.partition(":")
    if not sep or ">" not in head: return None
    src, _, rest = head.partition(">"); parts = rest.split(",")
    return {"src": src, "dst": parts[0], "path": parts[1:], "info": info}


# ---------------------------------------------------------------- APRS
def _uncomp(s):
    """'4903.50N/07201.75W-' -> lat, lon, table, symbol"""
    m = re.match(r"(\d{2})([\d ]{2}\.[\d ]{2})([NS])(.)(\d{3})([\d ]{2}\.[\d ]{2})([EW])(.)", s)
    if not m: return None
    lat = int(m[1]) + float(m[2].replace(" ", "0")) / 60; lon = int(m[5]) + float(m[6].replace(" ", "0")) / 60
    if m[3] == "S": lat = -lat
    if m[7] == "W": lon = -lon
    if not (-90 <= lat <= 90 and -180 <= lon <= 180): return None
    return lat, lon, m[4], m[8], s[19:]


def _b91(s): return sum((ord(c) - 33) * 91 ** (len(s) - 1 - i) for i, c in enumerate(s))


def _comp(s):
    """compressed: /YYYYXXXX$csT"""
    if len(s) < 13 or not all(33 <= ord(c) <= 124 for c in s[1:9]): return None
    lat = 90 - _b91(s[1:5]) / 380926; lon = -180 + _b91(s[5:9]) / 190463
    out = {"lat": lat, "lon": lon, "table": s[0], "sym": s[9]}
    c, sp, t = s[10], s[11], s[12]
    if c != " " and 0 <= ord(c) - 33 <= 89 and not (((ord(t) - 33) >> 3) & 3 == 2):
        out["course"] = (ord(c) - 33) * 4; out["speed"] = round((1.08 ** (ord(sp) - 33) - 1) * 1.852, 1)
    return out, s[13:]


def _mice(dst, info):
    """Mic-E: latitude etc. in the destination, longitude, speed and course in the info."""
    d = dst.split("-")[0]
    if len(d) != 6 or len(info) < 9: return None
    digs = []
    for ch in d:
        if ch.isdigit(): digs.append(ch)
        elif "A" <= ch <= "J": digs.append(str(ord(ch) - 65))
        elif "P" <= ch <= "Y": digs.append(str(ord(ch) - 80))
        elif ch in "KLZ": digs.append("0")
        else: return None
    lat = int("".join(digs[:2])) + int("".join(digs[2:4])) / 60 + int("".join(digs[4:6])) / 6000
    if not ("P" <= d[3] <= "Z"): lat = -lat
    lon_off = 100 if "P" <= d[4] <= "Z" else 0
    west = "P" <= d[5] <= "Z"
    b = [ord(c) - 28 for c in info[1:7]]
    deg = b[0] + lon_off
    if 180 <= deg <= 189: deg -= 80
    elif 190 <= deg <= 199: deg -= 190
    mn = b[1] - 60 if b[1] >= 60 else b[1]
    lon = deg + (mn + b[2] / 100) / 60
    if west: lon = -lon
    sp = b[3] * 10 + b[4] // 10; co = (b[4] % 10) * 100 + b[5]
    if sp >= 800: sp -= 800
    if co >= 400: co -= 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180): return None
    return {"lat": lat, "lon": lon, "sym": info[7], "table": info[8], "speed": round(sp * 1.852, 1), "course": co}, info[9:]


WX = {"g": ("gust_mph", 3, 1), "t": ("temp_f", 3, 1), "r": ("rain_1h_in", 3, .01), "p": ("rain_24h_in", 3, .01), "P": ("rain_mid_in", 3, .01),
      "h": ("humidity", 2, 1), "b": ("pressure_mb", 5, .1), "L": ("lum_wm2", 3, 1), "l": ("lum_wm2", 3, 1), "s": ("snow_in", 3, .1),
      "c": ("wind_dir", 3, 1), "#": ("rain_raw", 3, 1)}


def parse_wx(text, wind=True):
    """APRS weather fields ('220/004g005t077r000p000P000h50b10132'; 'c220s004g005t077...' without a position) -> (dict, the rest).
    Missing values ('...' or spaces) are left out."""
    w = {}; i = 0
    if wind:
        m = re.match(r"([\d. ]{3})/([\d. ]{3})", text)
        if m:
            if m[1].strip(" .").isdigit(): w["wind_dir"] = int(m[1])
            if m[2].strip(" .").isdigit(): w["wind_mph"] = int(m[2])
            i = 7
    while i < len(text):
        k = text[i]
        if k == "s" and "wind_mph" not in w and "c" in text[:i + 1]:  # positionless: s = wind speed (after c), not snow
            m = re.match(r"s([\d. ]{3})", text[i:])
            if m:
                if m[1].strip(" .").isdigit(): w["wind_mph"] = int(m[1])
                i += 4; continue
        if k not in WX: break
        name, n, scale = WX[k]
        if k == "t": n = 3
        m = re.match(r"(-?[\d]{%d}|-?\d{%d}|[. ]{%d})" % (n, n - 1, n), text[i + 1:]) if k in "tb" else re.match(r"([\d. ]{%d})" % n, text[i + 1:])
        if not m: break
        v = m[1]
        if v.strip(" .").lstrip("-").isdigit():
            x = int(v) * scale
            if k == "h": x = 100 if x == 0 else x
            w[name] = round(x, 2)
        i += 1 + len(v)
    w.pop("rain_raw", None)
    return w, text[i:]


TABLES = "/\\0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"         # symbol table: primary, alternate, or an overlay on the alternate


def parse(p):
    """Adds kind and fields to a decoded packet (dict from ax25_decode / parse_tnc2). Never raises.
    A symbol with a table character that isn't allowed (some radios send them) is kept as sym_bad, not as a symbol."""
    try: q = _parse(dict(p))
    except Exception: q = dict(p); q["kind"] = "other"; return q
    c = q.get("comment") or ""
    m = re.search(r"/A=(-?\d{6})", c)                              # altitude in feet, inside the comment
    if m: q["alt_ft"] = int(m[1]); q["comment"] = (c[:m.start()] + c[m.end():]).strip()
    if "sym" in q and (q.get("table") or "/") not in TABLES and not (q.get("table") or "").islower():
        q["sym_bad"] = (q.get("table") or "") + q["sym"]; q.pop("sym"); q.pop("table", None)
    return q


def _parse(p):
    info = p["info"]; p["kind"] = "other"
    if not info: return p
    t = info[0]
    if t == "}":                                                   # third-party (gated): parse the inner packet
        inner = parse_tnc2(info[1:])
        if inner: q = _parse(inner); q["via3"] = p["src"]; return q
        return p
    if t == ":" and len(info) >= 11 and info[10] == ":":           # message / ack / bulletin
        to = info[1:10].strip(); body = info[11:]
        p.update(kind="message", to=to); m = re.match(r"(ack|rej)([A-Za-z0-9}]{1,5})\s*$", body)
        if m: p.update(kind=m[1], msgno=m[2]); return p
        m = re.match(r"(.*?)\{([A-Za-z0-9}]{1,5})\s*$", body, re.S)
        p["text"], p["msgno"] = (m[1], m[2]) if m else (body, None)
        if to.upper().startswith(("BLN", "NWS")): p["kind"] = "bulletin"
        return p
    if t == ">": p.update(kind="status", text=info[1:]); return p
    if t in "`'":                                                  # Mic-E
        r = _mice(p["dst"], info)
        if r: p.update(r[0]); p.update(kind="position", comment=re.sub(r"^[>\]`']", "", r[1]).strip()); return p
        return p
    body = None
    if t in "!=": body = info[1:]
    elif t in "/@": body = info[8:]                                 # with a timestamp
    elif t == ";" and len(info) > 18:                               # object
        p.update(name=info[1:10].strip(), alive=info[10] == "*"); body = info[18:]; p["kind"] = "object"
    elif t == ")":                                                  # item
        m = re.match(r"\)([^!_]{3,9})([!_])", info)
        if m: p.update(name=m[1].strip(), alive=m[2] == "!"); body = info[m.end():]; p["kind"] = "item"
    elif t == "_":                                                  # weather without a position: _MMDDHHMM then c s g t r ...
        w, rest = parse_wx(info[9:], wind=False)
        p.update(kind="weather", wx=w, comment=rest.strip()); return p
    if body is None: return p
    if body[:1] in "/\\" or body[:1].isalpha():
        r = _comp(body)
        if r:
            p.update(r[0]); p["comment"] = r[1].strip(); p["kind"] = p["kind"] if p["kind"] in ("object", "item") else "position"
            if p.get("sym") == "_":
                w, rest = parse_wx(p["comment"], wind=False)
                if "course" in p: w["wind_dir"] = p.pop("course"); w["wind_mph"] = round(p.pop("speed") / 1.609, 0)
                p.update(wx=w, comment=rest.strip()); p["kind"] = "weather" if p["kind"] == "position" else p["kind"]
            return p
    r = _uncomp(body)
    if r:
        p.update(lat=r[0], lon=r[1], table=r[2], sym=r[3]); c = r[4]
        if p["sym"] == "_":                                         # a weather station: wind, temperature, rain... not course and speed
            w, c = parse_wx(c)
            if w: p["wx"] = w
            if p["kind"] == "other": p["kind"] = "weather"
        else:
            m = re.match(r"(\d{3})/(\d{3})", c)
            if m and (m[1] != "000" or m[2] != "000"): p.update(course=int(m[1]), speed=round(int(m[2]) * 1.852, 1)); c = c[7:]
        p["comment"] = c.strip()
        if p["kind"] == "other": p["kind"] = "position"
    return p


def position_info(lat, lon, table, sym, comment="", messaging=True):
    """An uncompressed position report, no timestamp."""
    def dm(v, w, pos, neg): d = int(abs(v)); m = (abs(v) - d) * 60; return f"{d:0{w}d}{m:05.2f}{pos if v >= 0 else neg}"
    return ("=" if messaging else "!") + dm(lat, 2, "N", "S") + table + dm(lon, 3, "E", "W") + sym + comment[:43]


def message_info(to, text, msgno=None):
    text = re.sub(r"[|~{\r\n]", " ", text)[:67]
    return f":{to.upper():<9}:{text}" + (f"{{{msgno}" if msgno else "")


def ack_info(to, msgno): return f":{to.upper():<9}:ack{msgno}"


# ---------------------------------------------------------------- places
def grid_to_latlon(g):
    g = g.strip()
    if not re.fullmatch(r"[A-Ra-r]{2}\d{2}([A-Xa-x]{2})?", g): raise ValueError("not a grid square")
    lon = (ord(g[0].upper()) - 65) * 20 - 180 + int(g[2]) * 2; lat = (ord(g[1].upper()) - 65) * 10 - 90 + int(g[3])
    if len(g) == 6: lon += (ord(g[4].lower()) - 97) / 12 + 1 / 24; lat += (ord(g[5].lower()) - 97) / 24 + 1 / 48
    else: lon += 1; lat += 0.5
    return round(lat, 5), round(lon, 5)


def latlon_to_grid(lat, lon):
    lon += 180; lat += 90
    return (chr(65 + int(lon // 20)) + chr(65 + int(lat // 10)) + str(int(lon % 20 // 2)) + str(int(lat % 10))
            + chr(97 + int(lon % 2 * 12)) + chr(97 + int(lat % 1 * 24)))


def dist_bearing(lat1, lon1, lat2, lon2):
    """-> km, degrees"""
    r = math.radians; dl = r(lon2 - lon1)
    a = math.sin(r(lat2 - lat1) / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(dl / 2) ** 2
    d = 6371 * 2 * math.asin(min(1, math.sqrt(a)))
    b = math.degrees(math.atan2(math.sin(dl) * math.cos(r(lat2)), math.cos(r(lat1)) * math.sin(r(lat2)) - math.sin(r(lat1)) * math.cos(r(lat2)) * math.cos(dl)))
    return round(d, 1), round((b + 360) % 360)

