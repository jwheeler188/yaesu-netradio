from p2 import *
rng=np.random.default_rng(5); fs=8000
def noise(n,lo=250,hi=2750): return bp(rng.standard_normal(n),fs,lo,hi)
def conf(r): return r is not None and r[2]>.08 and r[1]>.55
n=8*fs; t=np.arange(n)/fs
def cw(f,wpm): 
    k=np.zeros(n); dit=int(1.2/wpm*fs); i=0
    while i<n: L=dit*(1 if rng.random()<.5 else 3); k[i:i+L]=1; i+=L+dit*(1 if rng.random()<.7 else 3)
    k=np.convolve(k,np.ones(40)/40,'same'); return k*np.sin(2*np.pi*f*t)
def ft8():
    y=np.zeros(n)
    for _ in range(rng.integers(3,20)):
        f0=rng.uniform(300,2600); s=np.repeat(rng.integers(0,8,400),1280)[:n]; y+=rng.uniform(.1,1)*np.sin(2*np.pi*np.cumsum(f0+6.25*s)/fs)
    return y
def rtty(): s=np.repeat(rng.integers(0,2,3000),176)[:n]; return np.sin(2*np.pi*np.cumsum(1000+170*s)/fs)
def pulse(): # 120 Hz power-line noise
    y=np.zeros(n); y[::67]=rng.standard_normal(len(y[::67]))*1+2; return bp(y,fs,250,2750)*3
def carrier(): return np.sin(2*np.pi*rng.uniform(400,2000)*t)
for nm,g in (("noise",lambda:0),("cw",lambda:cw(rng.uniform(400,1500),rng.uniform(12,30))),("ft8",ft8),("rtty",rtty),("carriers",lambda:sum(carrier() for _ in range(3))),("power-line pulses",pulse)):
    c=a=0
    for _ in range(20):
        y=np.zeros(n)+g()+noise(n)*.1
        r=score(y,.4); a+=r is not None; c+=conf(r)
    print(nm,"answered",a,"of 20, confident",c,flush=True)
