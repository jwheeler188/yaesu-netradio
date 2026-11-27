"""W5PL FT-897D network radio - offline maps for the APRS page (used by ft897-netbridge.py).

An area is a circle (centre, radius) downloaded up to a maximum zoom from a tile source, stored as one MBTiles file (SQLite,
the format EmComm Tools' map server reads too). The whole radius gets every zoom up to town level (12); street detail, the zooms
above that, only within detail_km (all of it if detail_km is 0 or larger than the radius): what a map around a station needs, and
much smaller. The bridge serves the tiles at /tiles/<source>/<z>/<x>/<y>, so the map works with no internet.
"""
import json, math, os, re, sqlite3, threading, time, urllib.request

SOURCES = {
    "usgs-topo": {"name": "USGS topo (US only)", "url": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
                  "max": 16, "est": 25_000, "credit": "USGS The National Map"},
    "usgs-imagery": {"name": "USGS imagery (US only)", "url": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
                     "max": 16, "est": 25_000, "credit": "USGS The National Map"},
}
MIN_Z = 3; TOWN_Z = 12


def tile_xy(lat, lon, z):
    n = 2 ** z; lat = max(-85.0511, min(85.0511, lat))
    x = int((lon + 180) / 360 * n); y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def plan(lat, lon, radius_km, maxz, detail_km=0):
    """-> [(z, x0, x1, y0, y1)] tile ranges covering the area, zoom by zoom"""
    out = []
    for z in range(MIN_Z, maxz + 1):
        r = radius_km if z <= TOWN_Z or not detail_km else min(radius_km, detail_km)
        dlat = r / 111.32; dlon = r / (111.32 * max(0.05, math.cos(math.radians(lat))))
        x0, y0 = tile_xy(lat + dlat, lon - dlon, z); x1, y1 = tile_xy(lat - dlat, lon + dlon, z)
        out.append((z, x0, x1, y0, y1))
    return out


def count(p): return sum((x1 - x0 + 1) * (y1 - y0 + 1) for _, x0, x1, y0, y1 in p)


class Store:
    """The downloaded areas in one folder, and the one download that may run at a time."""
    def __init__(self, folder, user_agent, url_override=""):
        self.dir = folder; self.ua = user_agent; self.override = url_override
        self.l = threading.Lock(); self.job = None; self.cancel = False; self.dbs = {}; self.idx = (0.0, [])
        os.makedirs(folder, exist_ok=True)

    # ---- areas
    def areas(self):
        out = []
        for f in sorted(os.listdir(self.dir)):
            if not f.endswith(".mbtiles"): continue
            try:
                with sqlite3.connect(f"file:{os.path.join(self.dir, f)}?mode=ro", uri=True) as db:
                    m = dict(db.execute("SELECT name, value FROM metadata").fetchall())
                    n = db.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
                a = json.loads(m.get("ynr", "{}")); a.update(id=f[:-8], name=m.get("name", f), tiles=n, size=os.path.getsize(os.path.join(self.dir, f)))
                out.append(a)
            except (sqlite3.Error, ValueError): continue
        return out

    def delete(self, aid):
        if not re.fullmatch(r"[a-z0-9-]{1,40}", aid or ""): return "no such area"
        with self.l:
            if self.job and self.job["id"] == aid and self.job["state"] == "running": return "it is downloading: cancel it first"
            p = os.path.join(self.dir, aid + ".mbtiles")
            if not os.path.exists(p): return "no such area"
            db = self.dbs.pop(aid, None)
            if db: db.close()
            os.remove(p); self.idx = (0.0, [])
        return ""

    # ---- tiles for the map
    def index(self):
        """(id, source, maxz) of each area, read again at most every 5 s (tiles are asked for many times a second)."""
        t, L = self.idx
        if time.time() - t > 5:
            L = []
            for f in sorted(os.listdir(self.dir)):
                if not f.endswith(".mbtiles"): continue
                try:
                    with sqlite3.connect(f"file:{os.path.join(self.dir, f)}?mode=ro", uri=True) as db:
                        r = db.execute("SELECT value FROM metadata WHERE name='ynr'").fetchone()
                    m = json.loads(r[0]) if r else {}
                    L.append({"id": f[:-8], "source": m.get("source"), "maxz": m.get("maxz", 0)})
                except (sqlite3.Error, ValueError): continue
            self.idx = (time.time(), L)
        return L

    def tile(self, source, z, x, y):
        """A tile from any downloaded area of this source, or None. (MBTiles rows count from the bottom: TMS.)"""
        for a in self.index():
            if a.get("source") != source or not (MIN_Z <= z <= a.get("maxz", 0)): continue
            db = self.dbs.get(a["id"])
            if db is None:
                db = self.dbs[a["id"]] = sqlite3.connect(f"file:{os.path.join(self.dir, a['id'] + '.mbtiles')}?mode=ro", uri=True, check_same_thread=False)
            try:
                r = db.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?", (z, x, 2 ** z - 1 - y)).fetchone()
            except sqlite3.Error: r = None
            if r: return r[0]
        return None

    # ---- downloading
    def start(self, name, source, lat, lon, radius_km, maxz, detail_km, limit_mb):
        if source not in SOURCES: return "unknown map source", None
        p = plan(lat, lon, radius_km, maxz, detail_km); n = count(p); est = n * SOURCES[source]["est"]
        free = shutil_free(self.dir)
        if est > limit_mb * 1_000_000: return f"about {est / 1e6:.0f} MB: more than the {limit_mb} MB limit (administration page). Use a smaller radius or zoom.", None
        if est > free - 1_000_000_000: return f"about {est / 1e6:.0f} MB, but only {free / 1e9:.1f} GB is free on the Pi", None
        with self.l:
            if self.job and self.job["state"] == "running": return "another map download is running", None
            aid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "area"
            aid = f"{aid}-{source}"; path = os.path.join(self.dir, aid + ".mbtiles")
            self.job = {"id": aid, "name": name, "source": source, "total": n, "done": 0, "have": 0, "bytes": 0, "failed": 0, "state": "running",
                        "started": time.time(), "est_bytes": est, "err": ""}
            self.cancel = False
        meta = {"source": source, "lat": lat, "lon": lon, "radius_km": radius_km, "maxz": maxz, "detail_km": detail_km, "created": int(time.time())}
        threading.Thread(target=self._run, args=(path, name, meta, p), daemon=True).start()
        return "", aid

    def _run(self, path, name, meta, p):
        j = self.job; src = SOURCES[meta["source"]]; url = self.override or src["url"]
        try:
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles (zoom_level, tile_column, tile_row)")
            lat = meta["lat"]; r = meta["radius_km"]; dl = r / 111.32; dn = r / (111.32 * max(0.05, math.cos(math.radians(lat))))
            md = {"name": name, "format": "jpg", "type": "baselayer", "minzoom": str(MIN_Z), "maxzoom": str(meta["maxz"]), "attribution": src["credit"],
                  "bounds": f"{meta['lon'] - dn:.5f},{lat - dl:.5f},{meta['lon'] + dn:.5f},{lat + dl:.5f}", "ynr": json.dumps(meta)}
            db.execute("DELETE FROM metadata"); db.executemany("INSERT INTO metadata VALUES (?, ?)", md.items()); db.commit()
            have = {(z, x, y) for z, x, y in db.execute("SELECT zoom_level, tile_column, tile_row FROM tiles")}
            k = 0
            for z, x0, x1, y0, y1 in p:
                for x in range(x0, x1 + 1):
                    for y in range(y0, y1 + 1):
                        if self.cancel: j["state"] = "cancelled"; db.commit(); db.close(); return
                        j["done"] += 1
                        if (z, x, 2 ** z - 1 - y) in have: j["have"] += 1; continue
                        t0 = time.time(); d = None
                        for attempt in range(3):
                            try:
                                req = urllib.request.Request(url.format(z=z, x=x, y=y), headers={"User-Agent": self.ua})
                                with urllib.request.urlopen(req, timeout=20) as resp: d = resp.read()
                                break
                            except Exception as e:
                                if getattr(e, "code", None) == 404: break           # no tile here (sea, outside the coverage)
                                time.sleep(2 * (attempt + 1))
                        if d: db.execute("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)", (z, x, 2 ** z - 1 - y, d)); j["bytes"] += len(d)
                        else: j["failed"] += 1
                        k += 1
                        if k % 100 == 0: db.commit()
                        time.sleep(max(0, 0.1 - (time.time() - t0)))           # at most about 10 tiles a second: polite to the server
            db.commit(); db.close(); j["state"] = "done"; self.idx = (0.0, [])
        except Exception as e:
            j["state"] = "error"; j["err"] = str(e)[:200]

    def status(self):
        j = self.job
        if not j: return None
        x = dict(j); el = time.time() - j["started"]; left = j["total"] - j["done"]
        x["eta"] = int(left * el / max(1, j["done"])) if j["state"] == "running" and j["done"] > 20 else None
        return x


def shutil_free(path):
    import shutil
    return shutil.disk_usage(path).free


def estimate(source, lat, lon, radius_km, maxz, detail_km=0):
    p = plan(lat, lon, radius_km, maxz, detail_km); n = count(p)
    return {"tiles": n, "mb": round(n * SOURCES[source]["est"] / 1e6, 1), "by_zoom": {z: (x1 - x0 + 1) * (y1 - y0 + 1) for z, x0, x1, y0, y1 in p}}
