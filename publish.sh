#!/bin/bash
# publish.sh: push the built dashboards from a cloud run to their GitHub Pages repos.
#
#   bash publish.sh           -> SHADOW: clone the live repos, compare, push nothing
#   bash publish.sh --live    -> copy the built pages in and git push both repos
#
# Needs: this repo's build outputs (index.html, tiktok.html at the repo root,
# meta-performance/Herology_MetaPerformance_*.html) and push access to
# rcc-data/herology-live-creative + rcc-data/herology-meta-ads in the session
# (add_repo with access "push" first). No tokens: the session's git gateway authenticates.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-shadow}"
WORK="${PUBLISH_WORKDIR:-/tmp/herology-publish}"
DATE="$(TZ=America/Vancouver date +%F)"
MP="$(ls -t "$ROOT"/meta-performance/Herology_MetaPerformance_*.html 2>/dev/null | head -1 || true)"

for f in "$ROOT/index.html" "$ROOT/tiktok.html" "$MP"; do
  [ -n "$f" ] && [ -f "$f" ] || { echo "FAIL: missing build output ${f:-Herology_MetaPerformance_*.html}"; exit 1; }
done

rm -rf "$WORK"; mkdir -p "$WORK"
git clone --depth 1 -q https://github.com/rcc-data/herology-live-creative "$WORK/live-creative"
git clone --depth 1 -q https://github.com/rcc-data/herology-meta-ads    "$WORK/meta-ads"

same(){ [ -f "$2" ] && cmp -s "$1" "$2" && echo same || echo DIFFERENT; }
echo "== compare cloud build vs live pages =="
echo "  Creative Hub index.html : $(same "$ROOT/index.html"  "$WORK/live-creative/index.html")"
echo "  Creative Hub tiktok.html: $(same "$ROOT/tiktok.html" "$WORK/live-creative/tiktok.html")"
echo "  Meta Performance        : $(same "$MP"               "$WORK/meta-ads/index.html")"

if [ "$MODE" != "--live" ]; then
  echo "SHADOW MODE: nothing pushed."
  exit 0
fi

push(){ # repo_dir message
  cd "$1"
  if git diff --quiet; then echo "  $(basename "$1"): no change"; cd - >/dev/null; return; fi
  git -c user.name="Claude (Herology nightly)" -c user.email="noreply@anthropic.com" \
      commit -qam "$2"
  git push -q origin HEAD:main
  echo "  $(basename "$1"): SUCCESS $(git rev-parse --short HEAD)"
  cd - >/dev/null
}
cp "$ROOT/index.html" "$ROOT/tiktok.html" "$WORK/live-creative/"
cp "$MP" "$WORK/meta-ads/index.html"
echo "== publish =="
push "$WORK/live-creative" "Refresh creative hub $DATE (cloud build)"
push "$WORK/meta-ads"      "Update dashboard, data to $(basename "$MP" .html | sed 's/Herology_MetaPerformance_//') (cloud build)"
