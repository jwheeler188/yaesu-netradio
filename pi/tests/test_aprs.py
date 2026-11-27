src=open("acctest.py").read().split("# ---- 1 other websites")[0]; exec(src)
# APRS on the Pi end to end, no radio: packets made into audio with Dire Wolf's gen_packets are "heard" (FT897_RX_FILE), what the
# radio would transmit (FT897_TX_RAW) is decoded with atest. Needs direwolf (gen_packets, atest) and the loopback card.
import array, wave
A="127.0.0.2"; B="127.0.0.3"; RAW=os.environ.get("FT897_TX_RAW","tx.raw"); RXF=os.environ.get("FT897_RX_FILE","rx.raw")
def ap(**j): return req(A,"/api/aprs",j)
def full(): return req(A,"/api/aprs")[1]
def waitfor(fn, sec):
    t0=time.time()
    while time.time()-t0<sec:
        if fn(): return time.time()-t0
        time.sleep(.2)
    return None
def hear(*lines):
    """Packets as audio, 3 s apart (a station can't hear while it transmits, e.g. a digipeat)."""
    out=bytes(48000*2)
    for l in lines:
        open("pk.txt","w").write(l)
        subprocess.run(["gen_packets","-r","48000","-o","pk.wav","pk.txt"],capture_output=True,check=True)
        with wave.open("pk.wav") as w: out+=w.readframes(w.getnframes())+bytes(48000*2*3)
    open(RXF+".t","wb").write(out); os.replace(RXF+".t",RXF)
def mark(): return os.path.getsize(RAW) if os.path.exists(RAW) else 0
def sent(p0):
    d=open(RAW,"rb").read()[p0:]
    with wave.open("tx.wav","wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000); w.writeframes(d)
    o=re.sub(r"\x1b\[[0-9;]*[A-Za-z]","",subprocess.run(["atest","-B","1200","tx.wav"],capture_output=True,text=True).stdout)
    return [l.split("] ",1)[-1].strip() for l in o.splitlines() if re.match(r"^\[\d",l)]
keep([A])
x=st(A)["aprs"]; t("summary in the state",x["on"] is False and x["missing"]=="",x)
t("start without a call sign refused",ap(op="start")[0]==409)
t("bad settings refused",ap(op="set",call="nonsense")[0]==409 and ap(op="set",grid="ZZ99")[0]==409 and ap(op="set",freq=7100000)[0]==409)
r=ap(op="set",call="w5pl-10",grid="DM79mp",beacon=0,path="WIDE1-1",show_is=False); f=full()
t("settings kept",r[0]==200 and f["settings"]["call"]=="W5PL-10" and f["settings"]["lat"]==39.64583 and json.load(open("aprs.json"))["call"]=="W5PL-10",(r,f["settings"]))
# admin: digipeater + iGate settings, passcode never sent back
req(A,"/api/admin",{"op":"setup","password":secrets.token_urlsafe(9)},who="adm")
r=req(A,"/api/admin",{"op":"save","cfg":{"aprs":{"digi":"fillin","igate":"rx","aliases":"emcomm"}},"aprs_pass":"12345"},who="adm"); g=req(A,"/api/admin",who="adm")[1]
t("admin: APRS settings saved",r[0]==200 and g["cfg"]["aprs"]["igate"]=="rx" and g["cfg"]["aprs"]["aliases"]=="EMCOMM",g.get("cfg",{}).get("aprs"))
t("admin: passcode stored apart, never shown",g["aprs_pass_set"] is True and "12345" not in json.dumps(g) and "12345" not in open("settings.json").read() and oct(os.stat("aprs-secret.json").st_mode)[-3:]=="600")
t("bad passcode refused",req(A,"/api/admin",{"op":"save","cfg":{},"aprs_pass":"abc"},who="adm")[0]==400)
req(A,"/api/admin",{"op":"save","cfg":{"aprs":{"igate":"off"}}},who="adm")
# Winlink and APRS exclude each other
v=Audio(B); time.sleep(1.2)
t("voice client connected: start refused",ap(op="start")[0]==409)
r=ap(op="start",stop_voice=True); w=waitfor(lambda: "talking to Dire Wolf" in open("log_"+os.path.basename(sys.argv[0])[:-3]+".txt").read(),15); x=st(A)
t("APRS starts: Dire Wolf + audio link, KISS connected",r[0]==200 and w is not None and x["aprs"]["on"] and all(x["aprs"]["alive"].values()),(r,x["aprs"],full()["log"]))
t("radio on 144.390 FM",waitfor(lambda: st(A)["rig"].get("freq")==144390000 and st(A)["rig"].get("mode")=="FM",15) is not None,st(A)["rig"])
t("Dire Wolf config: no network ports, fill-in digi with the alias",(lambda c:"KISSPORT 0" in c and "AGWPORT 0" in c and "DIGIPEAT 0 0 ^WIDE1-1$|^EMCOMM$ ^WIDE1-1$" in c and "12345" not in c)(open("aprs-run/direwolf.conf").read()))
t("Winlink refused while APRS runs",req(A,"/api/winlink",{"op":"set","call":"w5pl"})[0]==200 and req(A,"/api/winlink",{"op":"start"})[0]==409)
# receive: a position, a Mic-E mobile, a status, an object, and a message for us
p0=mark()
hear("N0CALL>APRS,WIDE2-1:!4903.50N/07201.75W-Home station","KE5ABC-9>S32U6T,WIDE1-1:`(_fn\"Oj/>Mobile","N0CALL>APRS,WIDE2-1:>Net at 8","N0CALL>APRS,WIDE2-1:;SHELTER  *092345z3940.00N/10530.00WhOpen",
     "N0CALL>APRS,WIDE1-1::W5PL-10  :Hello from the test{42")
w=waitfor(lambda: len(full()["stations"])>=3 and full()["msgs"],30); f=full(); S={s["call"]:s for s in f["stations"]}
t("stations heard on the radio",{"N0CALL","KE5ABC-9","SHELTER"}<=set(S) and all(S[k]["rf"] for k in ("N0CALL","KE5ABC-9")),(list(S),[p["line"] for p in f["packets"]],f["log"][-8:]))
t("plain position decoded",abs(S["N0CALL"]["lat"]-49.0583)<.001 and abs(S["N0CALL"]["lon"]+72.0292)<.001 and S["N0CALL"]["sym"]=="/-")
dw=[l for l in f["log"]]; 
t("object decoded",S["SHELTER"].get("obj") and abs(S["SHELTER"]["lat"]-39.6667)<.001)
t("distance and bearing from home",S["SHELTER"].get("km") is not None and 20<S["SHELTER"]["km"]<80,S["SHELTER"])
m=[m for m in f["msgs"] if m["dir"]=="in"]; t("message for us kept",m and m[0]["from"]=="N0CALL" and m[0]["text"]=="Hello from the test" and st(A)["aprs"]["unread"]==1,f["msgs"])
time.sleep(3); tx=sent(p0); t("and acknowledged on the air",any(":N0CALL   :ack42" in l for l in tx),tx)
t("fill-in digipeater: the WIDE1-1 packets were repeated",any(l.startswith("N0CALL>APRS,W5PL-10*") or ("W5PL-10*" in l and "Hello" in l) for l in tx) or any("WIDE1*" in l for l in tx),tx)
# the same message again (lost ack): acked again, not shown twice
p0=mark(); hear("N0CALL>APRS::W5PL-10  :Hello from the test{42"); time.sleep(6)
t("a repeat is acked again but kept once",len([m for m in full()["msgs"] if m["dir"]=="in"])==1 and any("ack42" in l for l in sent(p0)))
ap(op="read"); t("marked read",st(A)["aprs"]["unread"]==0)
# send a message; the other side acks it
p0=mark(); r=ap(op="send",to="n0call",text="Hi there, copy?"); time.sleep(4); tx=sent(p0); out=[m for m in full()["msgs"] if m["dir"]=="out"]
t("message sent on the air with a number",r[0]==200 and any(re.search(r":N0CALL   :Hi there, copy\?\{\d+",l) for l in tx) and out and out[-1]["state"]=="sent",(r,tx,out))
no=out[-1]["msgno"]; hear(f"N0CALL>APRS::W5PL-10  :ack{no}"); w=waitfor(lambda: [m for m in full()["msgs"] if m["dir"]=="out"][-1]["state"]=="acked",15)
t("their ack marks it delivered",w is not None)
p0=mark(); r=ap(op="beacon"); time.sleep(4); tx=sent(p0)
t("beacon now: our position on the air",r[0]==200 and any(l.startswith("W5PL-10>APZYNR,WIDE1-1:=3938.75N/10457.50W-") for l in tx),tx)
t("bad message refused",ap(op="send",to="not a call",text="x")[0]==409 and ap(op="send",to="N0CALL",text="")[0]==409)
log=open("log_"+os.path.basename(sys.argv[0])[:-3]+".txt").read(); t("keyed through the CAT port, no safety un-keys",log.count("radio KEYED")>=3 and "SAFETY" not in log)
# Mic-E: our decoding against Dire Wolf's own
dwl="\n".join(full()["log"]); t("Mic-E position decoded as Dire Wolf's decode_aprs does (N 33 25.64, W 012 07.74)",abs(S["KE5ABC-9"]["lat"]-33.4273)<.001 and abs(S["KE5ABC-9"]["lon"]+12.129)<.001,S["KE5ABC-9"])
# stop
r=ap(op="stop"); w=waitfor(lambda: not st(A)["aprs"]["on"],8)
t("stop: all gone",r[0]==200 and w is not None and st(A)["audio"] is None and subprocess.run(["pgrep","-f","direwolf -c .*aprs-run"],capture_output=True).stdout.strip()==b"")
t("iGate on without a passcode: refused",req(A,"/api/admin",{"op":"save","cfg":{"aprs":{"igate":"rx"}},"aprs_pass":""},who="adm")[0]==200 and ap(op="start")[0]==409)
log=open("log_"+os.path.basename(sys.argv[0])[:-3]+".txt").read(); t("no tracebacks",log.count("Traceback")==0)
keep.on=False; v.run=False; print(f"{ok} passed, {bad} failed")
