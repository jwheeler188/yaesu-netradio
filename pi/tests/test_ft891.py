# the whole bridge against sim_ft891.py through the real Hamlib (see run_ft891.sh)
src=open("acctest.py").read().split("# ---- 1 other websites")[0]; exec(src)
import json
def F(**k): open("f.json","w").write(json.dumps(k))
A="127.0.0.2"; keep([A]); au=Audio(A); time.sleep(4); cmd=lambda **j: req(A,"/api/cmd",j); L=lambda: open("log_ft891.txt").read(); R=lambda: st(A)["rig"]
def simlog(): return open("log.txt").read()
def w(f,s=4):
    e=time.time()+s
    while time.time()<e:
        if f(): return True
        time.sleep(.1)
    return False
x=st(A); t("radio known",x["radio"]["key"]=="ft891" and x["rig_ok"] and not x["radio"]["problem"],x["radio"]); t("baud found","found on" in L()); t("no 2 m, 70 cm",x["radio"]["max"]==56000000); t("modes",x["modes"][:2]==["LSB","USB"] and "DLSB" not in x["modes"])
t("reads frequency and mode",w(lambda:R().get("freq")==14250000 and R().get("mode")=="USB"),R()); t("power shown",w(lambda:R().get("power_w")==100),R()); t("S-meter",w(lambda:8<=R().get("smeter",0)<=9),R())
for hz in (7200000,50125000,30000): cmd(a="freq",hz=hz); t(f"frequency {hz}",w(lambda:R()["freq"]==hz),R()["freq"])
r=cmd(a="freq",hz=146520000); t("2 m refused",r[0]==409,r)
for m in x["modes"]: r=cmd(a="mode",mode=m); t("mode "+m,r[0]==200 and w(lambda:R()["mode"]==m),(r,R()["mode"]))
t("no AB; from the bridge's own mode changes","AB;" not in simlog())
t("LSB voice refused",cmd(a="mode",mode="DLSB")[0]!=200); cmd(a="freq",hz=14250000); cmd(a="mode",mode="USB")
for hz in (100,-250,0): r=cmd(a="rit",hz=hz); t(f"RIT {hz}",r[0]==200 and w(lambda:st(A)["rit"]==hz),(r,st(A)["rit"]))
r=cmd(a="power",w=10); t("power 10 W",r[0]==200 and w(lambda:R()["power_w"]==10),(r,R().get("power_w"))); cmd(a="power",w=1); t("power not below 5",w(lambda:R()["power_w"]==5),R()["power_w"])
r=cmd(a="swap"); t("swap VFOs",r[0]==200 and w(lambda:R()["freq"]==14260000),(r,R()["freq"])); cmd(a="swap"); t("VFO A/B refused politely",cmd(a="vfo",vfo="VFOB")[0]==409)
r=cmd(a="split",on=True); t("split on",r[0]==200 and w(lambda:R().get("split")),r); r=cmd(a="split",on=False); t("split off",r[0]==200 and w(lambda:R().get("split") is False),r)
r=cmd(a="LOCK",on=True); t("lock",r[0]==200 and "LK1;" in simlog(),r); cmd(a="LOCK",on=False)
# split: the transmit VFO is checked
cmd(a="split",on=True); time.sleep(1); req(A,"/api/admin",{"op":"setup","password":"x"*9},who="adm"); c=req(A,"/api/admin",who="adm")[1]["cfg"]; t("bands without 2 m",[b["name"] for b in c["bands"]][-1]=="6",c["bands"][-1]); t("SWR step 7",c["swr"]["level"]==7,c["swr"])
cat("127.0.0.3",["S 1 VFOB","I 14400000"]); time.sleep(.5); r=ptt(A,start=True); t("VFO B out of band: refused",r[0]==409,r); t("not keyed","TX1;" not in simlog())
cat("127.0.0.3",["I 14300000"]); time.sleep(.5); r=ptt(A,start=True); time.sleep(.3); t("VFO B in band: keys",r[0]==200 and st(A)["keyed"],r); ptt(A,False); time.sleep(2)
o=cat("127.0.0.3",["S 0 VFOA"]); t("app: split off works (Hamlib itself refuses)","RPRT 0" in o[0] and w(lambda:R().get("split") is False),o); o=cat("127.0.0.3",["+\\set_split_vfo 0 VFOA"]); t("long form","RPRT 0" in o[0],o)
# keying and meters
h=Hold(A); t("keys",w(lambda:st(A)["keyed"] and "TX1;" in simlog()),h.last); t("meters while keyed",w(lambda:R().get("swr_ratio") is not None and R().get("po",0)>.02 and R().get("alc",0)>.1),R()); h.up(); t("un-keys",w(lambda:not st(A)["keyed"])); time.sleep(1.5); t("no alarm",st(A)["access"]["stuck"] is None); t("meters in the log","radio meters" in L())
h=Hold(A); time.sleep(4.5); t("long over stays keyed",h.on and st(A)["keyed"],h.last); h.up(); time.sleep(2)
# high SWR
h=Hold(A); time.sleep(1); F(swr=70); time.sleep(2.5); t("1.6:1 does not trip",st(A)["keyed"],h.last); F(swr=130); t("2.8:1 trips",w(lambda:not st(A)["keyed"],4)); x=st(A)["access"]; t("band locked, says why","20" in x["trips"] and ":1" in x["trips"]["20"]["why"],x["trips"]); h.up(); F(); t("locked",ptt(A,start=True)[0]==409); req(A,"/api/swr_reset",{"band":"20"}); time.sleep(.5)
h=Hold(A); time.sleep(1); F(hi_swr=True); t("the radio's own high-SWR flag trips",w(lambda:not st(A)["keyed"],4)); h.up(); F(); req(A,"/api/swr_reset",{"band":"20"}); time.sleep(2)
# stuck and dead
h=Hold(A); time.sleep(1); F(stuck=True); h.up(); t("stuck in transmit: alarm",w(lambda:(st(A)["access"]["stuck"] or {}).get("why","").find("STILL TRANSMITTING")>=0,8),st(A)["access"]["stuck"]); t("sent TX0 again",simlog().count("TX0;")>=3); t("locked",ptt(A,start=True)[0]==409); F(); t("unlocks in receive",w(lambda:st(A)["access"]["stuck"] is None,14)); time.sleep(1)
F(dead=True); t("dead radio noticed",w(lambda:not st(A)["rig_ok"],25)); r=ptt(A,start=True); t("dead: PTT refused",r[0]==409,r); F(); t("back",w(lambda:st(A)["rig_ok"],20)); time.sleep(1)
h=Hold(A); time.sleep(1); F(dead=True); h.up(); t("dies while keyed: alarm",w(lambda:(st(A)["access"]["stuck"] or {}).get("why","").find("STOPPED ANSWERING")>=0,20),st(A)["access"]["stuck"]); F(); t("recovers",w(lambda:st(A)["access"]["stuck"] is None and st(A)["rig_ok"],25)); time.sleep(1)
# FM: the radio reports shift and tones
cmd(a="freq",hz=29640000); cmd(a="mode",mode="FM"); r=cmd(a="shift",dir="-"); t("shift -",r[0]==200 and w(lambda:st(A)["rpt"]["shift"]=="-" and st(A)["rpt"]["tx"]==29540000),(r,st(A)["rpt"]))
r=cmd(a="tone",hz=103.5); cmd(a="TONE",on=True); t("tone",r[0]==200 and w(lambda:st(A)["rpt"]["mode"]=="tone" and st(A)["rpt"]["tone"]==103.5),st(A)["rpt"]); cmd(a="TSQL",on=True); t("TSQL",w(lambda:st(A)["rpt"]["mode"]=="tsql"))
r=cmd(a="dcs",code=23,on="dsql"); t("DSQL",r[0]==200 and w(lambda:st(A)["rpt"]["mode"]=="dsql" and st(A)["rpt"]["dcs"]==23),(r,st(A)["rpt"])); t("DCS transmit-only refused",cmd(a="dcs",code=23,on="dcs")[0]==409)
r=cmd(a="offset",hz=600000); t("offset: says to use the menu",r[0]==409 and "menu" in r[1]["error"],r); t("same offset accepted",cmd(a="offset",hz=100000)[0]==200)
h=Hold(A); time.sleep(3); t("repeater over stays keyed",h.on and st(A)["keyed"],h.last); h.up(); time.sleep(2)
cmd(a="freq",hz=28050000); cmd(a="shift",dir="-"); time.sleep(.8); r=ptt(A,start=True); t("shift out of band refused",r[0]==409 and "shift" in r[1]["error"],r)
r=cmd(a="simplex"); t("simplex",r[0]==200 and w(lambda:st(A)["rpt"]["shift"]=="0" and st(A)["rpt"]["mode"]=="none"),(r,st(A)["rpt"]))
cmd(a="freq",hz=53000000); cmd(a="shift",dir="-"); t("6 m offset 1 MHz",w(lambda:st(A)["rpt"]["tx"]==52000000),st(A)["rpt"]); cmd(a="simplex")
req(A,"/api/repeaters",{"op":"put","item":{"id":"f1","freq":29620000,"mode":"FM","shift":"-","offset":100000,"tone":88.5,"tmode":"tone","fav":True}}); r=req(A,"/api/cmd",{"a":"repeater","id":"f1"}); t("favorite",r[0]==200 and w(lambda:st(A)["rpt"]["tone"]==88.5 and st(A)["rpt"]["tx"]==29520000),(r,st(A)["rpt"]))
# a set command the radio ignores is reported
F(noset=["MD"]); r=cmd(a="mode",mode="USB"); t("ignored command is reported",r[0]==409,r); F(); time.sleep(.3); cmd(a="mode",mode="USB"); cmd(a="freq",hz=14250000)
# the dial turned at the radio
import socket
t("no panel command, dial moved: seen",True if cat("127.0.0.3",["F 14300000"]) and w(lambda:R()["freq"]==14300000) else False,R()["freq"])
cmd(a="smeter",on=False); time.sleep(3); n=simlog().count("\n"); time.sleep(4); t("Live S-meter off: quiet line",simlog().count("\n")-n<12,simlog().count("\n")-n); cmd(a="smeter",on=True)
t("no Python errors","Traceback" not in L() and "failed:" not in L())
print(f"{ok} passed, {bad} failed")
