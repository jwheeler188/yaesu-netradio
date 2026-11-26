src=open("acctest.py").read().split("# ---- 1 other websites")[0]; exec(src)
A="127.0.0.2"; keep([A]); au=Audio(A); time.sleep(2.5); L=lambda: open("log_"+__import__("os").path.basename(__import__("sys").argv[0])[:-3]+".txt").read(); cmd=lambda **j: req(A,"/api/cmd",j); cmd(a="freq",hz=14250000); time.sleep(1)
def stuck(): return st(A)["access"]["stuck"]
h=Hold(A); time.sleep(1); h.up(); time.sleep(2.2); t("normal un-key: no alarm",stuck() is None and not st(A)["keyed"])
h=Hold(A); time.sleep(.8); fake(swr=0,stuck=True); h.up(); time.sleep(5.5); x=stuck(); t("still transmitting after un-key: alarm",x and "STILL TRANSMITTING" in x["why"],x); r=ptt(A,start=True); t("PTT locked",r[0]==409 and not st(A)["keyed"],r); t("CAT PTT locked","RPRT -9" in cat("127.0.0.3",["T 1"])[0])
fake(swr=0); time.sleep(4); t("back in receive: unlocks by itself",stuck() is None); h=Hold(A); time.sleep(.6); t("keys again",st(A)["keyed"],h.last); h.up(); time.sleep(3)
h=Hold(A); time.sleep(.8); fake(swr=0,dead=True); h.up(); time.sleep(5.5); x=stuck(); t("silent after un-key: alarm",x and "STOPPED ANSWERING" in x["why"],x); t("panel shows not answering",not st(A)["rig_ok"]); t("PTT locked",ptt(A,start=True)[0]==409); fake(swr=0); time.sleep(14); t("recovers",stuck() is None and st(A)["rig_ok"],(stuck(),st(A)["rig_ok"])); h=Hold(A); time.sleep(.6); t("keys again 2",st(A)["keyed"],h.last); h.up(); time.sleep(2)
# quick re-key while the check is running: no false alarm
for _ in range(3): h=Hold(A); time.sleep(.7); h.up(); time.sleep(.25)
time.sleep(4); t("quick overs: no false alarm",stuck() is None)
# no panel open: still noticed
keep.on=False; h=Hold(A); time.sleep(.8); fake(swr=0,stuck=True); h.up(); time.sleep(6); t("noticed and logged",L().count("STILL TRANSMITTING")>=2); fake(swr=0); time.sleep(6)
print(f"{ok} passed, {bad} failed")
# settings
print([ (k, req(A,"/api/admin",{"op":"setup","password":"x"*9},who="adm")[0]) for k in (1,)]); j=req(A,"/api/admin",{"op":"save","cfg":{"mic":12,"spk":2}},who="adm")[1]["cfg"]; t("levels saved",j["mic"]==12 and j["spk"]==2,j); j=req(A,"/api/admin",{"op":"save","cfg":{"mic":999,"spk":None}},who="adm")[1]["cfg"]; t("clamped / cleared",j["mic"]==35 and j["spk"] is None,j); j=req(A,"/api/admin",{"op":"save","cfg":{"mic":"x"}},who="adm")[1]["cfg"]; t("nonsense ignored",j["mic"]==35,j); print(f"{ok} passed, {bad} failed")
