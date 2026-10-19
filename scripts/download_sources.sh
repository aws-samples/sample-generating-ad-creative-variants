#!/usr/bin/env bash
# Download the four free Mixkit source clips and trim each to a 9-second input,
# matching the clips used in the blog. Requires ffmpeg.
#
# Usage: ./scripts/download_sources.sh [aurora_dir]
#   aurora_dir defaults to ./Aurora ; clips land in <aurora_dir>/samples/raw/ad/
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AURORA_DIR="${1:-$REPO_ROOT/Aurora}"
OUT="$AURORA_DIR/samples/raw/ad"
mkdir -p "$OUT"

if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg is required (brew install ffmpeg / apt-get install ffmpeg)." >&2
  exit 1
fi

# Mixkit clip id  ->  output base name   (Mixkit Free License, commercial use OK)
declare -a CLIPS=(
  "805:fashion_dress_805"
  "44546:auto_camaro_44546"
  "40766:sportswear_jogger_40766"
  "4530:fragrance_lavender_4530"
)

for entry in "${CLIPS[@]}"; do
  id="${entry%%:*}"; name="${entry##*:}"
  url="https://assets.mixkit.co/videos/${id}/${id}-1080.mp4"
  raw="$OUT/${name}_1080.mp4"
  nine="$OUT/${name}_9s.mp4"
  echo ">> $id: downloading $url"
  curl -fSL "$url" -o "$raw"
  echo ">> $id: trimming first 9s -> $nine"
  ffmpeg -y -loglevel error -i "$raw" -t 9 -c copy "$nine" \
    || ffmpeg -y -loglevel error -i "$raw" -t 9 "$nine"   # re-encode fallback
done

echo ">> Done. 9-second source clips in $OUT"
