"""CW decoder on synthetic audio: clean and noisy signals, an off-pitch station, a hand-sender's wide letter gaps, and pure noise
(which must print nothing). Runs anywhere: python3 pi/tests/test_cw_decoder.py"""
import math, os, random, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import ft897_cw as cw
ok = bad = 0
def t(name, c, info=""):
    global ok, bad
    ok += bool(c); bad += not c; print(("pass " if c else "FAIL ") + name + ("" if c else f"   {info}"), flush=True)
def audio(text, wpm, snr_db, off=0, gap=1.0, seed=1):
    tl = cw.Timeline(); at = 1.0; ev = []
    for tok in cw.tokens(cw.clean_text(text)):
        e, nxt = cw.char_events(tok, at, wpm); ev += e
        end = e[-1][0] if e else at; at = end + (nxt - end) * gap         # gap: letter and word spaces stretched alike
    tl.add(ev); g = cw.Tone(48000); x = []; T = 0.0
    while T < at + 1:
        s = struct.unpack("<960h", g.frame(T, 960, tl, 700 + off, 0.3)); x += [sum(s[i:i + 6]) / 6 for i in range(0, 960, 6)]; T += 0.02
    nz = 0.3 * 32767 / math.sqrt(2) / 10 ** (snr_db / 20) * 4; r = random.Random(seed)       # SNR in 500 Hz
    return [v + r.gauss(0, nz) for v in x]
def decode(x, auto=True):
    d = cw.Decoder(700, 20)
    for i in range(0, len(x), 160):
        d.feed(x[i:i + 160])
        if auto and i % 4000 == 0: d.track(700)
    d.feed([0] * 8000); return d.text.strip(), d
T = "CQ CQ DE W5PL W5PL K TNX FER CALL UR RST 599 5NN <AR>"
for wpm, snr, off in ((12, 20, 0), (20, 10, 0), (20, 8, 0), (30, 20, 0), (25, 15, 120), (18, 10, -150)):
    txt, d = decode(audio(T, wpm, snr, off))
    t(f"{wpm} WPM, {snr} dB, {off:+d} Hz: decodes", txt.endswith(T[8:]), txt); t(f"{wpm} WPM: speed found", abs(d.wpm() - wpm) <= 3, d.wpm())
txt, _ = decode(audio("GM OM TNX FER CALL N0CALL DE N0CALL 73", 13, 20, gap=2.5))
t("a hand-sender's wide letter gaps: words apart once learnt", txt.endswith("N0CALL DE N0CALL 73"), txt)
for seed in range(3):
    r = random.Random(seed); txt, d = decode([r.gauss(0, 2000) for _ in range(8000 * 40)])
    t(f"40 s of noise ({seed}): prints nothing, speed stays", txt == "" and d.wpm() == 20, (txt, d.wpm()))
print(f"{ok} passed, {bad} failed")
