#!/usr/bin/env bash
# weekly data refresh chain — grammar, synergy derive,
# staleness check. Optional: --kb, --precons, --reeval (understanding pulse).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

RUN_KB=0
RUN_PRECONS=0
RUN_REEVAL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --kb) RUN_KB=1; shift ;;
    --precons) RUN_PRECONS=1; shift ;;
    --reeval) RUN_REEVAL=1; shift ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/refresh_data_cadence.sh [--kb] [--precons] [--reeval]

  (default)  refresh corpus → grammar → synergy derive → check_data_staleness
  --kb       also run build_commanders_kb.py --mechanics-refresh
  --precons  also run scripts/bake_precons.py --stale (needs ANTHROPIC_API_KEY or claude CLI)
  --reeval   also run print_wave_reeval.py (offline) — coverage + playstyles;
             does not admit new oracle_signals or strategies
EOF
      exit 0
      ;;
    *)
      echo "Unknown flag: $1" >&2
      exit 1
      ;;
  esac
done

require_script() {
  local rel="$1"
  if [ ! -f "$REPO_ROOT/$rel" ]; then
    echo "Missing required script: $REPO_ROOT/$rel" >&2
    exit 1
  fi
}

require_script "corpus.py"
require_script "scripts/refresh_scryfall_grammar.py"
require_script "scripts/derive_synergy_objects.py"
require_script "scripts/check_data_staleness.py"

if [ "$RUN_KB" -eq 1 ]; then
  require_script "scripts/build_commanders_kb.py"
fi
if [ "$RUN_PRECONS" -eq 1 ]; then
  require_script "scripts/bake_precons.py"
fi
if [ "$RUN_REEVAL" -eq 1 ]; then
  require_script "scripts/print_wave_reeval.py"
fi

if [ -f "$REPO_ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
  log "activated .venv"
else
  log "no .venv — using system python3"
fi

PYTHON="${PYTHON:-python3}"

log "repo: $REPO_ROOT"
# pull oracle_cards (+ prices from default_cards) before anything
# else asks Scryfall one card at a time.
log "step 1/4: python -m corpus  (local Scryfall bulk)"
"$PYTHON" -m corpus

log "step 2/4: refresh_scryfall_grammar.py"
"$PYTHON" scripts/refresh_scryfall_grammar.py

log "step 3/4: derive_synergy_objects.py"
"$PYTHON" scripts/derive_synergy_objects.py

if [ "$RUN_KB" -eq 1 ]; then
  log "optional: build_commanders_kb.py --mechanics-refresh"
  "$PYTHON" scripts/build_commanders_kb.py --mechanics-refresh
fi

if [ "$RUN_PRECONS" -eq 1 ]; then
  log "optional: scripts/bake_precons.py --stale"
  "$PYTHON" scripts/bake_precons.py --stale
fi

log "step 4/4: check_data_staleness.py"
"$PYTHON" scripts/check_data_staleness.py

if [ "$RUN_REEVAL" -eq 1 ]; then
  # remeasure how cards read / which ways still fit. Detection only —
  # admitting new signals or playstyles stays a named PR
  # (docs/KNOW-MAGIC-MAINTENANCE.md).
  wave="cadence-reeval-$(date -u '+%Y-%m-%d')"
  log "optional: print_wave_reeval.py --wave $wave"
  "$PYTHON" scripts/print_wave_reeval.py --wave "$wave"
fi

log "cadence refresh complete"
