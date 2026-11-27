src=open("acctest.py").read().split("# ---- 1 other websites")[0]; exec(src)
# CW keyer through DIG (mode DCW). Run by run_all.sh with FT897_TX_RAW set: the transmit audio lands in tx.raw.
import math, array
sys.path.insert(0, "."); import ft897_cw as cwm
A="127.0.0.2"; B="127.0.0.3"; RAW=os.environ.get("FT897_TX_RAW","tx.raw")
def raw(c):
    s=socket.create_connection(("127.0.0.1",14635),3); s.sendall(c.encode()+b"\n"); time.sleep(.15); r=s.recv(999).decode(); s.close(); return r.strip()
def cmd(**j): return req(A,"/api/cmd",j)
def cw(**j): return req(A,"/api/cw",j)
def mark(): return os.path.getsize(RAW) if os.path.exists(RAW) else 0
def heard(p0, pitch):
    """Decodes what went to the radio since byte p0; -> (text, strongest Hz, peak)"""
    d=open(RAW,"rb").read()[p0:]; s=array.array("h",d[:len(d)//2*2])
    x=[sum(s[i:i+6])/6 for i in range(0,len(s)-5,6)]
    dec=cwm.Decoder(pitch,20,auto=False)
    for i in range(0,len(x),160): dec.feed(x[i:i+160])
    dec.feed([0]*8000)
    on=[v for v in s if v]; pk=max(map(abs,s)) if s else 0
    seg=x[:]; best=(0,0)
    k=next((i for i,v in enumerate(seg) if abs(v)>1000),0); seg=seg[k:k+400]
    if len(seg)==400: best=max((abs(sum(v*complex(math.cos(2*math.pi*f*i/8000),-math.sin(2*math.pi*f*i/8000)) for i,v in enumerate(seg))),f) for f in range(300,1100,25))
    return dec.text.strip(), best[1], pk
def waitfor(fn, sec):
    t0=time.time()
    while time.time()-t0<sec:
        if fn(): return time.time()-t0
        time.sleep(.05)
    return None
keep([A])
cmd(a="freq",hz=14050000); cmd(a="mode",mode="USB"); time.sleep(.6)
r=cmd(a="mode",mode="DCW"); time.sleep(.6); x=st(A)
t("CW keyer on",r[0]==200 and x["rig"]["mode"]=="DCW" and x["rig"]["freq"]==14050000 and x["cw"]["on"],(r,x["rig"],x.get("cw")))
t("radio in DIG, dial a pitch below the carrier",raw("f")=="14049300" and raw("m").startswith("PKTUSB"),(raw("f"),raw("m")))
t("mode list has it",x["modes"][-1]=="DCW")
r=cw(op="set",pitch=600,wpm=25); time.sleep(.3); t("pitch 600: dial follows, carrier stays",r[0]==200 and raw("f")=="14049400" and st(A)["rig"]["freq"]==14050000,(r,raw("f")))
t("settings kept",st(A)["cw"]["pitch"]==600 and st(A)["cw"]["wpm"]==25 and json.load(open("cw.json"))["pitch"]==600)
t("PTT button refused in CW",ptt(A,start=True)[0]==409 and not st(A)["keyed"]); ptt(A,False)
# typed text, no audio client connected at all
p0=mark(); r=cw(op="text",text="cq test <ar>"); k=waitfor(lambda: st(A)["keyed"],1.0)
x=st(A); t("text keys the radio",r[0]==200 and k is not None and (x["keyed_by"] or "").startswith("cw:"),(r,k,x["keyed_by"],x["cw"]))
t("queue shown",bool(x["cw"]["queue"]) or bool(x["cw"]["sent"]),x["cw"])
u=waitfor(lambda: not st(A)["keyed"],8)
t("un-keys after the text and the hang time (no audio client needed)",u is not None,st(A)["cw"])
time.sleep(.3); txt,f,pk=heard(p0,600)
t("what went to the radio decodes as the text",txt=="CQ TEST <AR>",txt); t("at 600 Hz",abs(f-600)<=25,f); t("level -6 dBFS",15000<pk<17500,pk)
t("sent text shown",st(A)["cw"]["sent"].endswith("CQ TEST <AR>"),st(A)["cw"]["sent"])
log=open("log_"+os.path.basename(sys.argv[0])[:-3]+".txt").read(); t("no underruns, no safety",re.findall(r"(\d+) underruns",log)[-1]=="0" and "SAFETY" not in log)
# the panel's keyer: events on the panel's clock ("K" = -.- at 20 wpm, dit 60 ms) sent late and in pieces
p0=mark(); T=time.time()*1000+5000; d=60
ev=[[T,1],[T+3*d,0],[T+4*d,1],[T+5*d,0],[T+6*d,1],[T+9*d,0]]
r1=cw(op="key",ev=ev[:2]); time.sleep(.1); r2=cw(op="key",ev=ev[2:4]); time.sleep(.12); r3=cw(op="key",ev=ev[4:])
k=waitfor(lambda: st(A)["keyed"],1.0); t("key events key the radio",r1[0]==r2[0]==r3[0]==200 and k is not None,(r1,r2,r3))
u=waitfor(lambda: not st(A)["keyed"],4); time.sleep(.2); txt,f,pk=heard(p0,600)
t("timing kept: decodes as K",txt=="K",txt); t("un-keyed after the hang",u is not None)
# a held key while the panel goes quiet
T=time.time()*1000; cw(op="key",ev=[[T,1]]); k=waitfor(lambda: st(A)["keyed"],1.0)
u=waitfor(lambda: not st(A)["keyed"],4); x=st(A)
t("key held, panel silent: let go after ~1 s",k is not None and u is not None and 1.0<u+k<3.5 and "stopped answering" in x["cw"]["err"],(k,u,x["cw"]["err"]))
# heartbeats keep a held key down
T=time.time()*1000; cw(op="key",ev=[[T,1]]); waitfor(lambda: st(A)["keyed"],1.0)
for i in range(8): time.sleep(.3); cw(op="key",ev=[])
x=st(A); t("heartbeats keep it down",x["keyed"] and x["cw"]["rx_on"] is not None,x["cw"]); cw(op="key",ev=[[T+2700,0]]); u=waitfor(lambda: not st(A)["keyed"],3); t("let go: un-keys",u is not None)
# stop
cw(op="text",text="vvv "*20); waitfor(lambda: st(A)["keyed"],1.0); time.sleep(1.0); r=cw(op="stop"); u=waitfor(lambda: not st(A)["keyed"],1.0)
t("Stop: un-keyed at once, queue empty",r[0]==200 and u is not None and u<0.5 and not st(A)["cw"]["queue"],(u,st(A)["cw"]["queue"]))
# Farnsworth: 25 wpm characters at 10 wpm
cw(op="set",farns=10); p0=mark(); t0=time.time(); cw(op="text",text="paris paris"); waitfor(lambda: st(A)["keyed"],1.0); u=waitfor(lambda: not st(A)["keyed"],18)
txt,_,_=heard(p0,600); t("Farnsworth: decodes",txt.replace(" ","")=="PARISPARIS",txt); t("and takes longer (~10 wpm: 2 words in ~12 s)",u and 10.5<u<15,u); cw(op="set",farns=0)
# locks
req(A,"/api/admin",{"op":"setup","password":secrets.token_urlsafe(9)},who="adm")
def save(**k): return req(A,"/api/admin",{"op":"save","cfg":k},who="adm")
save(modes={"DCW":"no CW here"}); time.sleep(.3)
x=st(A); t("mode lock shown",x["access"]["mode_why"].startswith("CW keyer is locked"),x["access"]["mode_why"])
cw(op="text",text="test"); time.sleep(1.0); x=st(A); t("mode lock: text refused, not keyed",not x["keyed"] and "locked" in x["cw"]["err"],x["cw"])
save(modes={},blocks=[{"lo":14049900,"hi":14050100,"note":"Extra only"}]); time.sleep(.3)
cw(op="text",text="test"); time.sleep(1.0); x=st(A); t("band lock on the carrier: refused",not x["keyed"] and "Extra only" in x["cw"]["err"],x["cw"])
save(blocks=[]); cmd(a="freq",hz=14050350); time.sleep(.3); t("tuning in CW moves the dial with the pitch",raw("f")=="14049750" and st(A)["rig"]["freq"]==14050350,raw("f"))
# a sender can't be keyed over
cw(op="text",text="test test"); waitfor(lambda: st(A)["keyed"],1.0); t("mode can't change while sending",cmd(a="mode",mode="USB")[0]==409); cw(op="stop"); time.sleep(.5)
# leave / LSB voice / CAT
cmd(a="mode",mode="DLSB"); time.sleep(.5); x=st(A); t("CW -> LSB voice: carrier kept",x["rig"]["mode"]=="DLSB" and x["rig"]["freq"]==14050350 and not x["cw"]["on"] and raw("f")=="14047350",(x["rig"],raw("f")))
cmd(a="mode",mode="DCW"); time.sleep(.5); x=st(A); t("LSB voice -> CW: carrier kept",x["rig"]["mode"]=="DCW" and x["rig"]["freq"]==14050350 and not x["lsb"] and raw("f")=="14049750",(x["rig"],raw("f")))
cmd(a="mode",mode="USB"); time.sleep(.5); t("leaving puts the dial on the carrier",raw("f")=="14050350" and raw("m").startswith("USB") and not st(A)["cw"]["on"],(raw("f"),raw("m")))
cmd(a="mode",mode="DCW"); time.sleep(.4); o=cat(B,["F 7030000"]); time.sleep(.3); t("an app that tunes turns it off",not st(A)["cw"]["on"],o)
cmd(a="mode",mode="DCW"); time.sleep(.4); raw("M CW 500"); time.sleep(11.5); t("mode changed at the radio: off",not st(A)["cw"]["on"],st(A)["rig"]["mode"])
# favorites
cmd(a="freq",hz=7030000); cmd(a="mode",mode="DCW"); time.sleep(.4)
r=req(A,"/api/repeaters",{"op":"put","item":{"name":"CW test","freq":7030000,"mode":"DCW"}}); lst=req(A,"/api/repeaters")[1]["list"]; i=next((y["id"] for y in lst if y["name"]=="CW test"),None)
cmd(a="mode",mode="USB"); cmd(a="freq",hz=14200000); time.sleep(.4); r=cmd(a="repeater",id=i); time.sleep(.5); x=st(A)
t("a CW favorite comes back as CW",i and r[0]==200 and x["rig"]["mode"]=="DCW" and x["rig"]["freq"]==7030000,(i,r,x["rig"]))
t("unknown op refused",cw(op="nope")[0]==409); t("text with nothing sendable refused",cw(op="text",text="~~~")[0]==409)
log=open("log_"+os.path.basename(sys.argv[0])[:-3]+".txt").read(); t("no tracebacks",log.count("Traceback")==0)
keep.on=False; print(f"{ok} passed, {bad} failed")
