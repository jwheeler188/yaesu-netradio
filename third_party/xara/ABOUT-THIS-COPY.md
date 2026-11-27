# About this copy of xara

xara is a VARA-compatible HF/FM modem by **x0rro**, released under the MIT licence (see `LICENSE`). Its original
repository, github.com/x0rro/xara, was discontinued and emptied by its author on 2026-11-08. This is an unmodified copy
of release **v0.1.3** (commit 09a9201381b00499ada9d9c816bd05bb8f175705), kept here so yaesu-netradio's Winlink on the Pi
can be built. Thanks to x0rro for writing it.

Differences from that release:
- `vectors/` (test data, 3.9 MB) is left out: only xara's own tests use it, not the build.
- `vendor/` and `.cargo/config.toml` are added: the 13 Rust libraries xara builds with (as `cargo vendor` saves them),
  so it builds with no internet. Each keeps its own licence files; all are MIT, Apache-2.0, zlib or 0BSD.

`pi/winlink-install.sh` builds it (`cargo build --release --offline -p xara --no-default-features --features alsa`).
Problems with this copy belong to yaesu-netradio, not to xara's author.
