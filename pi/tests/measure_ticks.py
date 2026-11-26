import sys,time,numpy as np; import os; sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); sys.dont_write_bytecode=True
from ft897_tap import TickClean
fs=48000
def run(x): c=TickClean(); b=x.astype("<i2").tobytes(); o=b"".join(c.process(b[i:i+1920]) for i in range(0,len(b)-1919,1920)); return np.frombuffer(o,"<i2").astype(float),c
def lowrate(x): return x[:len(x)//6*6].reshape(-1,6).mean(1)
def hp(x,fc): X=np.fft.rfft(x); f=np.fft.rfftfreq(len(x),1/fs); X[f<fc]=0; return np.fft.irfft(X,len(x))
def ticks(x): h=hp(x,9000); e=np.sqrt(np.convolve(h**2,np.ones(24)/24,'same')); fl=max(np.median(e),.5); k=np.where(e>6*fl+6)[0]; return len(np.split(k,np.where(np.diff(k)>480)[0]+1)) if len(k) else 0
x=np.fromfile("tick1.raw","<i2").astype(float); y,c=run(x); 
print("real FM recording: tick bursts",ticks(x),"->",ticks(y),"| bridged pulses",c.count)
for nm,v in (("raw",lowrate(x)),("clean",lowrate(y))):
    fr=v[:len(v)//160*160].reshape(-1,160); r=np.sqrt((fr**2).mean(1)); print("  8 kHz ",nm,"20 ms frames rms: median %.1f  99%% %.1f  max %.1f"%(np.median(r),np.percentile(r,99),r.max()))
t=time.perf_counter(); run(x); print("CPU per 20 ms frame on this Mac: %.2f ms"%((time.perf_counter()-t)/(len(x)/960)*1000))
# does it hurt a voice / music / FM noise? clean speech with and without synthetic ticks
import wave
f=wave.open("../vt/v3.wav"); s=np.frombuffer(f.readframes(f.getnframes()),"<i2").astype(float); n=len(s)*3; X=np.fft.rfft(s); Y=np.zeros(n//2+1,complex); Y[:len(X)]=X; v=np.fft.irfft(Y,n)[:fs*8]; v=v/np.abs(v).max()*9000
def bp(x): X=np.fft.rfft(x); q=np.fft.rfftfreq(len(x),1/fs); X*=(q>300)&(q<3000); return np.fft.irfft(X,len(x))
v=bp(v); rng=np.random.default_rng(1)
for lvl in (9000,3000,300):
    z=v/9000*lvl; out,c=run(z); d=96; a,b=z[:len(out)-d],out[d:]; n_=min(len(a),len(b)); err=b[:n_]-a[:n_]; print("clean speech peak %d: changed %d places, error %.1f dB below speech"%(lvl,c.count,10*np.log10((a[:n_]**2).mean()/max(1e-9,(err**2).mean()))))
    tk=np.zeros(len(z)); pul=np.r_[np.hanning(38)*390*np.where(np.arange(38)%2,1,-1)]
    for c0 in np.arange(.6,7.5,.51):
        for j in range(5): i=int((c0+j*.0052)*fs); tk[i:i+38]+=pul
    o2,c2=run(z+tk); u=o2[d:][:n_]; e2=u-z[:n_]; e0=(z+tk)[:n_]-z[:n_]; print("   with ticks: tick energy error %.1f -> %.1f dB below the speech, bursts left %d of %d"%(10*np.log10((z[:n_]**2).mean()/(e0**2).mean()),10*np.log10((z[:n_]**2).mean()/(e2**2).mean()),ticks(u),ticks(z+tk)))
nse=bp(rng.standard_normal(fs*4)*2000)*1; o,c=run(nse); print("wide noise: changed",c.count); w=rng.standard_normal(fs*4)*300; o,c=run(w); print("white noise (FM hiss like): changed",c.count)
