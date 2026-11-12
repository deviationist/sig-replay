#!/usr/bin/env bash
# Regenerate the README animation from the test signature:
#   assets/demo-<hash>.svg, and rewrite the README's <img> ref to it.
# The content hash in the filename busts GitHub's image cache.
set -euo pipefail
cd "$(dirname "$0")/.."

tmp=$(mktemp -t sig-replay-demo).svg
trap 'rm -f "$tmp"' EXIT
./sig_replay.py examples/john-doe.png --svg "$tmp" >/dev/null

hash=$(shasum -a 256 "$tmp" | cut -c1-6)
out="assets/demo-$hash.svg"
rm -f assets/demo-*.svg
cp "$tmp" "$out"
sed -i.bak -E "s#assets/demo-[0-9a-f]{6}\.svg#$out#" README.md && rm -f README.md.bak
echo "$out"
