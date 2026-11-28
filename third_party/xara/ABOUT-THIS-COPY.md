# About this copy of xara

xara is a VARA-compatible HF/FM modem by **x0rro**, released under the MIT licence (see `LICENSE`). Its original
repository, github.com/x0rro/xara, was discontinued and emptied by its author on 2026-11-08. This is a copy
of release **v0.1.3** (commit 09a9201381b00499ada9d9c816bd05bb8f175705) with two changes, released here as
**0.1.4** (see `CHANGELOG.md`), kept here so yaesu-netradio's Winlink on the Pi
can be built. Thanks to x0rro for writing it.

Differences from that release:
- `vectors/` (test data, 3.9 MB) is left out: only xara's own tests use it, not the build.
- `vendor/` and `.cargo/config.toml` are added: the 13 Rust libraries xara builds with (as `cargo vendor` saves them),
  so it builds with no internet. Each keeps its own licence files; all are MIT, Apache-2.0, zlib or 0BSD.
- **Change 1 to xara's code, by yaesu-netradio (2026-11-27), version 0.1.4: the FM busy-channel detector**
  (`crates/xara-phy/src/busy.rs`, test `fm_tilted_hiss_is_not_busy_but_vara_fm_in_it_is` in
  `crates/xara-phy/tests/busy.rs`). On FM, xara used its HF single-bin "carrier" test. A real FM receiver's hiss with the
  squelch open is tilted by 15-20 dB across the passband, and that test took the tilt for a carrier: on an FT-897D it held
  BUSY on 97 % of the time with nobody there, so Pat waited for a clear channel for ever. On FM it now learns the
  receiver's hiss bin by bin and calls the channel busy when the last 0.34 s differs from it by more than 2 dB across the
  band (a station quietens the hiss and puts its own shape in its place). On a recording of that FT-897D: hiss alone 0 %
  busy; xara's VARA FM, narrow and wide, busy within 0.12-0.25 s, even 15 dB under the hiss. HF is unchanged.

- **Change 2, version 0.1.4: registered speeds on by default.** The `registered` build feature (in x0rro's code, off
  by default) is now among the default features and switches the higher speed levels on at start; the new
  `--unregistered` flag keeps a run to the lower ones. The same idea as github.com/anelson/xara (2026-11-20). See
  `CHANGELOG.md`, `README.md` (registered mode) and the yaesu-netradio manual. Not part of the FM busy patch.

`pi/winlink-install.sh` builds it (`cargo build --release --offline -p xara --no-default-features --features alsa`).
Problems with this copy belong to yaesu-netradio, not to xara's author.
