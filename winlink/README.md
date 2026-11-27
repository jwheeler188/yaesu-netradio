# Winlink drop folder

Put these here before running `pi/install.sh` (or `pi/winlink-install.sh` on its own) and Winlink on the Pi is set up
for you:

- **xara's source**, as `xara….zip`, `xara….tar.gz` or `xara….tgz`. xara is the VARA HF-compatible modem (MIT licence).
  Its original project closed in November 2026; use a copy of its source. If the archive includes a `vendor/` folder
  (the "offline build" kind), it builds without downloading anything else.
- **Pat** (optional): `pat_<version>_linux_arm64.deb` from <https://github.com/la5nta/pat/releases>. Without it the
  installer uses your system's `pat` package, which on Ubuntu 24.04 is 0.15.1 and has no settings page.

The installer then:
1. installs the build tools (`libasound2-dev build-essential pkg-config`) and Pat,
2. adds the loopback sound card (`snd-aloop`, card 10) at every boot,
3. offers to install Rust with rustup if Rust 1.85 or newer isn't there (about 250 MB in your home folder, no sudo),
4. builds xara (a few minutes on a Pi 4) and installs it as `~/.local/bin/xara`.

Steps already done are skipped, so running it again is safe. Nothing you put in this folder is uploaded: git ignores it.
