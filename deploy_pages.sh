#!/usr/bin/env bash
# Publish data/web/index.html to GitHub Pages.
#
# The built page is ~13 MB and lives under the gitignored data/, so it is deployed to an
# **orphan gh-pages branch that is replaced on every deploy** rather than committed to master.
# That keeps a large, frequently-regenerated binary blob out of the main history — otherwise
# every rebuild would add another 13 MB to the clone size permanently.
set -euo pipefail

cd "$(dirname "$0")"
PAGE="data/web/index.html"
[ -f "$PAGE" ] || { echo "no $PAGE — run: uv run python build_page.py"; exit 1; }

WT=$(mktemp -d)
trap 'git worktree remove --force "$WT" 2>/dev/null || true; rm -rf "$WT"' EXIT

if git show-ref --verify --quiet refs/heads/gh-pages; then
  git worktree add --force "$WT" gh-pages >/dev/null
else
  git worktree add --force --orphan -b gh-pages "$WT" >/dev/null
fi

rm -f "$WT"/*.html "$WT"/.nojekyll
cp "$PAGE" "$WT/index.html"
touch "$WT/.nojekyll"   # stop Jekyll mangling the file

git -C "$WT" add -A
if git -C "$WT" diff --cached --quiet; then
  echo "no change to publish"
else
  git -C "$WT" commit -q -m "Deploy viewer $(date -u +%Y-%m-%dT%H:%MZ)"
  git -C "$WT" push -q --force origin gh-pages
  echo "pushed gh-pages ($(du -h "$PAGE" | cut -f1))"
fi

echo "https://edcrossing.github.io/bleau-fire/"
