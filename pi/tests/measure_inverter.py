import sys; import os; _r=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); sys.path[:0]=[_r+"/pi",_r+"/client"]; sys.dont_write_bytecode=True
from proto import *; import warnings; warnings.filterwarnings("ignore")
from ft897_lsb import Invert; from ft897_dsp import Speech, DEFAULTS
fs=48000
def up(f): x,f0=load(f); n=len(x)*3; X=rfft(x); Y=np.zeros(n//2+1,complex); Y[:len(X)]=X; y=irfft(Y,n)[:fs*8]; return y/np.abs(y).max()
def frames_(fn,x): b=x.astype("<i2").tobytes(); return np.frombuffer(b"".join(fn(b[i:i+1920]) for i in range(0,len(b)-1919,1920)),"<i2").astype(float)
def analytic(x): n=len(x); X=np.fft.fft(x); X[n//2+1:]=0; X[1:n//2]*=2; return np.fft.ifft(X)
db=lambda v: 20*np.log10(v/32768+1e-12); inv=lambda x: frames_(Invert().process,x)
# tones
for f in (250,300,500,1000,1500,2500,2700):
    t=np.arange(fs)/fs; y=inv(8000*np.sin(2*np.pi*f*t))[4800:]; S=np.abs(np.fft.rfft(y*np.hanning(len(y)))); q=np.fft.rfftfreq(len(y),1/fs); k=S.argmax(); bad=S.copy(); bad[abs(q-q[k])<40]=0
    print(f,"->",round(q[k]),"level",round(db(np.abs(y).max())-db(8000),2),"dB, worst other",round(20*np.log10(bad.max()/S.max()),1),"dB at",round(q[bad.argmax()]))
for v in ("v1.wav","v3.wav","v5.wav"):
    sp=Speech(dict(DEFAULTS),fs); tx=frames_(sp.process,up(v)*12000); iv=inv(tx); n=len(iv); t=np.arange(n)/fs; tx=tx[:n]
    print(v,"speech out: rms %.1f peak %.1f -> inverted rms %.1f peak %.1f dBFS"%(db(np.sqrt((tx**2).mean())),db(np.abs(tx).max()),db(np.sqrt((iv**2).mean())),db(np.abs(iv).max())), end="  ")
    flt=lambda x: bp(x,fs,300,2700,120); pu=np.abs(analytic(flt(tx))).max(); pi=np.abs(analytic(flt(iv))).max(); print("envelope peak through the radio filter, USB %.1f LSB %.1f dBFS"%(db(pu),db(pi)),end="  ")
    Fc=7200.; rf=np.real(analytic(flt(iv))*np.exp(2j*np.pi*(Fc-3000)*t)); S=np.abs(np.fft.rfft(rf))**2; q=np.fft.rfftfreq(n,1/fs); print("in channel %.3f%% above carrier %.1f dB"%(100*S[(q>Fc-2760)&(q<Fc-240)].sum()/S.sum(),10*np.log10(S[q>Fc].sum()/S.sum()+1e-30)),end="  ")
    A=np.fft.fft(analytic(rf)*np.exp(-2j*np.pi*Fc*t)); ff=np.fft.fftfreq(n,1/fs); A[ff>0]=0; heard=np.conj(np.fft.ifft(A)); B=analytic(flt(tx)); 
    # magnitude spectrum match over time (phase of the filters aside)
    def sg(z): N=2048; return np.array([np.abs(np.fft.fft(z[i:i+N]*np.hanning(N)))[:130] for i in range(0,n-N,N//2)])
    a,b=sg(heard),sg(B); print("heard vs sent %.4f"%((a*b).sum()/np.sqrt((a*a).sum()*(b*b).sum())))
    # receive: their true LSB -> our USB radio at F-3000 -> inverted
    voice=flt(up(v)*6000)[:n]; lsb=np.real(np.conj(analytic(voice))*np.exp(2j*np.pi*Fc*t)); ours=inv(flt(np.real(analytic(lsb)*np.exp(-2j*np.pi*(Fc-3000)*t)))); a,b=sg(analytic(ours)),sg(analytic(voice)); print("   receive match %.4f  above 3.2 kHz %.1f dB"%((a*b).sum()/np.sqrt((a*a).sum()*(b*b).sum()),10*np.log10((np.abs(np.fft.rfft(ours))**2)[np.fft.rfftfreq(len(ours),1/fs)>3200].sum()/(np.abs(np.fft.rfft(ours))**2).sum())))
