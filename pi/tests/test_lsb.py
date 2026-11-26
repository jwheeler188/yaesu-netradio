src=open("acctest.py").read().split("# ---- 1 other websites")[0]; exec(src)
import math
A="127.0.0.2"; B="127.0.0.3"
def raw(c):
    s=socket.create_connection(("127.0.0.1",14635),3); s.sendall(c.encode()+b"\n"); time.sleep(.15); r=s.recv(999).decode(); s.close(); return r.strip()
def cmd(**j): return req(A,"/api/cmd",j)
TF=[b"".join(struct.pack("<h",int(8000*math.sin(2*math.pi*700*(960*k+i)/48000))) for i in range(960)) for k in range(12)]
class V(Audio):
    mode=True
    def tx(s):
        n=0; ph=0
        while s.run:
            try:
                s.f(b"A",TF[n%12]); n+=1
                if n%10==0: s.f(b"P"); s.f(b"S",json.dumps({"voice":s.mode,"client":"t","set":{"drive":-5}}).encode())
            except OSError: s.run=False
            time.sleep(.02)
keep([A]); v=V(A); time.sleep(1.5)
cmd(a="freq",hz=7200000); cmd(a="mode",mode="LSB"); time.sleep(.8)
r=cmd(a="mode",mode="DLSB"); time.sleep(.8); x=st(A); t("LSB voice on",r[0]==200 and x["rig"]["mode"]=="DLSB" and x["rig"]["freq"]==7200000,(r,x["rig"])); t("radio is in DIG, 3 kHz low",raw("f")=="7197000" and raw("m").startswith("PKTUSB"),(raw("f"),raw("m"))); t("shows the dial",x["rig"]["dial"]==7197000)
cmd(a="freq",hz=7185500); time.sleep(.4); t("tuning",raw("f")=="7182500" and st(A)["rig"]["freq"]==7185500,(raw("f"),st(A)["rig"]["freq"]))
cmd(a="nudge",hz=-120); time.sleep(.4); t("nudge",raw("f")=="7182380" and st(A)["rig"]["freq"]==7185380)
cmd(a="mode",mode="DLSB"); time.sleep(.3); t("pressing it again changes nothing",raw("f")=="7182380")
# knob
raw("F 7180000"); time.sleep(1.2); t("radio's knob: carrier follows",st(A)["rig"]["freq"]==7183000,st(A)["rig"]["freq"])
t("VFO / split refused",cmd(a="vfo",vfo="VFOB")[0]==409 and cmd(a="split",on=True)[0]==409)
# audio: dummy receive tone is 1000 Hz -> must arrive as 2000
def tone(sec=1.0,full=False):
    c=http.client.HTTPConnection("127.0.0.1",W,timeout=10,source_address=(A,0)); c.request("GET","/api/audio"+("?full=1" if full else ""),headers={"Host":"127.0.0.1"}); r=c.getresponse(); b=b""; kinds=set(); fs=48000 if full else 8000
    while len(b)<fs*2*sec:
        h=r.read(3); L=h[1]<<8|h[2]
        if L: kinds.add(h[:1]); d=r.read(L); b+=d if any(d) else b""
    c.close(); n=len(b)//2; s=struct.unpack(f"<{n}h",b[:n*2]); s=s[n//4:]; best=max(((abs(sum(v*complex(math.cos(2*math.pi*f*i/fs),-math.sin(2*math.pi*f*i/fs)) for i,v in enumerate(s[:800]))),f) for f in range(200,3000,100))); return best[1],kinds
t("receive audio inverted (1000 -> 2000 Hz)",tone()[0]==2000)
h=Hold(A); time.sleep(1.2); k=st(A); t("keys on LSB",k["keyed"],h.last); tn=tone(.4); t("waterfall shows the voice as it is while sending",tn[0]==700 and tn[1]=={b"T"},tn); t("can't change mode or leave while sending",cmd(a="mode",mode="USB")[0]==409 and cmd(a="mode",mode="FM")[0]==409 and st(A)["rig"]["mode"]=="DLSB"); h.up(); time.sleep(.6)
# what went to the radio: checked on the inverter itself (see invtest); timing here
log=open("log_"+__import__("os").path.basename(__import__("sys").argv[0])[:-3]+".txt").read(); t("no underruns",re.findall(r"(\d+) underruns",log)[-1]=="0",re.findall(r"(\d+) underruns",log)[-3:])
# leaving
cmd(a="mode",mode="USB"); time.sleep(.6); t("leaving puts the dial on the carrier",raw("f")=="7183000" and raw("m").startswith("USB") and st(A)["rig"]["mode"]=="USB",(raw("f"),raw("m"))); t("receive audio normal again",tone()[0]==1000)
# mode changed at the radio
cmd(a="mode",mode="DLSB"); time.sleep(2.5); raw("M CW 500"); time.sleep(11.5); t("mode changed at the radio: off",st(A)["rig"]["mode"]=="CW",st(A)["rig"]["mode"])
cmd(a="mode",mode="DLSB"); time.sleep(.6); t("on again from CW",raw("m").startswith("PKTUSB") and st(A)["lsb"]); raw("M USB 2400"); p=ptt(A,start=True); t("PTT right after that is refused",p[0]==409 and not st(A)["keyed"],p); ptt(A,False)
# WSJT-X
cmd(a="freq",hz=7200000); cmd(a="mode",mode="DLSB"); time.sleep(.5); o=cat(B,["f","m"]); t("an app that only reads leaves it on",st(A)["lsb"],o); o=cat(B,["F 7074000"]); time.sleep(.3); t("an app that tunes turns it off",not st(A)["lsb"] and raw("f")=="7074000",(st(A)["lsb"],raw("f")))
# data client
cmd(a="freq",hz=7200000); cmd(a="mode",mode="DLSB"); time.sleep(.5); v.mode=False; time.sleep(1.5); t("a data audio client turns it off, dial back on the carrier",not st(A)["lsb"] and raw("f")=="7200000",(st(A)["lsb"],raw("f"))); t("and it can't be turned on with one",cmd(a="mode",mode="DLSB")[0]==409); v.mode=True; time.sleep(1)
# split
raw("S 1 VFOB"); t("split on: refused",cmd(a="mode",mode="DLSB")[0]==409); raw("S 0 VFOA")
# locks: checked on the carrier and the 2.8 kHz below it
req(A,"/api/admin",{"op":"setup","password":secrets.token_urlsafe(9)},who="adm")
def save(**k): return req(A,"/api/admin",{"op":"save","cfg":k},who="adm")
save(blocks=[{"lo":7210000,"hi":7212000,"note":"x"}])
for hz,want in((7200000,1),(7300000,1),(7301000,0),(7002000,0),(7003000,1),(7211000,0),(7213500,0),(7209000,1),(7215000,1)):
    cmd(a="freq",hz=hz); cmd(a="mode",mode="DLSB"); time.sleep(.35); p=ptt(A,start=True); time.sleep(.15); k=st(A)["keyed"]; ptt(A,False); time.sleep(.2); t(f"LSB carrier {hz/1e6:.4f} {'allowed' if want else 'locked'}",(p[0]==200 and k) if want else (p[0]==409 and not k),p)
cmd(a="freq",hz=7214000); time.sleep(.4); 
# swr trip is filed under the carrier's band
cmd(a="freq",hz=14000000+4000); h=Hold(A); time.sleep(.6); fake(swr=9); time.sleep(1.0); t("high SWR trips on the right band",list(st(A)["access"]["trips"])==["20"],st(A)["access"]["trips"]); h.up(); fake(swr=0); req(A,"/api/swr_reset",{"band":"20"},who="adm")
# dial turned into a lock while sending
cmd(a="freq",hz=7205000); time.sleep(.3); h=Hold(A); time.sleep(.5); raw("F 7208000"); time.sleep(1.6); t("knob into a locked range while sending: un-keyed",not st(A)["keyed"],st(A)["rig"]["freq"]); h.up(); time.sleep(.3)
# favorites
cmd(a="freq",hz=3873000); cmd(a="mode",mode="DLSB"); time.sleep(.3); req(A,"/api/repeaters",{"op":"put","item":{"id":"f1","freq":3873000,"mode":"DLSB","name":"75 m net","fav":True}},who="adm"); req(A,"/api/repeaters",{"op":"put","item":{"id":"f2","freq":443225000,"mode":"FM","shift":"+","offset":5000000,"tone":103.5,"fav":True}},who="adm")
req(A,"/api/cmd",{"a":"repeater","id":"f2"}); time.sleep(.6); t("LSB voice -> FM favorite",raw("f")=="443225000" and not st(A)["lsb"] and st(A)["rig"]["mode"]=="FM",(raw("f"),st(A)["rig"]["mode"]))
req(A,"/api/cmd",{"a":"repeater","id":"f1"}); time.sleep(.6); t("FM -> LSB voice favorite",raw("f")=="3870000" and st(A)["rig"]["mode"]=="DLSB" and st(A)["rig"]["freq"]==3873000,(raw("f"),st(A)["rig"]))
print(f"{ok} passed, {bad} failed")
