#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: scripts/cut_release.sh <version> [--run-workflow]"
  echo "Example: scripts/cut_release.sh 0.0.11 --run-workflow"
}

VERSION=""
RUN_WORKFLOW="false"

for arg in "$@"; do
  case "$arg" in
    --run-workflow)
      RUN_WORKFLOW="true"
      ;;
    *)
      if [[ -z "$VERSION" ]]; then
        VERSION="$arg"
      else
        usage
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  usage
  exit 1
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must look like x.y.z (example: 0.0.11)"
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree is not clean. Commit or stash your changes first."
  exit 1
fi

TAG="v${VERSION}"
if git ls-remote --tags origin "refs/tags/$TAG" | grep -q "refs/tags/$TAG"; then
  echo "Tag ${TAG} already exists on origin."
  echo "Bump APP_VERSION before preparing a new release."
  exit 1
fi

sed -i -E "s/^APP_VERSION = \"[^\"]+\"$/APP_VERSION = \"${VERSION}\"/" src/erpermitsys/version.py

git add src/erpermitsys/version.py
git commit -m "release: ${TAG}"
git push origin HEAD

BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if [[ "$RUN_WORKFLOW" == "true" ]]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "GitHub CLI (gh) is required for --run-workflow."
    echo "Install gh or run the workflow manually in GitHub Actions."
    exit 1
  fi
  gh workflow run release-windows.yml --ref "$BRANCH"
  echo "Workflow dispatched: release-windows.yml on branch $BRANCH"
fi

echo "Release commit prepared: ${TAG}"
echo "Next step: run GitHub Action 'Release Windows Build' on branch $BRANCH."
