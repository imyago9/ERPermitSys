#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: scripts/cut_release.sh <version>"
  echo "Example: scripts/cut_release.sh 0.0.3"
}

if [[ $# -ne 1 ]]; then
  usage
  exit 1
fi

VERSION="$1"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must look like x.y.z (example: 0.0.3)"
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree is not clean. Commit or stash your changes first."
  exit 1
fi

TAG="v${VERSION}"
if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "Tag ${TAG} already exists."
  exit 1
fi

sed -i -E "s/^APP_VERSION = \"[^\"]+\"$/APP_VERSION = \"${VERSION}\"/" src/erpermitsys/version.py

git add src/erpermitsys/version.py .github/workflows/release-windows.yml scripts/cut_release.sh
git commit -m "release: ${TAG}"
git tag "$TAG"
git push origin HEAD
git push origin "$TAG"

echo "Released ${TAG}. GitHub Actions is now building and publishing erpermitsys-windows.zip."
