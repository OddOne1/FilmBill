#!/usr/bin/env bash
#
# Run the web pipeline the way CI runs it: Linux, node:20, pnpm 9.12.3, and a
# COLD install from a clean `git archive` export.
#
# This exists because CI was intermittently red for weeks while the same suite
# was green on the laptop every single time. The cause was a test that raced an
# SWR fetch (see docs/prompts/14-ci-web-flake.md): macOS won the race, Linux
# containers and GitHub's runners did not. A flake you cannot reproduce is a
# flake you cannot fix, and "push and see" is a twenty-minute feedback loop
# that teaches people to ignore red.
#
# Three things make this match CI rather than approximate it:
#
#   * `git archive HEAD` — only COMMITTED files. The host's node_modules,
#     .next cache, .env and any uncommitted edit are all absent, exactly as on
#     a fresh checkout. This is also why it will not see a fix you have not
#     committed yet, which is deliberate: CI will not either.
#   * `node:20` on Linux, with the same `corepack prepare pnpm@9.12.3` CI
#     uses. Node's version and the platform both change timing, and timing is
#     the whole bug class this script exists to catch.
#   * `--frozen-lockfile`, so a lockfile that does not match package.json
#     fails here rather than in CI.
#
# `--platform linux/amd64` is NOT passed. It is slower under emulation on an
# Apple-silicon Mac and only matters for genuinely architecture-specific bugs;
# plain Linux is enough to flip the timing, which is what this catches. Set
# FB_CI_PLATFORM=linux/amd64 to force it.
#
# Usage:  pnpm ci:web          (or: ./scripts/ci-web-local.sh)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPORT_DIR="${FB_CI_EXPORT_DIR:-/tmp/fb-ci}"
NODE_IMAGE="${FB_CI_NODE_IMAGE:-node:20}"
PNPM_VERSION="9.12.3"

cd "$REPO_ROOT"

# Say so loudly rather than let somebody wonder why their fix "did nothing".
if ! git diff --quiet HEAD -- apps/web package.json pnpm-lock.yaml; then
  echo "NOTE: apps/web has uncommitted changes. This runs from 'git archive HEAD',"
  echo "      so those changes are NOT included — commit them first, or this"
  echo "      tests the previous state. (CI would do the same.)"
  echo
fi

echo "==> Exporting HEAD to $EXPORT_DIR"
rm -rf "$EXPORT_DIR"
mkdir -p "$EXPORT_DIR"
git archive HEAD | tar -x -C "$EXPORT_DIR"

# An empty array expanded under `set -u` is an error in bash 3.2, which is what
# macOS ships — so this builds the argument list with the `+` guard rather than
# a bare "${arr[@]}". Found by running this script on the Mac it is for.
PLATFORM_ARG=()
if [ -n "${FB_CI_PLATFORM:-}" ]; then
  PLATFORM_ARG=(--platform "$FB_CI_PLATFORM")
  echo "==> Forcing platform $FB_CI_PLATFORM (slower under emulation)"
fi

echo "==> Running lint, typecheck, test and build in $NODE_IMAGE"
docker run --rm -t ${PLATFORM_ARG[@]+"${PLATFORM_ARG[@]}"} \
  -v "$EXPORT_DIR:/w" -w /w "$NODE_IMAGE" bash -c "
    set -euo pipefail
    corepack enable
    corepack prepare pnpm@$PNPM_VERSION --activate
    pnpm install --frozen-lockfile
    pnpm --filter web lint
    pnpm --filter web typecheck
    pnpm --filter web test
    pnpm --filter web build
  "

echo
echo "==> Web pipeline green on Linux/node:20 from a clean export."
