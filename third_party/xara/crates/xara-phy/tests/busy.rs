mod common;
use common::{Rng, bw_of, load_doc};
use xara_phy::busy::{
    Busy, HANG, Signature, detect_signature, detect_tone, signature_threshold, signatures, tone_band,
};
use xara_phy::hf::level::{Bandwidth, Record};
use xara_phy::hf::tx::Modulator;
use xara_testkit::json::Value;

const BLOCK48: usize = 128 * 16;

fn spectrum(v: &Value, n: usize) -> Vec<f64> {
    let mut p = vec![0.0; n];
    for (k, x) in v.as_object().unwrap() {
        p[k.parse::<usize>().unwrap()] = x.as_f64().unwrap();
    }
    p
}

fn bins<const N: usize>(v: &Value) -> [usize; N] {
    std::array::from_fn(|i| v[i].as_u64().unwrap() as usize)
}

fn noise(r: &mut Rng, n: usize, rms: f64) -> Vec<f64> {
    (0..n).map(|_| rms * r.gauss()).collect()
}

fn db(x: f64) -> f64 {
    10f64.powf(x / 20.0)
}

fn tone(n: usize, hz: f64, amp: f64) -> Vec<f64> {
    (0..n).map(|i| amp * (std::f64::consts::TAU * hz * i as f64 / 48000.0).sin()).collect()
}

fn run(b: &mut Busy, x: &[f64]) -> Vec<(usize, bool)> {
    let mut edges = Vec::new();
    for (i, c) in x.chunks(480).enumerate() {
        edges.extend(b.feed(c, false).into_iter().map(|e| (i * 480 + c.len(), e)));
    }
    edges
}

#[test]
fn tone_detector_matches_vector() {
    let v = load_doc("hf/busy");
    for c in v["detector1"]["cases"].as_array().unwrap() {
        let bw = bw_of(&c["bw"]);
        let p = spectrum(&c["spectrum"], 128);
        assert_eq!(tone_band(bw).0, c["bins"][0].as_u64().unwrap() as usize);
        assert_eq!(detect_tone(&p, bw), c["busy"].as_bool().unwrap(), "{c}");
    }
}

#[test]
fn signature_detector_matches_vector() {
    let v = load_doc("hf/busy");
    let d2 = &v["detector2"];
    for bw in Bandwidth::ALL {
        let want: Vec<Signature> = d2["signatures"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|s| bw_of(&s["bw"]) == bw)
            .map(|s| Signature {
                record: Record::new(s["record"].as_u64().unwrap() as usize).unwrap(),
                carriers: bins(&s["carrier_bins"]),
                gaps: bins(&s["gap_bins"]),
            })
            .collect();
        assert_eq!(signatures(bw), want, "{bw:?}");
    }
    for c in d2["cases"].as_array().unwrap() {
        let bw = bw_of(&c["bw"]);
        let sig = *signatures(bw).iter().find(|s| s.record.get() as u64 == c["record"].as_u64().unwrap()).unwrap();
        let s = spectrum(&c["S"], 64);
        assert_eq!(signature_threshold(bw), c["threshold"].as_f64().unwrap());
        assert_eq!(detect_signature(&s, &sig, signature_threshold(bw)), c["busy"].as_bool().unwrap(), "{c}");
    }
}

#[test]
fn silence_and_noise_rarely_trip() {
    for bw in Bandwidth::ALL {
        let mut b = Busy::new(bw);
        assert!(run(&mut b, &vec![0.0; 48000 * 3]).is_empty());
        let mut b = Busy::new(bw);
        let x = noise(&mut Rng::new(7), 48000 * 20, db(-15.0));
        run(&mut b, &x);
        assert!(b.hits[0] == 0 && b.hits[1] <= 3, "{bw:?} {:?}", b.hits);
    }
}

#[test]
fn tone_in_noise_hangs_34_blocks() {
    let mut b = Busy::new(Bandwidth::Hz2300);
    let mut n = Rng::new(3);
    let mut x = noise(&mut n, 48000, db(-30.0));
    let burst: Vec<f64> =
        tone(9600, 1500.0, db(-20.0)).iter().zip(noise(&mut n, 9600, db(-30.0))).map(|(a, b)| a + b).collect();
    x.extend(burst);
    x.extend(noise(&mut n, 48000 * 3, db(-30.0)));
    let edges = run(&mut b, &x);
    assert_eq!(edges.iter().map(|e| e.1).collect::<Vec<_>>(), [true, false], "{edges:?}");
    let off = (edges[1].0 as f64 - 57600.0) / 48000.0;
    let hang = f64::from(HANG) * BLOCK48 as f64 / 48000.0;
    assert!((hang - 0.09..=hang + 0.05).contains(&off), "{off}");
    assert!(edges[0].0 < 48000 + 2 * BLOCK48 + 480);
}

#[test]
fn tone_threshold_is_24_db_over_the_floor() {
    let mut n = Rng::new(11);
    let sigma = db(-30.0);
    for (bin_db, want) in [(8.0, false), (30.0, true)] {
        let mut b = Busy::new(Bandwidth::Hz2300);
        let noise = noise(&mut n, 48000 * 2, sigma);
        let t = tone(noise.len(), 1500.0, sigma * (db(bin_db) * db(bin_db) / 1024.0).sqrt());
        let x: Vec<f64> = noise.iter().zip(t).map(|(a, b)| a + b).collect();
        run(&mut b, &x);
        assert_eq!(b.hits[0] > 0, want, "{bin_db} dB {:?}", b.hits);
    }
}

#[test]
fn quiet_tone_is_gated() {
    let mut b = Busy::new(Bandwidth::Hz2300);
    let mut n = Rng::new(13);
    let floor = |x: Vec<f64>, n: &mut Rng| -> Vec<f64> {
        x.iter().zip(noise(n, x.len(), db(-75.0))).map(|(a, b)| a + b).collect()
    };
    let quiet = floor(tone(48000 * 3, 1500.0, db(-52.0)), &mut n);
    assert!(run(&mut b, &quiet).is_empty(), "{:?}", b.hits);
    let edges = run(&mut b, &floor(tone(48000, 1500.0, db(-40.0)), &mut n));
    assert_eq!(edges.first().map(|e| e.1), Some(true));
}

#[test]
fn xara_psk_trips_signature_detector() {
    let mut rng = Rng::new(1);
    let mut rand = |n: u32| rng.below(n);
    for (bw, record) in [(Bandwidth::Hz2300, 5), (Bandwidth::Hz2300, 8), (Bandwidth::Hz2750, 7), (Bandwidth::Hz500, 4)]
        .map(|(bw, r)| (bw, Record::new(r).unwrap()))
    {
        let m = Modulator::new(bw);
        let len = bw.level(record).frame_bytes() as usize;
        let frame: Vec<u8> = (0..len).map(|i| (i * 37 + 11) as u8).collect();
        let x = m.transmission(record, &[&frame], &mut rand);
        let rms = (x.iter().map(|v| v * v).sum::<f64>() / x.len() as f64).sqrt();
        let mut n = Rng::new(5);
        let sig: Vec<f64> =
            x.iter().map(|v| v / rms * db(-25.0)).zip(noise(&mut n, x.len(), db(-25.0))).map(|(a, b)| a + b).collect();
        let mut b = Busy::new(bw);
        run(&mut b, &noise(&mut n, 48000 * 3, db(-25.0)));
        assert!(!b.is_on() && b.hits == [0, 0]);
        run(&mut b, &sig);
        assert!(b.hits[1] > 0, "{bw:?} L{record} {:?}", b.hits);
        assert!(b.is_on());
    }
}

#[test]
fn forced_holds_and_releases_after_hang() {
    let mut b = Busy::new(Bandwidth::Hz2300);
    assert_eq!(b.feed(&[0.0; 480], true), [true]);
    for _ in 0..100 {
        assert!(b.feed(&[0.0; 480], true).is_empty());
    }
    let edges = run(&mut b, &vec![0.0; 48000 * 2]);
    assert_eq!(edges.len(), 1);
    assert!(!edges[0].1);
    let t = edges[0].0 as f64 / 48000.0;
    assert!((1.40..1.50).contains(&t), "{t}");
}

// yaesu-netradio: an FM receiver's open-squelch hiss is tilted (here 18 dB down across the band, as an FT-897D's data
// port gives it). That alone must not read busy on FM; xara's own VARA FM signal in it, even weaker than the hiss, must.
fn tilted_hiss(n: &mut Rng, len: usize) -> Vec<f64> {
    let w = noise(n, len, db(-20.0));
    let (mut a, mut out) = (0.0, Vec::with_capacity(len));
    for v in w {
        a = 0.93 * a + v; // one-pole low-pass: about -18 dB from 300 Hz to 3 kHz
        out.push(a * 0.25);
    }
    out
}

#[test]
fn fm_tilted_hiss_is_not_busy_but_vara_fm_in_it_is() {
    use xara_phy::fm::level::{FmBandwidth, Record as FmRecord};
    use xara_phy::fm::tx::{Modulator as FmModulator, TxOptions};
    let mut n = Rng::new(21);
    let hiss = tilted_hiss(&mut n, 48000 * 20);
    let mut hf = Busy::new(Bandwidth::Hz2750);
    run(&mut hf, &hiss);
    let mut b = Busy::fm();
    run(&mut b, &hiss);
    assert!(!b.is_on() && b.hits == [0, 0], "hiss alone: {:?} (the HF test on it: {:?})", b.hits, hf.hits);
    let mut r = Rng::new(9);
    let mut rand = |k: u32| r.below(k);
    for bw in [FmBandwidth::Narrow, FmBandwidth::Wide] {
        let m = FmModulator::new(bw);
        let rec = FmRecord::from_index(3).unwrap();
        let frame: Vec<u8> = (0..m.level(rec).frame_bytes() as usize).map(|i| (i * 37 + 11) as u8).collect();
        let sig = m.transmission(rec, 7, &[&frame], 4, TxOptions::default(), &mut rand);
        let rms = |x: &[f64]| (x.iter().map(|v| v * v).sum::<f64>() / x.len() as f64).sqrt();
        let more = tilted_hiss(&mut n, sig.len());
        let (hr, sr) = (rms(&more), rms(&sig));
        // 10 dB under the hiss's level, the hiss quietened 10 dB by the carrier
        let x: Vec<f64> = sig.iter().zip(&more).map(|(s, h)| s / sr * hr * db(-10.0) + h * db(-10.0)).collect();
        let mut b2 = Busy::fm();
        run(&mut b2, &hiss[..48000 * 5]);
        assert!(!b2.is_on(), "{bw:?} before");
        let edges = run(&mut b2, &x);
        assert_eq!(edges.first().map(|e| e.1), Some(true), "{bw:?}: VARA FM not seen as busy");
        assert!(edges[0].0 < 48000 / 2, "{bw:?}: busy only after {} samples", edges[0].0);
    }
}
