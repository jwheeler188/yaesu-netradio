#!/usr/bin/env python3
"""A pretend FT-891 on a pseudo-terminal, for tests without a radio. Answers the commands in Yaesu's FT-891 CAT reference
book and "?;" to everything else (as the radio does), so Hamlib and the bridge can be run against it.
  sim_ft891.py LINK LOG FAULTS   LINK = serial device to offer, LOG = every command received, FAULTS = JSON file, read live:
  {"dead": true}    answers nothing        {"stuck": true}  stays in transmit after TX0;
  {"swr": 0-255}    SWR meter              {"hi_swr": true} high-SWR flag       {"noset": ["MD"]} ignores these set commands"""
import json, os, pty, select, sys, time, tty
link, logf, faults = sys.argv[1:4]
m, s = pty.openpty(); tty.setraw(m); tty.setraw(s)
try: os.unlink(link)
except OSError: pass
os.symlink(os.ttyname(s), link)
R = {"FA": 14250000, "FB": 14260000, "MD": "2", "TX": 0, "PC": 100, "OS": "0", "CT": "0", "CN0": 12, "CN1": 0, "ST": "0", "clar": 0, "cf": "0", "PS": "1",
     "LK": "0", "SM": 120, "EX0904": 100, "EX0905": 1000, "EX0812": 0, "AG": 100, "RG": 255, "SQ": 0, "NA": "0", "SH": "00", "AI": "0", "NB": "0", "NR": "0", "BC": "0", "GT": "4",
     "PA": "1", "RA": "0", "MX": "0", "VX": "0", "AC": "000", "KS": 20, "MG": 50, "PR0": "1", "VM": "0"}
R["keyed_at"] = 0.0
def F():
    try:
        with open(faults) as f: return json.load(f)
    except (OSError, ValueError): return {}
def info(f): return f"{'001'}{f:09d}{'+' if R['clar'] >= 0 else '-'}{abs(R['clar']):04d}{R['cf']}0{R['MD']}0{R['CT'] if R['CT'] != '3' else '0'}00{R['OS']};"
def do(c, fl):
    k, v = c[:2], c[2:]; st = k in (fl.get("noset") or []) and v != ""
    if k == "ID" and not v: return "ID0650;"
    if k in ("FA", "FB"):
        if not v: return f"{k}{R[k]:09d};"
        if len(v) != 9 or not v.isdigit(): return "?;"
        if not 30_000 <= int(v) <= 56_000_000: return "?;"
        if not st: R[k] = int(v)
        return ""
    if k == "MD" and v[:1] == "0":
        if len(v) == 1: return "MD0" + R["MD"] + ";"
        if v[1] not in "123456789BCD": return "?;"
        if not st: R["MD"] = v[1]
        return ""
    if k == "TX":
        if not v: return f"TX{1 if fl.get('stuck') and not R['TX'] else R['TX']};"
        if v not in "01": return "?;"
        R["TX"] = int(v); return ""
    if k == "RM" and v in "1234567" and v:
        tx = R["TX"] or fl.get("stuck")
        n = {"1": 0 if tx else R["SM"], "3": 0, "4": 60 if tx else 0, "5": int(R["PC"] * 2) if tx else 0, "6": int(fl.get("swr", 5)) if tx else 0, "7": 0, "2": 0}[v]
        return f"RM{v}{min(n, 255):03d};"
    if k == "SM" and v == "0": return f"SM0{R['SM']:03d};"
    if k == "RI": return {"0": f"RI0{1 if fl.get('hi_swr') and R['TX'] else 0};", "A": f"RIA{1 if R['TX'] or fl.get('stuck') else 0};", "B": "RIB1;"}.get(v, "?;")
    if k == "BY" and not v: return "BY10;"
    if k == "IF" and not v: return "IF" + info(R["FA"])
    if k == "OI" and not v: return "OI" + info(R["FB"])
    if k == "PC":
        if not v: return f"PC{R['PC']:03d};"
        if not (v.isdigit() and len(v) == 3 and 5 <= int(v) <= 100): return "?;"
        R["PC"] = int(v); return ""
    if k == "OS":
        if v == "0": return "OS0" + R["OS"] + ";"
        if R["MD"] not in "4B" or v not in ("00", "01", "02"): return "?;"
        R["OS"] = v[1]; return ""
    if k == "CT":
        if v == "0": return "CT0" + R["CT"] + ";"
        if R["MD"] not in "4B" or v not in ("00", "01", "02", "03"): return "?;"
        R["CT"] = v[1]; return ""
    if k == "CN" and v[:2] in ("00", "01"):
        if len(v) == 2: return f"CN{v}{R['CN' + v[1]]:03d};"
        if len(v) != 5 or not v[2:].isdigit() or int(v[2:]) > (49 if v[1] == "0" else 103): return "?;"
        R["CN" + v[1]] = int(v[2:]); return ""
    if k == "ST":
        if not v: return "ST" + R["ST"] + ";"
        if v not in "012": return "?;"
        R["ST"] = v; return ""
    if k == "AB" and not v: R["FB"] = R["FA"]; R["MDB"] = R["MD"]; return ""
    if k == "BA" and not v: R["FA"] = R["FB"]; return ""
    if k == "SV" and not v: R["FA"], R["FB"] = R["FB"], R["FA"]; return ""
    if k == "RC" and not v: R["clar"] = 0; return ""
    if k in ("RU", "RD"):
        if len(v) != 4 or not v.isdigit(): return "?;"
        R["clar"] += int(v) if k == "RU" else -int(v); return ""
    if k == "CF":                                  # P1 0, P2 0/1, P3 0 (fixed)
        if v == "0": return f"CF0{R['cf']}0;"
        if len(v) != 3: return "?;"
        R["cf"] = v[1]; return ""
    if k == "EX":
        n = k + v[:4]
        if n not in R: return "?;"
        if len(v) == 4: return f"{n}{R[n]:0{1 if n == 'EX0812' else 4}d};"
        if not v[4:].isdigit(): return "?;"
        R[n] = int(v[4:]); return ""
    if k in ("LK", "PS", "AI", "MX", "VX", "AC", "KS", "MG"):
        if not v: return k + (f"{R[k]:03d}" if isinstance(R[k], int) else R[k]) + ";"
        R[k] = v if not isinstance(R[k], int) else int(v); return ""
    if k in ("AG", "RG", "SQ", "NA", "SH", "NB", "NR", "BC", "GT", "PA", "RA"):
        if v == "0": return k + "0" + (f"{R[k]:03d}" if isinstance(R[k], int) else R[k]) + ";"
        if not v: return "?;"
        R[k] = int(v[1:]) if isinstance(R[k], int) else v[1:]; return ""
    return "?;"
buf = b""; L = open(logf, "w", buffering=1)
while True:
    r, _, _ = select.select([m], [], [], 1)
    if not r: continue
    buf += os.read(m, 4096)
    while b";" in buf:
        c, buf = buf.split(b";", 1); c = c.decode(errors="replace").strip().upper(); fl = F()
        if not c: continue
        a = "" if fl.get("dead") else do(c, fl)
        L.write(f"{time.time():.2f} {c}; -> {a or '-'}\n")
        if a: time.sleep(0.004); os.write(m, a.encode())
