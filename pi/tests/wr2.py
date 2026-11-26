import pickle,numpy as np
R=pickle.load(open("weak2.pkl","rb")); ks=list(R[0][3][0][1].keys())
def mk(c,f0,e,n0=10): return lambda r: r["nv"]>=n0 and r["fit"]>max(.35,f0*np.sqrt(14/max(r["nv"],14))) and r["margin"]>c/np.sqrt(r["nv"]) and r["env"]>e
rules={"old":lambda r: r["margin"]>.1 and r["fit"]>.6 and r["env"]>.3 and r["nv"]>=14,"c.4":mk(.4,.5,.2),"c.5":mk(.5,.5,.2),"c.6":mk(.6,.5,.25,12),"c.8":mk(.8,.55,.25,12)}
for dd in (4,8,16,24):
  for k in ks:
    row=[]
    for nm,ru in rules.items():
        s=[]
        for snr in (10,3,0,-3):
            c=[(r[1],o[k]) for r in R if r[4]=="v" and r[2]==snr for dur,o in r[3] if dur==dd and o[k] and ru(o[k])]; s.append(f"{len(c)}/{sum(abs(d-x['d'])>30 for d,x in c)}w")
        ng=sum(1 for r in R if r[4]=="n" for dur,o in r[3] if dur==dd and o[k] and ru(o[k])); row.append(f"{nm}: {' '.join(s)} neg {ng}/60")
    anyr=[sum(1 for r in R if r[4]=="v" and r[2]==snr for dur,o in r[3] if dur==dd and o[k] and abs(r[1]-o[k]["d"])<=30) for snr in (10,3,0,-3)]
    print(dd,"s",k,"best-guess right of 36:",anyr); [print("     ",x) for x in row]
