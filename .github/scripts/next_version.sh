#!/usr/bin/env bash
# Prints the next release tag (vX.Y.Z). Reads existing tags from stdin, one per line.
# Usage: next_version.sh major|minor|patch   (tags not in vX.Y.Z format are ignored)
set -euo pipefail

bump="${1:-patch}"

latest="$(tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' \
  | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
  | sed 's/^v//' \
  | sort -t. -k1,1n -k2,2n -k3,3n \
  | tail -n1 || true)"

IFS=. read -r major minor patch <<< "${latest:-0.0.0}"

case "$bump" in
  major) major=$((major + 1)); minor=0; patch=0 ;;
  minor) minor=$((minor + 1)); patch=0 ;;
  patch) patch=$((patch + 1)) ;;
  *) echo "Unknown bump type: $bump (use major, minor or patch)" >&2; exit 1 ;;
esac

echo "v${major}.${minor}.${patch}"
