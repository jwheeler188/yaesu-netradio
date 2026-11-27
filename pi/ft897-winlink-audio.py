#!/usr/bin/env python3
"""W5PL FT-897D network radio - the audio link for Winlink on the Pi itself (started by ft897-netbridge.py).

xara (the VARA-compatible modem) runs on the Pi and talks to a loopback sound card (snd-aloop, card "Loopback"). This program
connects to the bridge's audio port like the laptop's data client does, so every safety check of the bridge applies:
  bridge receive audio  -> Loopback DEV 0 SUB 0  ->  xara captures   Loopback DEV 1 SUB 0
  xara plays            -> Loopback DEV 0 SUB 1  ->  we capture      Loopback DEV 1 SUB 1  -> bridge transmit audio
xara keys the radio through the bridge's CAT port (rigctld protocol), as WSJT-X does.
"""
import argparse, json, socket, struct, subprocess, sys, threading, time

p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
p.add_argument("--port", type=int, default=4534, help="the bridge's audio port")
p.add_argument("--to-modem", default="plughw:CARD=Loopback,DEV=0,SUBDEV=0", help="where receive audio goes (xara captures the other end)")
p.add_argument("--from-modem", default="plughw:CARD=Loopback,DEV=1,SUBDEV=1", help="where xara's transmit audio comes from")
p.add_argument("--name", default="Winlink on the Pi (xara)")
a = p.parse_args()
RATE, FRAME = 48000, 48000 * 2 * 20 // 1000


def log(m): print(time.strftime("%H:%M:%S ") + m, flush=True)


def alsa(cmd, dev):
    return [cmd, "-q", "-D", dev, "-f", "S16_LE", "-c", "1", "-r", str(RATE), "-t", "raw", "--buffer-time=100000", "--period-time=20000"]


def frame(s, t, d=b""): s.sendall(t + struct.pack(">H", len(d)) + d)


def recv_exact(s, n):
    b = b""
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d: raise ConnectionError("closed")
        b += d
    return b


def run():
    s = socket.create_connection(("127.0.0.1", a.port), timeout=10)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    frame(s, b"H", json.dumps({"name": a.name, "tx_buffer_ms": 160}).encode())
    h = recv_exact(s, 3); d = recv_exact(s, h[1] << 8 | h[2])
    if h[:1] == b"B": log("the bridge said no: " + d.decode(errors="replace")); time.sleep(5); return
    log("connected to the bridge " + d.decode(errors="replace"))
    out = subprocess.Popen(alsa("aplay", a.to_modem), stdin=subprocess.PIPE)
    rec = subprocess.Popen(alsa("arecord", a.from_modem), stdout=subprocess.PIPE)
    wl = threading.Lock(); alive = [True]

    def up():                                   # xara's transmit audio -> bridge (the bridge passes it on only while keyed)
        n = 0
        try:
            while alive[0]:
                d = rec.stdout.read(FRAME)
                if not d: break
                with wl:
                    frame(s, b"A", d); n += 1
                    if n % 25 == 0: frame(s, b"S", json.dumps({"voice": False, "client": "winlink", "name": a.name}).encode())
        except OSError: pass
        alive[0] = False
    threading.Thread(target=up, daemon=True).start()
    s.settimeout(15)
    try:
        while alive[0]:
            h = recv_exact(s, 3); d = recv_exact(s, h[1] << 8 | h[2])
            if h[:1] == b"A": out.stdin.write(d); out.stdin.flush()
            elif h[:1] == b"B": log("the bridge closed the link: " + d.decode(errors="replace")); break
    except (OSError, ConnectionError) as e: log(f"link ended: {e}")
    alive[0] = False
    for pr in (out, rec):
        try: pr.terminate()
        except OSError: pass
    s.close()


if __name__ == "__main__":
    while True:
        try: run()
        except (OSError, ConnectionError) as e: log(f"no bridge yet: {e}")
        time.sleep(2)
