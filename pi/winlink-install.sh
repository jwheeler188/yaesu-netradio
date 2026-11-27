#!/usr/bin/env bash
# W5PL FT-897D network radio - set up Winlink on the Pi: xara (VARA HF modem) and Pat (mail).
#   pi/winlink-install.sh       builds the copy of xara in third_party/xara (no internet needed for that part)
#   A newer xara source archive (xara*.zip, xara*.tar.gz or xara*.tgz) or Pat's own package (pat_*_linux_arm64.deb from
#   github.com/la5nta/pat/releases) put in winlink/ next to pi/ is used instead. ./install.sh asks whether to run this.
#   Steps already done are skipped, so it is safe to run again.
#   Builds xara with Rust (1.85 or newer; offers to install it with rustup if missing), installs it as ~/.local/bin/xara,
#   adds the loopback sound card (snd-aloop) at every boot, and installs Pat.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DROP="$(cd "$HERE/.." && pwd)/winlink"; BUNDLED="$(cd "$HERE/.." && pwd)/third_party/xara"
say() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mWinlink setup stopped:\033[0m %s\n' "$*"; exit 1; }
need_pkgs=(); for p in libasound2-dev build-essential pkg-config; do dpkg -s "$p" >/dev/null 2>&1 || need_pkgs+=("$p"); done

SRC=$(ls -t "$DROP"/xara*.zip "$DROP"/xara*.tar.gz "$DROP"/xara*.tgz 2>/dev/null | head -1 || true)
PATDEB=$(ls -t "$DROP"/pat_*_linux_arm64.deb "$DROP"/pat_*_linux_*.deb 2>/dev/null | head -1 || true)
[[ -n "$SRC" || ! -f "$BUNDLED/crates/xara/Cargo.toml" ]] || SRC="$BUNDLED"
[[ -n "$SRC" || -n "$PATDEB" ]] || die "no xara source: neither $BUNDLED nor an archive in $DROP"

# ---- packages for building, and Pat
UPD=""; upd() { [[ -n "$UPD" ]] || { sudo apt-get update -q; UPD=1; }; }
if (( ${#need_pkgs[@]} )); then say "Installing ${need_pkgs[*]}"; upd; sudo apt-get install -y -q "${need_pkgs[@]}"; fi
if [[ -n "$PATDEB" ]]; then
  v=$(dpkg-deb -f "$PATDEB" Version)
  if [[ "$(dpkg-query -W -f='${Version}' pat 2>/dev/null)" == "$v" ]]; then say "Pat $v is installed already"
  else say "Installing Pat $v from $(basename "$PATDEB")"; sudo apt-get install -y -q "$PATDEB"; fi
elif ! command -v pat >/dev/null && ! command -v pat-winlink >/dev/null; then
  say "Installing Pat from Ubuntu/Debian (an older version without a settings page: Pat's own .deb in winlink/ is better)"
  upd; sudo apt-get install -y -q pat
fi

# ---- the loopback sound card that joins xara and the bridge (index 10 keeps the other cards' numbers)
if [[ ! -f /etc/modprobe.d/snd-aloop.conf || ! -f /etc/modules-load.d/snd-aloop.conf ]]; then
  say "Adding the loopback sound card (snd-aloop) at every boot"
  echo "options snd-aloop index=10 id=Loopback pcm_substreams=2" | sudo tee /etc/modprobe.d/snd-aloop.conf >/dev/null
  echo "snd-aloop" | sudo tee /etc/modules-load.d/snd-aloop.conf >/dev/null
fi
[[ -e /proc/asound/Loopback ]] || sudo modprobe snd-aloop
[[ -n "$SRC" ]] || { say "Winlink: Pat and the loopback card are ready; no xara source in $DROP"; exit 0; }

# ---- Rust
CARGO=""
for c in "$HOME/.cargo/bin/cargo" "$(command -v cargo || true)"; do
  [[ -x "$c" ]] || continue
  v=$("$c" --version | awk '{print $2}'); IFS=. read -r ma mi _ <<<"$v"
  if (( ma > 1 || mi >= 85 )); then CARGO="$c"; break; fi
done
if [[ -z "$CARGO" ]]; then
  echo "xara needs Rust 1.85 or newer, which isn't here. rustup can install it in $HOME/.cargo (about 250 MB, no sudo),"
  echo "downloaded from static.rust-lang.org and checked against its published checksum."
  read -r -p "Install Rust now? [y/N] " a; [[ "$a" == [yY]* ]] || die "no Rust: install it from https://rustup.rs and run this again"
  arch=$(uname -m); case "$arch" in aarch64|arm64) t=aarch64-unknown-linux-gnu;; armv7l) t=armv7-unknown-linux-gnueabihf;; x86_64) t=x86_64-unknown-linux-gnu;; *) die "unknown machine $arch";; esac
  tmp=$(mktemp -d); u="https://static.rust-lang.org/rustup/dist/$t/rustup-init"
  curl -sSfL -o "$tmp/rustup-init" "$u"; curl -sSfL -o "$tmp/rustup-init.sha256" "$u.sha256"
  (cd "$tmp" && echo "$(cut -d' ' -f1 rustup-init.sha256)  rustup-init" | sha256sum -c -) || die "rustup-init checksum does not match"
  chmod +x "$tmp/rustup-init"; "$tmp/rustup-init" -y --profile minimal --default-toolchain stable --no-modify-path; rm -rf "$tmp"
  CARGO="$HOME/.cargo/bin/cargo"
fi
say "Rust: $("$CARGO" --version)"

# ---- unpack (python's zipfile / tarfile 'data' filter keep every file inside the build folder) and build
B="$HOME/.cache/ft897-xara-build"; rm -rf "$B"; mkdir -p "$B"
if [[ -d "$SRC" ]]; then say "Copying the bundled xara source ($SRC)"; cp -R "$SRC" "$B/xara"
else say "Unpacking $(basename "$SRC")"
python3 - "$SRC" "$B" <<'PY'
import sys, tarfile, zipfile
src, dst = sys.argv[1], sys.argv[2]
if src.endswith(".zip"): zipfile.ZipFile(src).extractall(dst)          # (drops absolute paths and "..")
else:
    with tarfile.open(src) as t:
        try: t.extractall(dst, filter="data")
        except TypeError:                                                  # an older Python: check each entry ourselves
            import os
            base = os.path.realpath(dst)
            for m in t.getmembers():
                p = os.path.realpath(os.path.join(dst, m.name))
                if not (p == base or p.startswith(base + os.sep)) or m.issym() or m.islnk() or m.isdev(): sys.exit(f"unsafe entry in the archive: {m.name}")
            t.extractall(dst)
PY
fi
ROOT=$(dirname "$(find "$B" -maxdepth 4 -path '*/crates/xara/Cargo.toml' | head -1)" 2>/dev/null)
[[ -n "$ROOT" && "$ROOT" != "." ]] || die "no xara source in $(basename "$SRC") (expected crates/xara/Cargo.toml)"
ROOT=$(cd "$ROOT/../.." && pwd)
OFF=(); [[ -d "$ROOT/vendor" && -f "$ROOT/.cargo/config.toml" ]] && OFF=(--offline --frozen)
say "Building xara (a few minutes on a Pi 4)${OFF:+, offline from the bundled crates}"
(cd "$ROOT" && "$CARGO" build --release "${OFF[@]}" -p xara --no-default-features --features alsa)
mkdir -p "$HOME/.local/bin"; install -m 755 "$ROOT/target/release/xara" "$HOME/.local/bin/xara"
rm -rf "$B"
say "Installed $("$HOME/.local/bin/xara" --version) as ~/.local/bin/xara"
say "Winlink is ready: open the panel's Winlink tab (restart the bridge first if it was already running)"
