src=open("acctest.py").read().split("# ---- 1 other websites")[0]; exec(src)
# APRS handling in the bridge without Dire Wolf or the sound card: packets go in through the test-mode feed (--dummy only).
A="127.0.0.2"
def ap(**j): return req(A,"/api/aprs",j)
def full(): return req(A,"/api/aprs")[1]
keep([A])
ap(op="set",call="W5PL-10",grid="DM79mp",beacon=0,show_is=False)
r=ap(op="inject",lines=["N0CALL>APRS,WIDE2-1:!4903.50N/07201.75W-Somebody else",
                        "W5PL-10>APZYNR,N0CALL-1*,WIDE2*:=3938.75N/10457.50W-yaesu-netradio"])
f=full(); S={s["call"] for s in f["stations"]}
t("our own packet repeated by a digipeater: not a station",r[0]==200 and "W5PL-10" not in S and "N0CALL" in S,S)
t("... shown as heard back via the digipeater",f["echo"] and f["echo"]["src"]=="rf" and f["echo"]["via"]=="N0CALL-1",f["echo"])
t("... and marked in the packet log",any(p["src"]=="echo" and p["via"]=="N0CALL-1" for p in f["packets"]))
ap(op="inject",src="is",lines=["W5PL-10>APZYNR,WIDE2-1,qAR,N0CALL-15:=3938.75N/10457.50W-yaesu-netradio"]); f=full()
t("gated to APRS-IS: says by which iGate",f["echo"]["src"]=="is" and f["echo"]["via"]=="N0CALL-15" and "W5PL-10" not in {s["call"] for s in f["stations"]},f["echo"])
ap(op="inject",lines=["N0CALL-15>APRS,TCPIP*:}W5PL-10>APZYNR,TCPIP,N0CALL-15*::N0CALL   :test{3"]); f=full()
t("our message heard back is not taken as a message",not [m for m in f["msgs"] if m["dir"]=="in"] and "W5PL-10" not in {s["call"] for s in f["stations"]})
ap(op="inject",lines=["W5PL-9>APRS,WIDE1-1:!3930.00N/10500.00W>my mobile"]); f=full()
t("a different SSID of the same call is a station",any(s["call"]=="W5PL-9" for s in f["stations"]))
ap(op="inject",lines=["N0CALL>APRS::W5PL-10  :hi{5"]); f=full()
t("messages to us still work",[m for m in f["msgs"] if m["dir"]=="in" and m["text"]=="hi"])
log=open("log_"+os.path.basename(sys.argv[0])[:-3]+".txt").read(); t("no tracebacks",log.count("Traceback")==0)
keep.on=False; print(f"{ok} passed, {bad} failed")
