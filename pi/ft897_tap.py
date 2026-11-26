"""W5PL FT-897D network radio - takes the CAT ticks out of the audio shown in the panel's waterfall (used by ft897-netbridge.py).

CAT bytes leak into the receive audio (shared Digirig cable): five pulses of about 0.8 ms, 5 ms apart, per command. What the listener hears is
cleaned in the voice client; this does the same, simply, for the picture. A tick sticks out above 8 kHz, where the radio's own audio has
nothing, so the second difference of the signal finds it; the 0.3 to 1.5 ms around it is bridged with a straight line. Only isolated, short
bursts, and only where the sound around them is quiet: longer ones, or anything the radio itself produces, are left alone. Delay 2 ms. 48 kHz, 16-bit mono.
"""
import array

STEP = 12                                  # 0.25 ms
TAIL = 96                                  # 2 ms: a tick at the edge of a frame is finished in the next one

class TickClean:
    def __init__(self): self.buf = array.array("h", [0] * TAIL); self.floor = 6.0; self.count = 0; self.p = (0, 0)
    def process(self, data):
        """Same length in and out, TAIL samples late."""
        x = array.array("h"); x.frombytes(data); n = len(x); b = self.buf; b.extend(x); m = len(b)
        x2 = b.tolist(); d = [0, 0]
        d += [abs(x2[i] - 2 * x2[i - 1] + x2[i - 2]) for i in range(2, m)]
        k = m // STEP; e = [max(d[i * STEP:i * STEP + STEP]) for i in range(k)]; fl = self.floor; hit = []
        for v in e:
            hit.append(v > 8 * fl + 40); fl += (min(v, 3 * fl + 3) - fl) * (0.004 if v > fl else 0.012)
        self.floor = fl; i = 0
        while i < k:
            if not hit[i]: i += 1; continue
            j = i
            while j + 1 < k and (hit[j + 1] or (j + 2 < k and hit[j + 2])): j += 1
            if j - i + 1 <= 6 and j < k - 2 and i > 0:     # at most 1.5 ms, and it ends inside the buffer
                a = max(1, i * STEP - 4); z = min(m - 1, (j + 1) * STEP + 4); near = x2[max(0, a - 240):a]
                if sum(q * q for q in near) / max(1, len(near)) > (0.12 * max(e[i:j + 1]) + 30) ** 2: i = j + 1; continue      # loud around it: hidden anyway
                y0, y1 = b[a - 1], b[z]; s = z - a + 1
                for q in range(a, z): b[q] = int(y0 + (y1 - y0) * (q - a + 1) / s)
                self.count += 1
            elif j >= k - 2 and j - i + 1 <= 6: break      # not finished yet: leave it for the next frame
            i = j + 1
        out = b[:n]; self.buf = b[n:]
        return out.tobytes()
