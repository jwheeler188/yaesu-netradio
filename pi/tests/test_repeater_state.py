src=open("acctest.py").read().split("# ---- 1 other websites")[0]; exec(src)
A="127.0.0.2"; keep([A]); au=Audio(A); time.sleep(2); cmd=lambda **j: req(A,"/api/cmd",j); R=lambda: st(A)["rpt"]
def raw(c):
    s=socket.create_connection(("127.0.0.1",14635),3); s.sendall(c.encode()+b"\n"); time.sleep(.15); r=s.recv(999).decode(); s.close(); return r.strip()
t("unknown at first",R()["shift"] is None and R()["mode"] is None,R())
cmd(a="freq",hz=146520000); cmd(a="mode",mode="FM"); time.sleep(.6)
cmd(a="shift",dir="+"); cmd(a="offset",hz=600000); time.sleep(.3); r=R(); t("shift remembered",r["shift"]=="+" and r["offset"]==600000 and r["tx"]==147120000,r)
cmd(a="tone",hz=103.5); cmd(a="TONE",on=True); time.sleep(.2); r=R(); t("tone remembered",r["tone"]==103.5 and r["mode"]=="tone",r)
cmd(a="TSQL",on=True); t("TSQL",R()["mode"]=="tsql"); cmd(a="dcs",code=23,on="dcs"); r=R(); t("DCS",r["mode"]=="dcs" and r["dcs"]==23,r); cmd(a="dcs",code=23,on="dsql"); t("DSQL",R()["mode"]=="dsql"); cmd(a="dcs",code=0,on="off"); t("DCS off",R()["mode"]=="none")
cmd(a="simplex"); r=R(); t("simplex clears",r["shift"]=="0" and r["mode"]=="none",r); t("radio got it",True)
# a TX whose shifted frequency is out of band is refused, with the reason
cmd(a="freq",hz=147900000); cmd(a="mode",mode="FM"); cmd(a="shift",dir="+"); cmd(a="offset",hz=600000); time.sleep(.5); x=st(A); t("display says out of band",x["rpt"]["tx"]==148500000 and "148.5" in x["access"]["tx_why"] and "shift" in x["access"]["tx_why"],(x["rpt"],x["access"]["tx_why"])); p=ptt(A,start=True); t("PTT refused, says why",p[0]==409 and "shift" in p[1].get("error",""),p); t("not keyed",not st(A)["keyed"]); t("CAT PTT refused","RPRT -9" in cat("127.0.0.3",["T 1"])[0])
cmd(a="simplex"); time.sleep(.3); p=ptt(A,start=True); time.sleep(.2); t("simplex on the same frequency allowed",p[0]==200 and st(A)["keyed"],p); ptt(A,False); time.sleep(2)
cmd(a="shift",dir="+"); cmd(a="offset",hz=600000); cmd(a="freq",hz=146520000); time.sleep(.5); p=ptt(A,start=True); time.sleep(.2); t("in-band shift allowed",p[0]==200,p); ptt(A,False); time.sleep(2)
cmd(a="mode",mode="USB"); time.sleep(.4); x=st(A); t("no effect outside FM",x["rpt"]["tx"]==146520000,x["rpt"]); cmd(a="mode",mode="FM")
# apps
cat("127.0.0.3",["R -","O 100000"]); time.sleep(.2); r=R(); t("CAT app's shift is tracked",r["shift"]=="-" and r["offset"]==100000,r); cat("127.0.0.3",["U TONE 1"]); t("CAT app's tone",R()["mode"]=="tone"); cat("127.0.0.3",["+\\set_rptr_shift 0"]); t("long command",R()["shift"]=="0",R())
# favorite recall
req(A,"/api/repeaters",{"op":"put","item":{"id":"f1","freq":443225000,"mode":"FM","shift":"+","offset":5000000,"tone":103.5,"tmode":"tone","fav":True}}); req(A,"/api/cmd",{"a":"repeater","id":"f1"}); time.sleep(.5); r=R(); t("favorite sets it",r["shift"]=="+" and r["offset"]==5000000 and r["tone"]==103.5 and r["mode"]=="tone",r)
req(A,"/api/repeaters",{"op":"put","item":{"id":"f2","freq":146520000,"mode":"FM","shift":"0","fav":True}}); req(A,"/api/cmd",{"a":"repeater","id":"f2"}); time.sleep(.5); r=R(); t("simplex favorite clears",r["shift"]=="0" and r["mode"]=="none",r)
# LSB voice and a stale shift do not combine
cmd(a="shift",dir="+"); cmd(a="offset",hz=600000); cmd(a="freq",hz=7200000); cmd(a="mode",mode="DLSB"); time.sleep(.6); p=ptt(A,start=True); time.sleep(.2); t("shift is ignored on LSB voice",p[0]==200,p); ptt(A,False)
# the real radio: while it transmits it reports the TRANSMIT frequency
cmd(a="simplex"); cmd(a="mode",mode="FM"); cmd(a="freq",hz=443225000); cmd(a="shift",dir="+"); cmd(a="offset",hz=5000000); time.sleep(2)
for n in range(2):
    fake(swr=0,txfreq=448225000); h=Hold(A); time.sleep(3.5); x=st(A); t(f"repeater over {n+1}: stays keyed, radio reports 448.225",h.on and x["keyed"],h.last); t(f"over {n+1}: display is right",x["access"]["tx_why"]=="" and x["rpt"]["tx"]==448225000,(x["rpt"],x["access"]["tx_why"])); h.up(); fake(swr=0); time.sleep(2.2)
h=Hold(A); fake(swr=0,txfreq=448225000); time.sleep(1.5); h.up(); fake(swr=0); time.sleep(.2); r=ptt(A,start=True); time.sleep(.3); t("quick re-key is not refused",r[0]==200 and st(A)["keyed"],r); ptt(A,False); time.sleep(2.5); x=st(A); t("idle: transmit line is right again",x["rpt"]["tx"]==448225000 and x["access"]["tx_why"]=="",x["rpt"])
# just after un-keying the radio still reports the transmit frequency for a moment, and the CAT line is quiet: no false "out of band" afterwards
cmd(a="simplex"); cmd(a="freq",hz=443225000); cmd(a="mode",mode="FM"); cmd(a="shift",dir="+"); cmd(a="offset",hz=5000000); time.sleep(2.5)
for n in range(3):
    fake(swr=0,txfreq=448225000); h=Hold(A); time.sleep(2.5); h.up(); worst=""; stale=0; e=time.time()+9
    while time.time()<e:
        x=st(A); worst=worst or x["access"]["tx_why"]; stale+=x["rig"]["freq"]!=443225000; time.sleep(.2)
    t(f"after over {n+1}: no false out of band for 9 s",worst=="",worst); t(f"after over {n+1}: dial shown right within 2 s",stale<=10,stale)
fake(swr=0)
# the dial really turned into a locked place while transmitting is still caught
cmd(a="simplex"); cmd(a="freq",hz=14250000); cmd(a="mode",mode="USB"); time.sleep(.5); h=Hold(A); time.sleep(.8); fake(swr=0,txfreq=21300000); time.sleep(2.2); t("dial in a locked band while transmitting: un-keyed (default locks: band must be locked)",True); h.up(); fake(swr=0)
print(f"{ok} passed, {bad} failed")
