src=open("acctest.py").read().split("# ---- 1 other websites")[0]; exec(src)
# Stations heard on the radio are kept across a bridge restart (aprs-stations.json); "Forget stations" ages them out.
# Restarts the test bridge itself (the one run_all.sh started on 18080), by its exact command line.
import signal, subprocess
A="127.0.0.2"
def ap(**j): return req(A,"/api/aprs",j)
def full(): return req(A,"/api/aprs")[1]
def calls(): return {s["call"]: s for s in full()["stations"]}
ARGS=["python3","nb.py","--dummy","-v","--cat-port","14633","--audio-port","14634","--web-port","18080","--rigctld-port","14635"]
def bridge_pids(): return [int(l.split()[0]) for l in subprocess.run(["ps","-eo","pid,args"],capture_output=True,text=True).stdout.splitlines()[1:] if l.split()[1:]==ARGS]
def restart(edit=None):
    keep.on=False
    for p in bridge_pids(): os.kill(p, signal.SIGTERM)
    for _ in range(80):
        if not bridge_pids(): break
        time.sleep(.1)
    time.sleep(1)
    if edit: edit()
    subprocess.Popen(ARGS, stdout=open("log_test_aprs_keep_2.txt","a"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(60):
        time.sleep(.5)
        try:
            if req(A,"/api/state")[0]==200: break
        except Exception: pass
    keep.on=True; keep([A]); time.sleep(1)
keep([A])
t("default: forget after 1 day",full()["settings"]["keep_h"]==24)
ap(op="set",call="W5PL-10",grid="DM79mp",beacon=0,show_is=False)
ap(op="inject",lines=["N0CALL>APRS,WIDE2-1:!3940.00N/10500.00W-heard on the radio","N0CALL-9>APRS,WIDE1-1:!3941.00N/10501.00W>a mobile"])
ap(op="inject",src="is",lines=["K0NET>APRS,TCPIP*,qAC,T2TEST:!3942.00N/10502.00W-internet only"])
t("three stations before the restart",{"N0CALL","N0CALL-9","K0NET"}<=set(calls()))
restart()
c=calls(); j=json.load(open("aprs-stations.json"))
t("saved when the bridge stops: radio stations only",set(j["stations"])=={"N0CALL","N0CALL-9"},set(j["stations"]))
t("radio stations back after the restart, with position",set(c)=={"N0CALL","N0CALL-9"} and c["N0CALL"].get("lat") and c["N0CALL"]["comment"]=="heard on the radio",list(c))
t("... the internet-only station is not",("K0NET" not in c))
t("the log says how many",open("log_test_aprs_keep_2.txt").read().count("remembered 2 station(s) heard on the radio")==1)
def age():                                         # pretend N0CALL was last heard 2 h ago and N0CALL-9 2 days ago
    j=json.load(open("aprs-stations.json")); now=time.time()
    j["stations"]["N0CALL"]["last"]=now-7200; j["stations"]["N0CALL-9"]["last"]=now-48*3600
    json.dump(j,open("aprs-stations.json","w"))
restart(age); c=calls()
t("older than the setting (1 day): forgotten at start",set(c)=={"N0CALL"},list(c))
r=ap(op="set",keep_h=1); c=calls()
t("setting it to 1 hour forgets the 2-hour-old one at once",r[0]==200 and not c and full()["settings"]["keep_h"]==1,list(c))
t("... and the saved file follows",json.load(open("aprs-stations.json"))["stations"]=={})
ap(op="set",keep_h=5); t("a value not in the list is ignored",full()["settings"]["keep_h"]==1)
ap(op="set",keep_h=0); t("never is allowed",full()["settings"]["keep_h"]==0)
ap(op="inject",lines=["N0CALL-7>APRS:!3943.00N/10503.00W-again"]); ap(op="clear")
t("Clear list empties the saved file too",json.load(open("aprs-stations.json"))["stations"]=={} and not calls())
json.dump({"v":1,"stations":{"BAD":{"call":"OTHER","last":"x"},"N0CALL":5}},open("aprs-stations.json","w")); restart()
t("a damaged file is ignored",calls()=={} if isinstance(calls(),dict) else True)
log=open("log_test_aprs_keep.txt").read()+open("log_test_aprs_keep_2.txt").read(); t("no tracebacks",log.count("Traceback")==0)
keep.on=False
for p in bridge_pids(): os.kill(p, signal.SIGTERM)
print(f"{ok} passed, {bad} failed")
