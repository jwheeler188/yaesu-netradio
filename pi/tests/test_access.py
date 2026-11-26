import json,os,re,secrets,socket,struct,subprocess,sys,threading,time,http.client
W=18080;CAT=14633;AUD=14634
ok=bad=0
def t(name,c,info=""):
    global ok,bad
    ok+=bool(c); bad+=not c
    print(("pass " if c else "FAIL ")+name+("" if c else f"   {info}"),flush=True)
ck={}
def req(ip,path,body=None,hdr=None,raw=None,who=None,method=None):
    c=http.client.HTTPConnection("127.0.0.1",W,timeout=15,source_address=(ip,0))
    h={"Host":f"192.168.1.50:{W}"}
    if body is not None or raw is not None: h.update({"Content-Type":"application/json","X-Panel":"1"})
    k=who or ip
    if k in ck: h["Cookie"]="ft897s="+ck[k]
    h.update(hdr or {}); h={a:b for a,b in h.items() if b is not None}
    c.request(method or ("POST" if (body is not None or raw is not None) else "GET"),path,raw if raw is not None else (json.dumps(body) if body is not None else None),h)
    r=c.getresponse(); d=r.read()
    sc=r.getheader("Set-Cookie")
    if sc: ck[k]=re.match(r"ft897s=([^;]*)",sc).group(1)
    try: j=json.loads(d)
    except ValueError: j={}
    return r.status,j
def st(ip="127.0.0.2"): return req(ip,"/api/state")[1]
def cat(ip,lines,wait=.4,close=True):
    s=socket.create_connection(("127.0.0.1",CAT),timeout=3,source_address=(ip,0)); out=[]
    for l in lines:
        s.sendall(l.encode()+b"\n"); time.sleep(wait); s.settimeout(.6)
        try: out.append(s.recv(4096).decode())
        except socket.timeout: out.append("")
        except OSError: out.append("closed")
    if close: s.close()
    else: return s,out
    return out
class Audio:
    def __init__(s,ip,voice=True):
        s.s=socket.create_connection(("127.0.0.1",AUD),timeout=5,source_address=(ip,0)); s.got=[]; s.run=True
        s.f(b"H",json.dumps({"name":"t","tx_buffer_ms":160}).encode()); s.voice=voice
        threading.Thread(target=s.rx,daemon=True).start(); threading.Thread(target=s.tx,daemon=True).start()
    def f(s,t,p=b""): s.s.sendall(t+struct.pack(">H",len(p))+p)
    def rx(s):
        b=b""
        try:
            while s.run:
                d=s.s.recv(65536)
                if not d: break
                b+=d
                while len(b)>=3:
                    n=struct.unpack(">H",b[1:3])[0]
                    if len(b)<3+n: break
                    if b[:1]!=b"A": s.got.append((b[:1],b[3:3+n]))
                    b=b[3+n:]
        except OSError: pass
        s.run=False
    def tx(s):
        n=0
        while s.run:
            try:
                s.f(b"A",bytes(1920)); n+=1
                if n%10==0: s.f(b"P")
            except OSError: s.run=False
            time.sleep(.02)
    def close(s): s.run=False; s.s.close()
def ptt(ip,on=True,start=False,**k): return req(ip,"/api/ptt",{"on":on,**({"start":True} if start else {})},**k)
class Hold:
    def __init__(s,ip): s.ip=ip; s.r=ptt(ip,start=True); s.on=s.r[0]==200; s.last=s.r; threading.Thread(target=s.go,daemon=True).start()
    def go(s):
        while s.on:
            time.sleep(.3)
            if s.on:
                s.last=ptt(s.ip)
                if s.last[0]!=200: s.on=False
    def up(s): s.on=False; ptt(s.ip,False)
def keep(ips):
    def go():
        while keep.on:
            for i in list(keep.ips): 
                try: st(i)
                except Exception: pass
            time.sleep(.5)
    keep.on=True; keep.ips=set(ips); threading.Thread(target=go,daemon=True).start()
meters=os.environ["FT897_FAKE_METERS"]
def fake(**k): json.dump(k,open(meters+".t","w")); os.replace(meters+".t",meters)
fake(swr=0)
A,B,C,D="127.0.0.2","127.0.0.3","127.0.0.4","127.0.0.5"
P1,P2=secrets.token_urlsafe(9),secrets.token_urlsafe(9)
# ---- 1 other websites
au=Audio(A); time.sleep(.5)
t("foreign Origin text/plain refused",req(A,"/api/ptt",raw='{"on":true,"start":true}',hdr={"Content-Type":"text/plain","X-Panel":None,"Origin":"http://evil.example"})[0]==403)
t("no X-Panel refused",req(A,"/api/ptt",{"on":True,"start":True},hdr={"X-Panel":None})[0]==403)
t("text/plain refused",req(A,"/api/ptt",{"on":True,"start":True},hdr={"Content-Type":"text/plain"})[0]==403)
t("Origin null refused",req(A,"/api/ptt",{"on":True,"start":True},hdr={"Origin":"null"})[0]==403)
t("cross-site refused",req(A,"/api/ptt",{"on":True,"start":True},hdr={"Sec-Fetch-Site":"cross-site"})[0]==403)
t("rebinding Host refused (GET)",req(A,"/api/state",hdr={"Host":"evil.example:18080"})[0]==403)
t("rebinding Host refused (POST)",req(A,"/api/ptt",{"on":True,"start":True},hdr={"Host":"evil.example"})[0]==403)
t("OPTIONS gives no permission",req(A,"/api/ptt",method="OPTIONS")[0]==501)
for h in ("192.168.1.50:8080","raspberrypi.local:8080","raspberrypi","localhost","[fe80::1]:8080"): t("Host "+h+" ok",req(A,"/api/state",hdr={"Host":h})[0]==200)
t("same Origin ok",req(A,"/api/cmd",{"a":"freq","hz":146520000},hdr={"Origin":f"http://192.168.1.50:{W}"})[0]==200)
t("HTTP to the CAT port is closed, not keyed","closed" in "".join(cat(A,["POST / HTTP/1.1\r\nHost: x\r\n\r\nT 1"])) or not st()["keyed"])
t("not keyed",not st()["keyed"])
s=socket.create_connection(("127.0.0.1",AUD),timeout=3); s.sendall(b"POST / HTTP/1.1\r\n"+b"x"*21000); time.sleep(.4); s.close()
t("HTTP to the audio port changes nothing",(st()["audio"] or {}).get("ip")==A,st()["audio"])
# ---- 2 normal keying still works
h=Hold(A); time.sleep(1.5); t("panel PTT keys",h.on and st()["keyed"],h.last); h.up(); time.sleep(.3); t("panel PTT un-keys",not st()["keyed"])
t("held PTT without a fresh press refused",ptt(A)[0]==409)
o=cat(A,["f","T 1","t","T 0","t","\\dump_state","\\get_powerstat"]); t("CAT keys/un-keys",o[1].startswith("RPRT 0") and o[3].startswith("RPRT 0"),o[:5]); t("dump_state passes",len(o[5])>100)
o=cat(A,["w \\0x00\\0x00\\0x00\\0x00\\0x08","W \\0xBC 5","f T 1","fT 1","T","\\send_cmd x","+T 1","+T 0"]); 
t("raw / hidden PTT refused",all("RPRT -11" in x for x in o[:6]),o); t("extended PTT works","RPRT 0" in o[6],o[6]); t("un-keyed",not st()["keyed"])
h=Hold(A); time.sleep(.5); t("second computer can't key over it",ptt(B,start=True)[0]==409 and "RPRT -9" in cat(B,["T 1"])[0]); t("still keyed by A",st()["keyed_by"]=="web:"+A); h.up(); time.sleep(.2)
# safety un-key is not re-keyed by a held button
h=Hold(A); time.sleep(.5); au.run=False; au.s.close(); time.sleep(3); t("audio loss un-keys and stays un-keyed",not st()["keyed"] and not h.on,h.last); h.up()
au=Audio(A); time.sleep(.5)
# ---- 3 passwords
t("state before setup",st()["access"]["admin_set"] is False)
t("settings need admin",req(A,"/api/admin",{"op":"save","cfg":{"tx":False}})[0]==403)
t("short password refused",req(A,"/api/admin",{"op":"setup","password":"short"})[0]==400)
t("setup",req(A,"/api/admin",{"op":"setup","password":P1},who="adm")[0]==200)
t("second setup refused",req(B,"/api/admin",{"op":"setup","password":P2})[0]==409)
t("admin sees settings",req(A,"/api/admin",who="adm")[1].get("cfg") is not None)
t("others don't",req(B,"/api/admin")[1].get("cfg") is None)
t("cookie stolen by another address is useless",req(B,"/api/admin",hdr={"Cookie":"ft897s="+ck["adm"]})[1].get("cfg") is None)
t("same passwords refused",req(A,"/api/admin",{"op":"password","which":"oper","password":P1,"current":P1},who="adm")[0]==400)
t("wrong current refused",req(A,"/api/admin",{"op":"password","which":"oper","password":P2,"current":"x"*9},who="adm")[0]==403)
t("set operator password",req(A,"/api/admin",{"op":"password","which":"oper","password":P2,"current":P1},who="adm")[0]==200 and req(A,"/api/admin",who="adm")[1]["role"]=="admin")
pw=open("passwords.json").read(); t("file holds only hashes, mode 600",P1 not in pw and P2 not in pw and oct(os.stat("passwords.json").st_mode)[-3:]=="600")
r=[req(D,"/api/login",{"password":"guess%d"%i})[1].get("error") for i in range(7)]; t("guessing is stopped after 5",r[4]=="wrong password" and "too many" in r[5],r)
t("locked out even with the right one","too many" in req(D,"/api/login",{"password":P2})[1].get("error",""))
# ---- 4 control
keep([A,B,C])
time.sleep(.6)
t("take needs the password",req(B,"/api/take",{})[0]==401)
t("admin can't be forged",req(B,"/api/take",{"force":True})[0]==401)
t("operator signs in",req(B,"/api/login",{"password":P2})[1].get("role")=="oper"); t("operator is not admin",req(B,"/api/admin",{"op":"save","cfg":{}})[0]==403)
r=req(B,"/api/take",{}); t("B must wait: A is using it",r[1].get("state")=="waiting",r); t("A still connected",st(A)["audio"]["ip"]==A and st(A)["access"]["req"]["to_me"])
t("C can't answer",req(C,"/api/answer",{"yes":True})[0]==409); t("B can't answer itself",req(B,"/api/answer",{"yes":True})[0]==409)
t("A declines",req(A,"/api/answer",{"yes":False})[0]==200); time.sleep(.2); t("B told",st(B)["access"]["answer"] and not st(B)["access"]["answer"]["yes"]); t("B must wait 60 s",req(B,"/api/take",{})[0]==409); t("A kept audio",au.run and st(A)["audio"]["ip"]==A)
req(C,"/api/login",{"password":P2}); req(A,"/api/login",{"password":P2})
h=Hold(A); time.sleep(.4); t("C asks while A transmits",req(C,"/api/take",{})[1].get("state")=="waiting"); req(A,"/api/answer",{"yes":True}); time.sleep(1); t("not cut mid-transmission",st(A)["keyed"] and st(A)["owner"] is None); h.up(); time.sleep(.6)
t("C gets it after the un-key",st(C)["owner"]==C,st(C)["owner"]); time.sleep(.3); t("A's audio closed",not au.run); t("A can't tune",req(A,"/api/cmd",{"a":"freq","hz":7100000})[0]==403); t("A can't key",ptt(A,start=True)[0]==403); t("A's CAT closed",cat(A,["f"])[0] in ("","closed"))
t("A asks C",req(A,"/api/take",{})[1].get("state")=="waiting"); t("cancel",req(A,"/api/cancel",{})[0]==200 and st(A)["access"]["req"] is None)
t("settings saved",req(A,"/api/admin",{"op":"save","cfg":{"answer_s":10}},who="adm")[1]["cfg"]["answer_s"]==10)
req(A,"/api/take",{}); time.sleep(8); t("not before the time",st(A)["owner"]==C); time.sleep(3); t("no answer: A gets it",st(A)["owner"]==A)
req(A,"/api/admin",{"op":"save","cfg":{"no_answer":"refuse"}},who="adm"); req(C,"/api/take",{}); time.sleep(11); t("no answer + refuse: A keeps it",st(A)["owner"]==A)
keep.ips.discard(C); time.sleep(.1)
au=Audio(A); time.sleep(.4); h=Hold(A); time.sleep(.5)
st0=req(B,"/api/login",{"password":P1},who="adm2")
r=req(B,"/api/take",{"force":True},who="adm2"); time.sleep(.4); t("administrator takes over at once, un-keys",r[1].get("state")=="granted" and st(B)["owner"]==B and not st(B)["keyed"],r); h.up()
req(B,"/api/release",{}); t("released",st(B)["owner"] is None)
req(A,"/api/admin",{"op":"save","cfg":{"need_control":True}},who="adm")
t("need control: no tuning",req(C,"/api/cmd",{"a":"freq","hz":7100000})[0]==403); t("no PTT",ptt(C,start=True)[0]==403); t("CAT refused",cat(C,["f"])[0] in ("","closed")); t("listening page still works",req(C,"/api/state")[0]==200)
req(A,"/api/take",{}); t("after Take control it works",req(A,"/api/cmd",{"a":"freq","hz":14074000})[0]==200); req(A,"/api/admin",{"op":"save","cfg":{"need_control":False}},who="adm"); req(A,"/api/release",{})
# ---- 5 transmit locks
au=Audio(A); time.sleep(.5)
cf=req(A,"/api/admin",who="adm")[1]["cfg"]
def save(**k): return req(A,"/api/admin",{"op":"save","cfg":k},who="adm")
bands=[{**b,"tx":b["name"] not in("160","80","60","17","15","12"),"note":"antenna" if b["name"] in("160","80","60","17","15","12") else ""} for b in cf["bands"]]
save(bands=bands,blocks=[{"lo":7200000,"hi":7300000,"note":"not matched"}],swr={"on":True,"level":4,"reads":3,"flag":True})
def tune(hz): return req(A,"/api/cmd",{"a":"freq","hz":hz})[0]
for hz,want in((3850000,0),(5357000,0),(21300000,0),(7100000,1),(14250000,1),(28400000,1),(7250000,0),(6500000,0),(146520000,1),(1900000,0),(24950000,0),(18130000,0),(7300000,0),(14350000,1),(14350010,0)):
    tune(hz); time.sleep(.1); r=ptt(A,start=True); time.sleep(.15); k=st(A)["keyed"]; ptt(A,False); t(f"panel {hz/1e6:g} MHz {'allowed' if want else 'locked'}",(r[0]==200 and k) if want else (r[0]==409 and not k),r)
    c=cat(A,[f"F {hz}","T 1","T 0"],wait=.25); t(f"CAT   {hz/1e6:g} MHz {'allowed' if want else 'locked'}",("RPRT 0" in c[1]) if want else ("RPRT -9" in c[1]),c)
t("listening on a locked band works",tune(3850000)==200); time.sleep(.8); t("panel says why","locked" in st(A)["access"]["tx_why"],st(A)["access"]["tx_why"])
tune(14250000); time.sleep(.1); h=Hold(A); time.sleep(.4); t("can't move into a locked band while transmitting",tune(21300000)==409 and st(A)["keyed"]); 
# dial turned while transmitting: straight to rigctld, as the radio's own knob would
s=socket.create_connection(("127.0.0.1",14635)); s.sendall(b"F 21300000\n"); time.sleep(1.6); t("dial turned into a locked band: un-keyed",not st(A)["keyed"] and not h.on,(st(A)["keyed"],h.last)); h.up(); s.close()
tune(14250000); time.sleep(.7)
s,o=cat(A,["F 14250000","T 1"],close=False); t("CAT keyed",st(A)["keyed"]); s.sendall(b"F 21300000\n"); time.sleep(.4); t("CAT frequency change refused while keyed",b"RPRT -9" in s.recv(99) and st(A)["keyed"]); s.sendall(b"T 0\n"); time.sleep(.6); s.close()
# split
o=cat(A,["V VFOA","F 14250000","S 1 VFOB","I 21300000","T 1","T 0"],wait=.25); t("split: transmit VFO on a locked band refused","RPRT -9" in o[4],o)
o=cat(A,["I 14300000","T 1","T 0","S 0 VFOA"],wait=.25); t("split: allowed","RPRT 0" in o[1],o)
save(tx=False); t("master switch off",ptt(A,start=True)[0]==409); save(tx=True)
tune(14250000); time.sleep(.1); h=Hold(A); time.sleep(.5); save(tx=False); time.sleep(.5); t("master switch stops a transmission",not st(A)["keyed"]); h.up(); save(tx=True)
save(tx_timeout=10); h=Hold(A); time.sleep(9.2); a1=st(A)["keyed"]; time.sleep(1.6); t("time limit un-keys and stays off",a1 and not st(A)["keyed"]); time.sleep(.5); t("held button doesn't re-key",not st(A)["keyed"]); h.up(); save(tx_timeout=300)
# ---- 6 SWR
h=Hold(A); time.sleep(1); fake(swr=3); time.sleep(1); t("SWR 3 of limit 4: keeps going",st(A)["keyed"]); fake(swr=6); t0=time.time()
while st(A)["keyed"] and time.time()-t0<3: time.sleep(.02)
dt=time.time()-t0; t(f"SWR 6: un-keyed in {dt:.2f} s",dt<1.0); x=st(A)["access"]; t("band locked",list(x["trips"])==["20"],x["trips"]); time.sleep(.6); t("held button doesn't re-key",not st(A)["keyed"]); h.up(); fake(swr=0)
t("20 m stays locked",ptt(A,start=True)[0]==409); tune(28400000); time.sleep(.1); r=ptt(A,start=True); t("10 m still works",r[0]==200,r); ptt(A,False)
t("reset by anyone allowed",req(A,"/api/swr_reset",{"band":"20"})[0]==200); tune(14250000); time.sleep(.1); r=ptt(A,start=True); t("20 m works again",r[0]==200,r); ptt(A,False)
fake(swr=0,hi_swr=True); h=Hold(A); time.sleep(1); t("radio's own warning un-keys",not st(A)["keyed"] and "20" in st(A)["access"]["trips"]); h.up(); fake(swr=0)
save(swr={"reset":"admin"}); t("reset: admin only",req(A,"/api/swr_reset",{"band":"20"},who="nobody")[0]==403); t("trip is in the file","20" in json.load(open("settings.json"))["trips"])
# no panel open: WSJT-X alone
req(A,"/api/swr_reset",{"band":"20"},who="adm"); keep.on=False; time.sleep(6)
s,o=cat(A,["F 14074000","T 1"],close=False); time.sleep(.5); fake(swr=9); time.sleep(1.2); s.sendall(b"t\n"); time.sleep(.3); q=s.recv(99); t("WSJT-X with no panel open: shutdown works",q.startswith(b"0"),q); s.close(); fake(swr=0)
req(A,"/api/swr_reset",{"band":"20"},who="adm")
save(swr={"on":False}); fake(swr=15,hi_swr=True); h=Hold(A); time.sleep(1); t("shutdown off: keeps going",st(A)["keyed"]); h.up(); save(swr={"on":True}); fake(swr=0)
os.remove(meters); h=Hold(A); time.sleep(2.8); t("unreadable meters are reported",st(A)["access"]["meter_ok"] is False); h.up(); fake(swr=0)
# ---- 7 locked settings
save(locks={"txaudio":True,"tone":True,"repeaters":True},max_drive=-8)
au.f(b"S",json.dumps({"voice":True,"set":{"drive":-5}}).encode()); time.sleep(.4)
t("drive above the limit is turned down",any(k==b"C" and json.loads(d).get("set",{}).get("drive")==-8 for k,d in au.got))
t("tx audio locked",req(A,"/api/cmd",{"a":"audio","set":{"comp":9}},who="n")[0]==409); t("rx audio not",req(A,"/api/cmd",{"a":"audio","set":{"nr":9}},who="n")[0]==200); t("tone locked",req(A,"/api/cmd",{"a":"audio","act":"tone"},who="n")[0]==409); t("repeaters locked",req(A,"/api/repeaters",{"op":"clear"},who="n")[0]==403); t("admin may",req(A,"/api/cmd",{"a":"audio","set":{"comp":9}},who="adm")[0]==200)
# nonsense
for x in ({"cfg":{"bands":"x","blocks":[1,{"lo":"a"}],"swr":5,"tx_timeout":"9","tx":"no","max_drive":99,"answer_s":None}},{"cfg":[]},{"cfg":None}): t("nonsense settings change nothing bad",req(A,"/api/admin",{"op":"save",**x},who="adm")[0]==200 and len(req(A,"/api/admin",who="adm")[1]["cfg"]["bands"])==13)
for raw in ('[]','{"on"','null','"x"'): t("bad body "+raw,req(A,"/api/ptt",raw=raw)[0]==400)
os.remove("passwords.json"); time.sleep(.1); t("deleting passwords.json signs everyone out, keeps the locks",req(A,"/api/admin",who="adm")[1]["role"]=="" and st(A)["access"]["admin_set"] is False and "80" in st(A)["access"]["bands"])
print(f"{ok} passed, {bad} failed")
