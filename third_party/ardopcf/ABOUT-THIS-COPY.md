# About this copy of ardopcf

ardopcf is an open-source implementation of ARDOP (the Amateur Radio Digital Open Protocol) by **Rick Muething KN6KB,
John Wiseman G8BPQ and Peter LaRue AI7YN**, released under the MIT licence (see `LICENSE`; the libraries in `lib/` keep
their own terms, listed there). Its repository is github.com/pflarue/ardop. Peter LaRue announced the end of its
development on 2026-09-01 (see `README.md`).

This is an **unmodified** copy of that repository at its final commit, e27af046c04f973ed8958a219844f655ead6c419: release
**1.0.4.1.3** plus the author's later notes on licensing and the end of development (no program changes). It was
downloaded on 2026-11-27 as the GitHub source archive (2,285,401 bytes, SHA-256
f8000a8b18c0c4973549b7896adc7e664f9d701efb8a28e2835fa41a62dfcd04) and is kept here so yaesu-netradio's Winlink on the Pi
can build it with no internet. Thanks to its authors.

`pi/winlink-install.sh` builds it with `make` and installs it as `~/.local/bin/ardopcf`. The bridge starts it for the
ARDOP link with its host port on this Pi only (`pi/localonly.c`) and no PTT of its own: Pat keys the radio through the
bridge's CAT port, so every transmit lock applies.

As its author asks, anything changed from this code should be released under a new name. Problems with this copy
belong to yaesu-netradio, not to ardopcf's authors.
