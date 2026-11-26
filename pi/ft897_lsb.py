"""W5PL FT-897D network radio - software LSB (used by ft897-netbridge.py).

The FT-897 takes rear-jack audio only in DIG, and DIG's sideband is a radio menu (normally USER-U = upper). Real LSB on a carrier F is
made from it by setting the dial to F - 3000 Hz and turning the audio spectrum over, f -> 3000 - f: on the air that is
F - 3000 + (3000 - f) = F - f, an ordinary lower sideband. Receive the same way back. Multiplying by a 3000 Hz cosine gives 3000 - f and
3000 + f; the low-pass (elliptic, 8th order, flat to 2.75 kHz, -60 dB from 3.2 kHz) removes the second. 48 kHz only.
"""
import array, math

PIVOT = 3000
SOS = [[0.00142070632523, -0.000608251329997, 0.00142070632523, -1.73337603428, 0.760352658307],
       [1.0, -1.6469459733, 1.0, -1.77798253937, 0.85084886854],
       [1.0, -1.79157847219, 1.0, -1.82047298568, 0.933972465641],
       [1.0, -1.82420076649, 1.0, -1.84992518728, 0.982424143225]]
COS = [2 * math.cos(2 * math.pi * i / 16) for i in range(16)]        # 48000 / 3000 = 16

class Invert:
    def __init__(self, gain=1.0): self.gain = gain; self.reset()
    def reset(self): self.z = [[*s, 0.0, 0.0] for s in SOS]; self.n = 0
    def process(self, data):
        """16-bit mono in and out, same length."""
        xs = array.array("h"); xs.frombytes(data); m = len(xs)
        tab = COS[self.n:] + COS[:self.n]; tab *= m // 16 + 1; self.n = (self.n + m) % 16
        ys = [x * c for x, c in zip(xs, tab)]
        for s in self.z:
            b0, b1, b2, a1, a2, z1, z2 = s; out = []; add = out.append
            for x in ys:
                y = b0 * x + z1; z1 = b1 * x - a1 * y + z2; z2 = b2 * x - a2 * y; add(y)
            s[5] = z1 if abs(z1) > 1e-9 else 0.0; s[6] = z2 if abs(z2) > 1e-9 else 0.0; ys = out
        g = self.gain
        return array.array("h", [32767 if v > 32767 else -32768 if v < -32768 else int(v) for v in (y * g for y in ys)]).tobytes()
