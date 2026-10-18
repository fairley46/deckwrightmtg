#!/usr/bin/env bash
# Fail-open code update at launch. Never exits non-zero — a bad network or
# dirty tree must not block the app.
#
# Invoked only when run.sh sees DECKWRIGHT_PULL_ON_LAUNCH=1 (off by default).
#
# Release channel:
#   DECKWRIGHT_RELEASE_REF=<tag-or-branch>  — fetch + ff-only checkout that ref
#   unset / empty                           — ff-only pull of the current branch
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [ "${DECKWRIGHT_SKIP_PULL:-}" = "1" ]; then
  echo "[pull-on-launch] skipped (DECKWRIGHT_SKIP_PULL=1)"
  exit 0
fi

if [ ! -d "$REPO_ROOT/.git" ]; then
  echo "[pull-on-launch] no .git — skip"
  exit 0
fi

if ! command -v git >/dev/null 2>&1; then
  echo "[pull-on-launch] git missing — skip"
  exit 0
fi

# Dirty tree: do not fight local edits. Clean pull only.
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  echo "[pull-on-launch] dirty working tree — skip"
  exit 0
fi

REF="${DECKWRIGHT_RELEASE_REF:-}"

if [ -n "$REF" ]; then
  echo "[pull-on-launch] release channel: $REF"
  if git fetch --tags origin "$REF" 2>/dev/null \
     || git fetch origin "refs/tags/${REF}:refs/tags/${REF}" 2>/dev/null \
     || git fetch origin "$REF" 2>/dev/null; then
    if git rev-parse --verify "$REF" >/dev/null 2>&1 \
       || git rev-parse --verify "refs/tags/$REF" >/dev/null 2>&1 \
       || git rev-parse --verify "origin/$REF" >/dev/null 2>&1; then
      target="$REF"
      git rev-parse --verify "refs/tags/$REF" >/dev/null 2>&1 && target="refs/tags/$REF"
      git rev-parse --verify "origin/$REF" >/dev/null 2>&1 \
        && ! git rev-parse --verify "$REF" >/dev/null 2>&1 \
        && ! git rev-parse --verify "refs/tags/$REF" >/dev/null 2>&1 \
        && target="origin/$REF"
      if git checkout --detach "$target" 2>/dev/null \
         || git checkout -B "_dw_release" "$target" 2>/dev/null; then
        echo "[pull-on-launch] checked out $REF"
        exit 0
      fi
    fi
  fi
  echo "[pull-on-launch] release checkout failed — continue on current tree"
  exit 0
fi

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
  echo "[pull-on-launch] detached HEAD — skip"
  exit 0
fi

echo "[pull-on-launch] ff-only pull of $branch"
if git pull --ff-only origin "$branch" 2>/dev/null; then
  echo "[pull-on-launch] up to date"
else
  echo "[pull-on-launch] pull failed — continue on current tree"
fi
exit 0
