#!/usr/bin/env bash
# update.sh: optional maintenance extras. Code updates on every desktop launch
# (run.sh → pull_on_launch + /api/boot brand veil). Use this for a forced
# restart, an out-of-band cadence run, or a print-wave re-eval.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

RUN_CADENCE=0
PRINT_WAVE=""
DO_PULL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --pull) DO_PULL=1; shift ;;
    --cadence) RUN_CADENCE=1; shift ;;
    --print-wave)
      if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        echo "usage: ./update.sh --print-wave <set-or-precon-name>" >&2
        exit 1
      fi
      PRINT_WAVE="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./update.sh [--pull] [--cadence] [--print-wave NAME]

Code updates happen on desktop launch (./run.sh / launchd). This script is for
extras and a forced restart:

  (default)     restart the launchd server when registered (picks up launch pull)
  --pull        also git pull --ff-only before restart
  --cadence     run scripts/refresh_data_cadence.sh --kb
  --print-wave  run print_wave_reeval.py --refresh --fetch --wave NAME
EOF
      exit 0
      ;;
    *)
      echo "Unknown flag: $1" >&2
      exit 1
      ;;
  esac
done

log() { echo "[update] $*"; }

if [ "$DO_PULL" -eq 1 ]; then
  if [ ! -d "$REPO_ROOT/.git" ]; then
    echo "Not a git checkout — cannot pull." >&2
    exit 1
  fi
  log "git pull --ff-only"
  git pull --ff-only
fi

LABEL="com.deckwright"
USER_UID="$(id -u)"
DOMAIN="gui/$USER_UID"
RESTARTED=0

if [ "$(uname)" = "Darwin" ] && command -v launchctl >/dev/null 2>&1; then
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 \
     || launchctl list "$LABEL" >/dev/null 2>&1; then
    log "restart server: launchctl kickstart -k $DOMAIN/$LABEL"
    if launchctl kickstart -k "$DOMAIN/$LABEL"; then
      RESTARTED=1
    else
      log "kickstart failed — try: launchctl kickstart -k $DOMAIN/$LABEL"
    fi
  fi
fi

if [ "$RESTARTED" -eq 0 ]; then
  log "launchd server not registered — restart with ./run.sh (or ./config/install-desktop.sh once)."
fi

if [ -f "$REPO_ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
fi
PYTHON="${PYTHON:-python3}"

if [ "$RUN_CADENCE" -eq 1 ]; then
  log "data cadence (--kb --reeval) — catalogs + understanding remeasure"
  "$REPO_ROOT/scripts/refresh_data_cadence.sh" --kb --reeval
fi

if [ -n "$PRINT_WAVE" ]; then
  # Named set dump: re-fetch oracle bulk, then report unread / precision.
  # Admitting new signals or strategies is still a separate PR.
  log "print-wave reeval (fetch bulk): $PRINT_WAVE"
  "$PYTHON" "$REPO_ROOT/scripts/print_wave_reeval.py" --refresh --fetch --wave "$PRINT_WAVE"
fi

log "done. Next browser open waits on /api/boot only if this process is still warming."
