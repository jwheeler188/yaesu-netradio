#!/bin/bash
# Rebuilds librnnoise.dylib (Apple silicon + Intel) from the RNNoise 0.2 release. Needs Xcode's command line tools.
# The release is missing os_support.h; the small stand-in next to this script replaces it.
set -e
here="$(cd "$(dirname "$0")" && pwd)"; work="$(mktemp -d)"
curl -sSL -o "$work/r.tar.gz" https://github.com/xiph/rnnoise/releases/download/v0.2/rnnoise-0.2.tar.gz
tar xzf "$work/r.tar.gz" -C "$work"; cd "$work/rnnoise-0.2"
S=(src/denoise.c src/rnn.c src/pitch.c src/kiss_fft.c src/celt_lpc.c src/nnet.c src/nnet_default.c
   src/parse_lpcnet_weights.c src/rnnoise_data.c src/rnnoise_tables.c)
for a in arm64 x86_64; do
  clang -O2 -fPIC -shared -arch $a -mmacosx-version-min=10.13 -I"$here" -Iinclude -Isrc -o "$work/lib-$a.dylib" "${S[@]}"
done
lipo -create -output "$here/librnnoise.dylib" "$work/lib-arm64.dylib" "$work/lib-x86_64.dylib"
cp COPYING "$here/COPYING"; rm -rf "$work"; echo "built $here/librnnoise.dylib"
