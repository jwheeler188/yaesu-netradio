# Changelog

## 0.1.4 (yaesu-netradio, W5PL, 2026-11-27)

Not from xara's author: x0rro's repository was discontinued and emptied on 2026-11-08. Kept with yaesu-netradio
(github.com/jwheeler188/yaesu-netradio, `third_party/xara`); the same change as a patch for 0.1.3 is
`third_party/xara-0.1.4-fm-busy.patch` there.

- bugfix: FM busy-channel detection no longer takes an open squelch's hiss for a station. The HF single-bin carrier
  test assumed flat noise; a real FM receiver's hiss is tilted 15-20 dB across the passband, so on an FT-897D xara
  held BUSY on 97 % of the time with nobody there and Pat waited for a clear channel for ever. On FM, xara now learns
  the receiver's hiss bin by bin and calls the channel busy when the last 0.34 s differs from it by more than 2 dB
  across the band. Measured on that radio: hiss alone 0 % busy; VARA FM narrow and wide busy within 0.12-0.25 s, even
  15 dB under the hiss. HF is unchanged. Test: `fm_tilted_hiss_is_not_busy_but_vara_fm_in_it_is`.
- registered speeds are on by default: the `registered` feature is in the default features and turns them on at start;
  the new `--unregistered` flag keeps a run to the unregistered speeds. The same idea as github.com/anelson/xara's 2026-11-20 change. Not in the FM busy patch.

## 0.1.3

- macOS / CoreAudio support thanks to NR5P's work in x0rro/xara#19
- FreeBSD support thanks to dcasati's work in x0rro/xara#29
- experimental (tr)uSDX support, thanks to VA3SHC's work in x0rro/xara#12
- revamped TX latency model

## 0.1.2

- `--log` produces timestamped log files
- new audio resampling and buffering path
- bugfix: call signs with SSID are encoded properly

## 0.1.1

recording feature and bugfixes

- `--record` flag captures rx, tx, and PTT in FLAC
- bugfix: long squelch tails no longer trigger false receive
- bugfix: phantom frames no longer send inappropriate compression

## 0.1.0

first release

- modem is fully compatible with VARA HF 4.9.0 and VARA FM 4.4.0
- web UI for configuration and management
- all the usual TCP ports (works with your favorite software)
- pipewire, pulse, and alsa support

