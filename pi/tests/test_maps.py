src=open("acctest.py").read().split("# ---- 1 other websites")[0]; exec(src)
# Offline maps: estimate, limit, download, serving tiles, resume, cancel, delete. Tiles come from a local stand-in server
# (FT897_TILE_URL=http://127.0.0.1:18099/t/{z}/{y}/{x} in test mode), never from USGS.
import http.server, socketserver
A="127.0.0.2"; hits=[0]
class T(http.server.BaseHTTPRequestHandler):
    def do_GET(s):
        z,y,x=map(int,s.path.split("/")[2:5]); hits[0]+=1
        if (x+y)%17==0: s.send_response(404); s.end_headers(); return          # some tiles don't exist (sea)
        d=f"TILE {z}/{x}/{y}".encode()+bytes(2000); s.send_response(200); s.send_header("Content-Type","image/jpeg"); s.send_header("Content-Length",str(len(d))); s.end_headers(); s.wfile.write(d)
    def log_message(s,*a): pass
socketserver.ThreadingTCPServer.allow_reuse_address=True; srv=socketserver.ThreadingTCPServer(("127.0.0.1",18099),T); threading.Thread(target=srv.serve_forever,daemon=True).start()
def mp(**j): return req(A,"/api/maps",j)
def info(): return req(A,"/api/maps")[1]
def waitfor(fn, sec):
    t0=time.time()
    while time.time()-t0<sec:
        if fn(): return time.time()-t0
        time.sleep(.3)
keep([A])
i=info(); t("sources and empty list",set(i["sources"])=={"usgs-topo","usgs-imagery"} and i["areas"]==[] and i["limit_mb"]==2000,i)
r=mp(op="estimate",source="usgs-topo",lat=39.65,lon=-105.0,radius_km=10,maxz=12,detail_km=0)
t("estimate",r[0]==200 and r[1]["tiles"]>10 and r[1]["mb"]>0 and "free_mb" in r[1],r)
req(A,"/api/admin",{"op":"setup","password":secrets.token_urlsafe(9)},who="adm"); req(A,"/api/admin",{"op":"save","cfg":{"maps_max_mb":50}},who="adm")
r=mp(op="download",source="usgs-topo",lat=39.65,lon=-105.0,radius_km=500,maxz=16,detail_km=0); t("over the admin limit: refused",r[0]==409 and "limit" in r[1].get("error",""),r)
t("nonsense refused",mp(op="estimate",source="nope",lat=1,lon=1,radius_km=5,maxz=12)[0]==409 and mp(op="estimate",source="usgs-topo",lat=99,lon=1,radius_km=5,maxz=12)[0]==409 and mp(op="estimate",source="usgs-topo",lat=1,lon=1,radius_km=5,maxz=20)[0]==409)
e=mp(op="estimate",source="usgs-topo",lat=39.65,lon=-105.0,radius_km=8.05,maxz=12,detail_km=0)[1]
r=mp(op="download",source="usgs-topo",lat=39.65,lon=-105.0,radius_km=8.05,maxz=12,detail_km=0,name="Test area"); w=waitfor(lambda: (info()["job"] or {}).get("state")=="done",60); i=info()
a=i["areas"][0] if i["areas"] else {}
t("download done (miles become km: radius 8.05)",r[0]==200 and w is not None and a.get("name")=="Test area" and abs(a.get("radius_km",0)-8.05)<0.06,(r,i["job"]))
t("all tiles fetched or known missing",i["job"]["done"]==e["tiles"] and a["tiles"]+i["job"]["failed"]==e["tiles"],(i["job"],a.get("tiles"),e["tiles"]))
sys.path.insert(0,"."); import ft897_maps as fm
z=12; x,y=fm.tile_xy(39.65,-105.0,z); inside=(x+y)%17!=0
c=http.client.HTTPConnection("127.0.0.1",W,timeout=5); c.request("GET",f"/tiles/usgs-topo/{z}/{x}/{y}",headers={"Host":"127.0.0.1"}); rr=c.getresponse(); d=rr.read()
t("a tile is served from the Pi",(rr.status==200 and d.startswith(f"TILE {z}/{x}/{y}".encode())) if inside else rr.status==404,(rr.status,d[:20],x,y))
c=http.client.HTTPConnection("127.0.0.1",W,timeout=5); c.request("GET","/tiles/usgs-topo/12/100/100",headers={"Host":"127.0.0.1"}); rr=c.getresponse(); rr.read()
t("outside the area: 404 (the map shows nothing there)",rr.status==404)
c=http.client.HTTPConnection("127.0.0.1",W,timeout=5); c.request("GET",f"/tiles/usgs-imagery/{z}/{x}/{y}",headers={"Host":"127.0.0.1"}); rr=c.getresponse(); rr.read()
t("another map source: not mixed in",rr.status==404)
h0=hits[0]; r=mp(op="download",source="usgs-topo",lat=39.65,lon=-105.0,radius_km=8.05,maxz=12,detail_km=0,name="Test area"); waitfor(lambda: (info()["job"] or {}).get("state")=="done",60); j=info()["job"]
t("Update fetches only what is missing",j["have"]==a["tiles"] and hits[0]-h0==j["failed"],(j,hits[0]-h0))
req(A,"/api/admin",{"op":"save","cfg":{"maps_max_mb":2000}},who="adm")
r=mp(op="download",source="usgs-imagery",lat=39.65,lon=-105.0,radius_km=40,maxz=14,detail_km=0,name="Big"); time.sleep(2); r2=mp(op="download",source="usgs-topo",lat=39.65,lon=-105.0,radius_km=5,maxz=10)
t("one download at a time",r[0]==200 and r2[0]==409,r2)
t("can't delete what is downloading",mp(op="delete",id=r[1]["id"])[0]==409)
mp(op="cancel"); w=waitfor(lambda: info()["job"]["state"]=="cancelled",10); t("cancel",w is not None,info()["job"])
ids=[x["id"] for x in info()["areas"]]; r=mp(op="delete",id=ids[0]); r2=mp(op="delete",id="../passwords")
t("delete; bad names refused",r[0]==200 and len(info()["areas"])==len(ids)-1 and r2[0]==409)
log=open("log_"+os.path.basename(sys.argv[0])[:-3]+".txt").read(); t("no tracebacks",log.count("Traceback")==0)
keep.on=False; srv.shutdown(); print(f"{ok} passed, {bad} failed")
