"""Stand-ins for xara and Pat in the Winlink test (FT897_WL_FAKE): xara keys the radio over CAT for 1 s every 4 s, Pat serves a page."""
import os, socket, sys, time, http.server
if sys.argv[1] == "pat":
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(s): s.send_response(200); s.end_headers(); s.wfile.write(b"fake Pat")
        def log_message(s, *a): pass
    print("fake pat listening", flush=True); http.server.HTTPServer(("127.0.0.1", int(sys.argv[2])), H).serve_forever()
elif sys.argv[1] == "direwolf":                     # listens on every network, as Dire Wolf 1.7 does: localonly.so must narrow it
    l = socket.socket(socket.AF_INET6); l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); l.bind(("::", int(sys.argv[2]))); l.listen(1)
    print(f"fake direwolf, AGW on {l.getsockname()[0]} port {sys.argv[2]}", flush=True)
    while True: time.sleep(1)
else:
    port = int(os.environ.get("FT897_WL_CAT", "4533")); print(f"fake xara, CAT {port}", flush=True)
    s = socket.create_connection(("127.0.0.1", port), 5); s.settimeout(3)
    def c(l):
        t0 = time.time(); s.sendall(l.encode() + b"\n"); r = s.recv(999).decode().strip(); print(f"{l} -> {r} in {time.time() - t0:.2f} s", flush=True); return r
    while True:
        time.sleep(3); c("T 1"); time.sleep(1); c("T 0")
