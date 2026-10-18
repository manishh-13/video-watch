#!/usr/bin/env bash
# Offline smoke test: build a 9 s clip with two hard cuts and a tone, digest it,
# and check frames, sheets, cut detection and the report.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
FF="$(uv run --with imageio-ffmpeg python -c 'import imageio_ffmpeg as f; print(f.get_ffmpeg_exe())')"
"$FF" -hide_banner -loglevel error \
  -f lavfi -i "color=red:s=640x360:d=3" -f lavfi -i "color=blue:s=640x360:d=3" \
  -f lavfi -i "testsrc=s=640x360:d=3" -f lavfi -i "sine=frequency=440:duration=9" \
  -filter_complex "[0][1][2]concat=n=3:v=1:a=0[v]" -map "[v]" -map 3 \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$OUT/clip.mp4"
REPORT="$(uv run --script "$HERE/skills/video-watch/scripts/watch.py" "$OUT/clip.mp4" \
  --out "$OUT/digest" --no-transcript 2>/dev/null)"
fail() { echo "FAIL: $1"; cat "$REPORT" 2>/dev/null; exit 1; }
[ -f "$REPORT" ] || fail "no report.md"
grep -q "Duration: 9" "$REPORT" || fail "duration"
grep -q "Scene cuts: 2 " "$REPORT" || fail "expected 2 scene cuts"
grep -q "Audio: yes" "$REPORT" || fail "audio track"
[ "$(ls "$OUT/digest/frames" | wc -l)" -ge 5 ] || fail "too few frames"
[ -f "$OUT/digest/sheets/sheet_01.jpg" ] || fail "no contact sheet"
echo "PASS"
