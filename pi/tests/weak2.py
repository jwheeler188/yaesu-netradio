from proto import *
import sys,multiprocessing as mp,warnings; warnings.filterwarnings("ignore")
FT=np.arange(0,4000,DF); TT=np.interp(FT,[0,150,250,400,600,900,1300,1800,2300,2700,2850,3100],[-40,-40,-8,0,0,-5,-9,-11,-13,-16,-35,-40])
K=int(round(220/DF))|1; KER=np.hanning(K+2)[1:-1]; KER/=KER.sum(); TTm=np.array([np.clip(np.interp(fs_-d,FT,TT,left=-40),-25,0) for d in D])
def analyse(x,qmin=.25):
    n=(len(x)-N)//H+1; X=np.lib.stride_tricks.sliding_window_view(x,N)[::H]*win; P=np.abs(np.fft.rfft(X,NF,axis=1)[:,sel])**2
    nb=np.percentile(P,20,axis=0); k2=int(150/DF)//1; nb=np.convolve(np.pad(nb,k2,mode='edge'),np.ones(2*k2+1)/(2*k2+1),'valid'); fl=np.sqrt(nb)*1.6
    A=np.sqrt(P); from scipy.signal import fftconvolve
    sm=fftconvolve(np.pad(A,((0,0),(K//2,K//2)),mode='edge'),KER[None,:],mode='valid',axes=1)
    W=(A-sm)/(sm+fl)*np.clip(sm/fl-.6,0,1); C=W@E; nn=np.sqrt((W**2).sum(1)*W.shape[1])+1e-9; R=np.abs(C)/nn[:,None]; j=R.argmax(1); q=R[np.arange(len(R)),j]
    ok=(q>qmin)&(j>2)&(j<R.shape[1]-3); tot=P.sum(1); a=tot>2*nb.sum(); act=P[a].mean(0) if a.any() else P.mean(0)
    return q[ok],taus[j[ok]],np.angle(C[np.arange(len(R)),j])[ok],act,nb,len(P)
def score(x,alpha=.4,qmin=.25,p=2,q0=0.,dial=None,pr=0.):
    q,t,ph,act,nb,n=analyse(x,qmin)
    if len(q)<6: return None
    w=(q-q0)**p; L=(w[:,None]*np.cos(ph[:,None]+2*np.pi*t[:,None]*D[None,:])).sum(0)/w.sum()
    o=10*np.log10(np.maximum(act/(nb+1e-12)-1,.03)); pas=nb>nb.max()*.03; o=np.clip(o-o.max(),-25,0); a=o[pas]-o[pas].mean(); B=TTm[:,pas]; B=B-B.mean(1,keepdims=True); env=(B@a)/np.sqrt((B**2).sum(1)*(a@a)+1e-9)
    lp=-np.abs(D)/250
    if dial is not None:
        fr=(dial+D)%1000; fr=np.minimum(fr,1000-fr); lp=lp+1.2*(fr<=25)+.5*(np.abs(fr-500)<=25)
    tot=L+alpha*env+pr/len(q)*lp; i=int(tot.argmax()); oth=np.max(np.where(np.abs(D-D[i])>45,tot,-9)); f=1/t
    return dict(d=float(D[i]),fit=float(L[i]),margin=float(tot[i]-oth),env=float(env[i]),nv=len(q),n=n,spread=float(f.std()/f.mean()))
CF=((.25,2,0.,0.),(.25,2,0.,2.),(.22,2,.15,0.),(.22,2,.15,2.))
def case(a):
    f,d,snr,dur,seed,kind=a; rng=np.random.default_rng(seed); dial=0
    if kind=="v":
        x,fs=load(f); x=np.tile(x,4); st=rng.integers(0,len(x)-fs*dur-1); y=onair(x[st:st+fs*dur],fs,d,snr,rng)
        Fc=14250000 if rng.random()<.65 else 14250000+int(rng.integers(-50,50))*10; dial=Fc-d
    else:
        g=dict(__import__("builtins").__dict__); ns={"rng":rng,"np":np,"bp":bp,"fs":8000,"n":dur*8000,"t":np.arange(dur*8000)/8000}; src=open("neg.py").read(); exec(src[src.index("def noise("):src.index("for nm,g in")].replace("n=8*fs; t=np.arange(n)/fs",""),ns)
        sig={"noise":lambda:0,"cw":lambda:ns["cw"](rng.uniform(400,1500),rng.uniform(12,30)),"ft8":ns["ft8"],"rtty":ns["rtty"],"car":lambda:sum(ns["carrier"]() for _ in range(3)),"pulse":ns["pulse"]}[f](); y=np.zeros(dur*8000)+sig+ns["noise"](dur*8000)*snr; dial=14250000+int(rng.integers(-50,50))*10; y=y*.1
    out=[(dd,{k:score(y[:dd*8000],qmin=k[0],p=k[1],q0=k[2],dial=dial,pr=k[3]) for k in CF}) for dd in (4,8,16,24)]
    return (f,d,snr,out,kind)
if __name__=="__main__":
    J=[(f,d,s,24,hash((f,d,s))%10**6,"v") for f in sorted(glob.glob("v*.wav")) for s in (10,3,0,-3) for d in (-150,30,250)]+[(k,0,s,24,i*7+1,"n") for k in ("noise","cw","ft8","rtty","car","pulse") for s in (.1,1.) for i in range(5)]
    with mp.Pool(6) as P: R=P.map(case,J,chunksize=2)
    import pickle; pickle.dump(R,open("weak2.pkl","wb")); print("done",len(R))
