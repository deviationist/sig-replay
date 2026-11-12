#!/usr/bin/env bash
# Regenerate the README images from the test signature and rewrite the README refs:
#   assets/demo-<hash>.svg   animated replay (--svg)
#   assets/trace-<hash>.png  trace sheet (--debug)
# The content hash in each filename busts GitHub's image cache.
set -euo pipefail
cd "$(dirname "$0")/.."

tmp=$(mktemp -d -t sig-replay-assets)
trap 'rm -rf "$tmp"' EXIT

publish() {  # publish <file> <name> <ext>
  local hash out
  hash=$(shasum -a 256 "$1" | cut -c1-6)
  out="assets/$2-$hash.$3"
  rm -f assets/"$2"-*."$3"
  cp "$1" "$out"
  sed -i.bak -E "s#assets/$2-[0-9a-f]{6}\.$3#$out#" README.md && rm -f README.md.bak
  echo "$out"
}

./sig_replay.py examples/john-doe.png --svg "$tmp/demo.svg" >/dev/null
publish "$tmp/demo.svg" demo svg

./sig_replay.py examples/john-doe.png --debug "$tmp/trace.png" >/dev/null
publish "$tmp/trace.png" trace png
