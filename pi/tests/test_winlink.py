src=open("acctest.py").read().split("# ---- 1 other websites")[0]; exec(src)
# Winlink on the Pi: start/stop, the local audio link, keying over CAT, take-over. Needs the loopback card (snd-aloop) and
# the bridge started with FT897_WL_FAKE=wl_fake.py FT897_WL_CAT=14633 --xara <any executable file> --pat python3 --pat-port 18092.
A="127.0.0.2"; B="127.0.0.3"
def wl(**j): return req(A,"/api/winlink",j)
def W_(ip=A): return st(ip)["winlink"]
def waitfor(fn, sec):
    t0=time.time()
    while time.time()-t0<sec:
        if fn(): return time.time()-t0
        time.sleep(.1)
    return None
keep([A])
x=W_(); t("state shown, off",x["on"] is False and x["missing"]=="",x)
t("start without a call sign refused",wl(op="start")[0]==409)
t("bad call sign refused",wl(op="set",call="not a call")[0]==409 and W_()["call"]=="")
r=wl(op="set",call="n0call",locator="em20dd",bw=2300,drive=-8); x=W_(); t("settings kept",r[0]==200 and x["call"]=="N0CALL" and x["locator"]=="EM20dd" and x["drive"]==-8 and json.load(open("winlink.json"))["call"]=="N0CALL",(r,x))
v=Audio(B); time.sleep(1.2)
t("a voice client connected: start refused",wl(op="start")[0]==409 and not W_()["on"])
r=wl(op="start",stop_voice=True); time.sleep(4.5); x=W_(); s=st(A)
t("start with stop_voice: running",r[0]==200 and x["on"] and all(x["alive"].values()) and len(x["alive"])==3,(r,x))
t("the audio link is Winlink's",s["audio"] and s["audio"]["ip"]=="127.0.0.1" and "Winlink" in s["audio"]["name"],s["audio"])
t("Pat's settings written, no password in them",(lambda c:c["mycall"]=="N0CALL" and c["varahf"]["addr"]=="127.0.0.1:8300" and not c.get("secure_login_password"))(json.load(open("pat-config.json"))))
t("Pat's page answers",http.client.HTTPConnection("127.0.0.1",18092,timeout=3).request("GET","/") or True)
k=waitfor(lambda: st(A)["keyed"],6); s=st(A); t("xara keys the radio over CAT",k is not None and (s["keyed_by"] or "").startswith("127.0.0.1"),s["keyed_by"])
u=waitfor(lambda: not st(A)["keyed"],3); t("and lets go",u is not None)
time.sleep(5); log=open("log_"+os.path.basename(sys.argv[0])[:-3]+".txt").read(); t("no safety un-keys (the audio link keeps sending)","SAFETY" not in log,[l for l in log.splitlines() if "SAFETY" in l][-2:])
lat=[float(m) for m in re.findall(r"T 1 -> RPRT 0 in ([0-9.]+) s","\n".join(W_()["log"]))]; t("PTT answers well under xara's 1 s limit",lat and max(lat)<0.5,lat)
t("settings locked while running",wl(op="set",call="n1call")[0]==409)
# another computer takes control: Winlink stops
req(B,"/api/take",{}); time.sleep(.5); req(A,"/api/answer",{"yes":True}); time.sleep(5); x=W_()
t("someone else takes control: Winlink stops",not x["on"] and st(A)["audio"] is None,(x["on"],st(A)["audio"]))
req(B,"/api/release",{}); time.sleep(.5)
r=wl(op="start"); time.sleep(3); t("starts again",r[0]==200 and W_()["on"])
r=wl(op="stop"); time.sleep(5); x=W_(); t("stop: all gone",not x["on"] and st(A)["audio"] is None and not st(A)["keyed"],x)
t("no stray processes",subprocess.run(["pgrep","-f","-u .*(wl_fake.py (xara|pat)|ft897-winlink-audio)"],capture_output=True).stdout.strip()==b"")
log=open("log_"+os.path.basename(sys.argv[0])[:-3]+".txt").read(); t("no tracebacks",log.count("Traceback")==0)
keep.on=False; v.run=False; print(f"{ok} passed, {bad} failed")
