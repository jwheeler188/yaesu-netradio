use crate::hf::level::{Bandwidth, Record, Waveform};
use crate::hf::rx::frontend::FrontEnd;
use num_complex::Complex64;
use rustfft::{Fft, FftPlanner};
use std::collections::VecDeque;
use std::sync::Arc;

pub const HANG: u32 = 34;
pub const TONE_PEAK_RATIO: f64 = 270.0;
pub const VU_GATE_DBFS: f64 = -46.0;
pub const SIGNATURE_POWER_GATE: f64 = 1e6;
const ROWS: usize = 20;
const FULL_SCALE: f64 = 32767.0;

pub fn tone_band(bw: Bandwidth) -> (usize, usize, usize) {
    match bw {
        Bandwidth::Hz500 => (54, 74, 4),
        Bandwidth::Hz2300 => (21, 107, 14),
        Bandwidth::Hz2750 => (16, 112, 17),
    }
}

pub fn signature_band(bw: Bandwidth) -> (usize, usize) {
    match bw {
        Bandwidth::Hz500 => (27, 37),
        Bandwidth::Hz2300 => (15, 49),
        Bandwidth::Hz2750 => (10, 54),
    }
}

pub fn signature_threshold(bw: Bandwidth) -> f64 {
    if bw == Bandwidth::Hz500 { 3.1 } else { 2.0 }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Signature {
    pub record: Record,
    pub carriers: [usize; 4],
    pub gaps: [usize; 3],
}

const CENTER_HZ: f64 = 1500.0;

fn bin(hz: f64) -> usize {
    (hz / crate::ofdm::BIN_HZ).round_ties_even() as usize
}

pub fn signature(fc: &[u32]) -> ([usize; 4], [usize; 3]) {
    let fc: Vec<f64> = fc.iter().map(|&f| f64::from(f)).collect();
    let nc = fc.len();
    let (mut car, mut gap) = ([0; 4], [0; 3]);
    if nc % 2 == 0 {
        let h = nc / 2;
        gap[0] = bin(CENTER_HZ);
        car[0] = bin(fc[h - 1]);
        car[1] = bin(fc[h]);
        if nc > 3 {
            car[2] = bin(fc[h - 2]);
            car[3] = bin(fc[h + 1]);
            gap[1] = bin((fc[h - 2] + fc[h - 1]) * 0.5);
            gap[2] = bin((fc[h] + fc[h + 1]) * 0.5);
        }
    } else {
        let c = (nc - 1) / 2;
        gap[0] = bin((fc[c - 1] + CENTER_HZ) * 0.5);
        car[0] = bin(CENTER_HZ);
        car[1] = bin(fc[c - 1]);
        if nc > 2 {
            gap[1] = bin((fc[c + 1] + CENTER_HZ) * 0.5);
            car[2] = bin(fc[c + 1]);
        }
    }
    (car, gap)
}

pub fn signatures(bw: Bandwidth) -> Vec<Signature> {
    Record::all()
        .filter_map(|record| match bw.level(record).waveform {
            Waveform::Psk { carriers_hz, .. } if carriers_hz.len() >= 2 => {
                let (carriers, gaps) = signature(carriers_hz);
                Some(Signature { record, carriers, gaps })
            }
            _ => None,
        })
        .collect()
}

pub fn detect_tone(power: &[f64], bw: Bandwidth) -> bool {
    let (lo, hi, rank) = tone_band(bw);
    let mut v = power[lo..=hi].to_vec();
    v.sort_by(f64::total_cmp);
    v[v.len() - 1] > TONE_PEAK_RATIO * v[rank - 1]
}

pub fn detect_signature(power: &[f64], sig: &Signature, threshold: f64) -> bool {
    let at = |b: usize, sh: i64| power[(b as i64 + sh) as usize];
    let (mut best, mut shift) = (0.0, 0);
    for sh in -1..=1 {
        let tot: f64 = sig.carriers.iter().filter(|&&c| c > 0).map(|&c| at(c, sh)).sum();
        if best <= tot {
            (best, shift) = (tot, sh);
        }
    }
    let max_gap = sig.gaps.iter().filter(|&&g| g > 0).map(|&g| at(g, shift)).fold(0.0, f64::max);
    let min_carrier = sig.carriers.iter().filter(|&&c| c > 0).map(|&c| at(c, shift)).fold(f64::INFINITY, f64::min);
    max_gap > 0.0 && min_carrier / max_gap > threshold
}

pub struct Busy {
    bw: Bandwidth,
    sigs: Vec<Signature>,
    fe: FrontEnd,
    fft128: Arc<dyn Fft<f64>>,
    fft64: Arc<dyn Fft<f64>>,
    block: Vec<Complex64>,
    last64: VecDeque<Complex64>,
    since_signature: usize,
    rows: VecDeque<[f64; 64]>,
    peak: f64,
    vu: f64,
    hang: u32,
    on: bool,
    pub hits: [u32; 2],
    /// FM only (None on HF): each bin's learned receiver hiss, the mean of 10 log10 of its power. An FM receiver's hiss with
    /// the squelch open is far from flat (de-emphasis, the radio's data filter: 15-20 dB of tilt across the passband), and
    /// the single-bin tone test, made for flat HF noise, took that tilt for a carrier and held BUSY on for good. On FM a
    /// station is busy when the last FM_AVG blocks differ from the learned hiss by more than FM_DIST_DB on average across
    /// the band: a station quietens the hiss and puts its own shape in its place. (yaesu-netradio change, 2026-11-27.)
    floor: Option<[f64; 128]>,
    floor_n: u32,
    recent: VecDeque<[f64; 128]>,
    pub fm_dist: f64,
}

/// The FM hiss floor: learned for the first second (FLOOR_LEARN blocks of 128 samples, about 23 a second) before the
/// test is trusted, then followed slowly (FLOOR_ALPHA per block, a step of at most FLOOR_STEP_DB), and 20 times more slowly
/// still while busy, so a signal isn't learned as hiss.
pub const FLOOR_ALPHA: f64 = 0.02;
pub const FLOOR_STEP_DB: f64 = 10.0;
/// E[10 log10 X] for X exponential with mean 1 (a noise bin's power): -2.51 dB.
pub const EXP_LOG_BIAS_DB: f64 = 2.507;
pub const FLOOR_LEARN: u32 = 23;
/// FM: blocks averaged (about 0.34 s) and the mean distance from the hiss, in dB, that means a station is there. Measured
/// on an FT-897D with the squelch open: hiss alone 1.2 dB typical, 1.6 dB at most; xara's VARA FM 2.6-5 dB, even 15 dB
/// under the hiss.
pub const FM_AVG: usize = 8;
pub const FM_DIST_DB: f64 = 2.0;

impl Busy {
    pub fn fm() -> Self {
        Self { floor: Some([f64::NAN; 128]), ..Self::new(Bandwidth::Hz2750) }
    }

    pub fn new(bw: Bandwidth) -> Self {
        let mut p = FftPlanner::new();
        Self {
            bw,
            sigs: signatures(bw),
            fe: FrontEnd::new(),
            fft128: p.plan_fft_forward(128),
            fft64: p.plan_fft_forward(64),
            block: Vec::with_capacity(128),
            last64: VecDeque::with_capacity(64),
            since_signature: 0,
            rows: VecDeque::with_capacity(ROWS),
            peak: 0.0,
            vu: -200.0,
            hang: 0,
            on: false,
            hits: [0; 2],
            floor: None,
            floor_n: 0,
            recent: VecDeque::with_capacity(16),
            fm_dist: 0.0,
        }
    }

    pub fn is_on(&self) -> bool {
        self.on
    }

    pub fn set_bw(&mut self, bw: Bandwidth) {
        if bw != self.bw {
            self.bw = bw;
            self.sigs = signatures(bw);
        }
    }

    pub fn feed(&mut self, x: &[f64], forced: bool) -> Vec<bool> {
        let mut edges = Vec::new();
        if forced {
            self.hang = HANG;
            self.set(true, &mut edges);
        }
        self.peak = x.iter().fold(self.peak, |m, v| m.max(v.abs()));
        let (_, bb) = self.fe.process(x);
        for z in bb {
            let z = z * FULL_SCALE;
            if self.last64.len() == 64 {
                self.last64.pop_front();
            }
            self.last64.push_back(z);
            self.since_signature += 1;
            if self.since_signature == 32 {
                self.since_signature = 0;
                if self.last64.len() == 64 && self.signature_hit() && !forced {
                    self.hits[1] += 1;
                    self.hang = HANG;
                    self.set(true, &mut edges);
                }
            }
            self.block.push(z);
            if self.block.len() == 128 {
                let hit = self.tone_hit();
                self.block.clear();
                if forced {
                    continue;
                }
                if self.hang > 0 {
                    self.hang -= 1;
                    if self.hang == 0 {
                        self.set(false, &mut edges);
                    }
                }
                if hit {
                    self.hits[0] += 1;
                    self.hang = HANG;
                    self.set(true, &mut edges);
                }
            }
        }
        edges
    }

    fn set(&mut self, on: bool, edges: &mut Vec<bool>) {
        if self.on != on {
            self.on = on;
            edges.push(on);
        }
    }

    fn tone_hit(&mut self) -> bool {
        let peak_db = 20.0 * self.peak.max(1e-12).log10();
        self.vu = peak_db.max(self.vu - 1.0);
        self.peak = 0.0;
        if self.vu <= VU_GATE_DBFS {
            return false;
        }
        self.fft128.process(&mut self.block);
        let p: [f64; 128] = std::array::from_fn(|k| self.block[k].norm_sqr());
        let Some(fl) = self.floor.as_mut() else { return detect_tone(&p, self.bw) };
        let (lo, hi, _) = tone_band(self.bw);
        let db: [f64; 128] = std::array::from_fn(|k| 10.0 * p[k].max(1e-30).log10());
        if fl[0].is_nan() {
            *fl = db;
            self.floor_n = 0;
            self.recent.clear();
        }
        self.floor_n = self.floor_n.saturating_add(1);
        if self.recent.len() == FM_AVG {
            self.recent.pop_front();
        }
        self.recent.push_back(p);
        // the last FM_AVG blocks against the learned hiss, bin by bin, in dB; the mean distance over the band
        let n = self.recent.len() as f64;
        let d = (lo..=hi)
            .map(|k| {
                // fl is the mean of 10 log10(power): for noise that is 2.51 dB under 10 log10 of the mean power
                let avg = self.recent.iter().map(|r| r[k]).sum::<f64>() / n;
                (10.0 * avg.max(1e-30).log10() - (fl[k] + EXP_LOG_BIAS_DB)).abs()
            })
            .sum::<f64>()
            / (hi - lo + 1) as f64;
        self.fm_dist = d;
        let hit = self.floor_n > FLOOR_LEARN && d > FM_DIST_DB;
        let a = if self.floor_n <= FLOOR_LEARN { 1.0 / self.floor_n as f64 } else if hit { FLOOR_ALPHA / 20.0 } else { FLOOR_ALPHA };
        for k in 0..128 {
            fl[k] += a * (db[k] - fl[k]).clamp(-FLOOR_STEP_DB, FLOOR_STEP_DB);
        }
        hit
    }

    fn signature_hit(&mut self) -> bool {
        let mut buf: [Complex64; 64] = std::array::from_fn(|i| self.last64[i]);
        self.fft64.process(&mut buf);
        let row: [f64; 64] = std::array::from_fn(|k| buf[k].norm_sqr());
        let (lo, hi) = signature_band(self.bw);
        let mean = row[lo..=hi].iter().sum::<f64>() / (hi - lo + 1) as f64;
        if self.rows.len() == ROWS {
            self.rows.pop_front();
        }
        self.rows.push_back(row);
        if mean < SIGNATURE_POWER_GATE || self.rows.len() < ROWS {
            return false;
        }
        let mut s = [0.0; 64];
        for r in &self.rows {
            for (a, b) in s.iter_mut().zip(r) {
                *a += b;
            }
        }
        let threshold = signature_threshold(self.bw);
        self.sigs.iter().any(|g| detect_signature(&s, g, threshold))
    }
}
