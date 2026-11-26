import numpy as np, wave, glob, sys
from numpy.fft import rfft, irfft, fft, ifft
def load(f):
    w=wave.open(f); x=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(float); return x,w.getframerate()
def bp(x,fs,lo,hi,tr=60):
    X=rfft(x); f=np.fft.rfftfreq(len(x),1/fs); H=1/(1+np.exp(-(f-lo)/(tr/4)))*1/(1+np.exp((f-hi)/(tr/4))); return irfft(X*H,len(x))
def shift(x,fs,d):
    X=fft(x); n=len(x); X[n//2+1:]=0; X[1:n//2]*=2; a=ifft(X); return np.real(a*np.exp(2j*np.pi*d*np.arange(n)/fs))
def onair(x,fs,d,snr,rng,txlo=250,txhi=2750,rxlo=250,rxhi=2750):
    s=bp(x,fs,txlo,txhi); y=shift(s,fs,d)         # Δ=d: pitch too high by d
    # components shifted below 0 fold: remove by analytic treatment
    X=fft(s); n=len(s); X[n//2+1:]=0; X[1:n//2]*=2; a=ifft(X)*np.exp(2j*np.pi*d*np.arange(n)/fs); A=fft(a); A[n//2:]=0; y=np.real(ifft(A))
    y=bp(y,fs,rxlo,rxhi); p=np.mean(s**2); nz=bp(rng.standard_normal(n),fs,rxlo,rxhi); nz*=np.sqrt(p/10**(snr/10)/np.mean(nz**2)); 
    z=y+nz; Z=rfft(z); m=n//2; return irfft(Z[:m//2+1],m)*.5  # to 8 kHz
N=1024;H=256;NF=4096
win=np.hanning(N); DF=8000/NF
LO,HI=200,2900
taus=np.arange(1/400,1/70,1/8000/2)   # s
fb=np.arange(NF//2+1)*DF; sel=(fb>=LO)&(fb<=HI); fs_=fb[sel]
E=np.exp(-2j*np.pi*np.outer(fs_,taus))     # F x T
D=np.arange(-1500,1501,5.0)
def frames(x):
    out=[]
    for i in range(0,len(x)-N+1,H): out.append(np.abs(rfft(x[i:i+N]*win,NF)))
    return np.array(out)
def est(x,detail=False):
    M=frames(x); P=M**2
    e=P[:,sel].sum(1); 
    noise=np.percentile(P[:,sel],15,axis=0)         # per-bin noise floor estimate
    L=np.zeros(len(D)); tot=0; nv=0
    k=int(round(220/DF))|1; ker=np.hanning(k+2)[1:-1]; ker/=ker.sum()
    for p in P[:,sel]:
        a=np.sqrt(p)                        # amplitude
        sm=np.convolve(np.pad(a,k//2,mode='edge'),ker,'valid')
        fl=np.sqrt(noise.mean())*1.0
        w=(a-sm)/(sm+fl)                    # whitened
        w=w*np.clip(sm/fl-1,0,1)
        C=E.T@w; nn=np.sqrt((w**2).sum()*len(w))+1e-9
        R=np.abs(C)/nn
        j=np.argmax(R[:]); q=R[j]
        if q<.28: continue
        nv+=1
        # use neighbourhood of peak only
        wgt=q**2
        L+=wgt*np.real(C[j]/np.abs(C[j])*np.exp(2j*np.pi*D*taus[j])); tot+=wgt
    return L/max(tot,1e-9),nv,len(P),M
if __name__=="__main__":
    rng=np.random.default_rng(1)
    for f in sorted(glob.glob("v*.wav"))[:12]:
        x,fs=load(f)
        for d in (0,-180,330):
            y=onair(x[fs*2:fs*8],fs,d,15,rng); L,nv,n,M=est(y)
            o=np.argsort(L)[::-1]; pk=[int(D[i]) for i in range(1,len(D)-1) if L[i]>L[i-1] and L[i]>=L[i+1] and L[i]>.35*L.max()]; pk=sorted(pk,key=lambda v:-L[int((v+1500)/5)])[:5]
            print(f,d,"voiced",nv,"/",n,"peaks",[(v,round(L[int((v+1500)/5)],2)) for v in pk])
