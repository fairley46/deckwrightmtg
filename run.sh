#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Prefer a real 3.12+ interpreter. Creating the venv with an older system
# python3 can produce an unusable environment.
for d in "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin; do
  [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH" ;; esac
done
export PATH

_py_ver() {
  "$1" -c 'import sys; print(sys.version_info.major * 100 + sys.version_info.minor)' 2>/dev/null || echo 0
}

PYTHON=""
for c in python3.12 python3.13 python3.14 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    ver=$(_py_ver "$(command -v "$c")")
    if [ "${ver:-0}" -ge 312 ]; then
      PYTHON=$(command -v "$c")
      break
    fi
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Need Python 3.12+ on PATH."
  echo "python3: $(command -v python3 2>/dev/null || echo none)"
  exit 1
fi

if [ -x .venv/bin/python ]; then
  vver=$(_py_ver .venv/bin/python)
  if [ "${vver:-0}" -lt 312 ]; then
    echo ".venv is Python < 3.12 (version code ${vver})."
    echo "  rm -rf .venv"
    echo "then restart."
    exit 1
  fi
fi

# Opt-in git pull on launch. Strangers cloning the repo should not hit the
# network or rewrite their checkout. Set DECKWRIGHT_PULL_ON_LAUNCH=1 to enable.
if [ "${DECKWRIGHT_PULL_ON_LAUNCH:-}" = "1" ]; then
  bash "$(dirname "$0")/scripts/pull_on_launch.sh" || true
fi
[ -d .venv ] || "$PYTHON" -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
# Staging identity (DECKWRIGHT_ENV=green): env from the process must win over .env.
_DW_ENV="${DECKWRIGHT_ENV:-}"
_DW_PORT="${PORT:-}"
_DW_HOST="${HOST:-}"
_DW_HOME="${DECKWRIGHT_HOME:-}"
_DW_CORPUS="${DECKWRIGHT_CORPUS_DIR:-}"
_DW_RO="${DECKWRIGHT_CORPUS_READONLY:-}"
[ -f .env ] && set -a && source .env && set +a
if [ "$_DW_ENV" = "green" ]; then
  export DECKWRIGHT_ENV=green
  [ -n "$_DW_PORT" ] && PORT="$_DW_PORT"
  [ -n "$_DW_HOST" ] && HOST="$_DW_HOST"
  [ -n "$_DW_HOME" ] && export DECKWRIGHT_HOME="$_DW_HOME"
  [ -n "$_DW_CORPUS" ] && export DECKWRIGHT_CORPUS_DIR="$_DW_CORPUS"
  [ -n "$_DW_RO" ] && export DECKWRIGHT_CORPUS_READONLY="$_DW_RO"
fi
: "${HOST:=127.0.0.1}"
: "${PORT:=8000}"

# Needs an LLM: Anthropic Claude via API key or `claude` CLI. All model calls
# go through deckwright/llm.py.
if [ -z "$ANTHROPIC_API_KEY" ] && ! command -v claude >/dev/null 2>&1; then
  echo "No ANTHROPIC_API_KEY and no claude CLI."
  echo "Copy .env.example to .env and set a key, or install the claude CLI."
  exit 1
fi

# Prefer the CLI adapter when present so a Keychain/API key does not silently
# bill the Messages API. Force API with DECKWRIGHT_LLM=api.
if [ -z "$DECKWRIGHT_LLM" ] && command -v claude >/dev/null 2>&1; then
  unset ANTHROPIC_API_KEY
fi

# Optional: Forge pressure test. Absent, the app hides the feature.
if [ -d /opt/homebrew/opt/openjdk/bin ]; then
  PATH="/opt/homebrew/opt/openjdk/bin:$PATH"
fi
if [ -z "$DECKWRIGHT_FORGE" ]; then
  for jar in "$HOME"/Applications/forge/forge-gui-desktop-*-jar-with-dependencies.jar; do
    [ -f "$jar" ] && DECKWRIGHT_FORGE="$jar" && break
  done
fi
[ -n "$DECKWRIGHT_FORGE" ] && export DECKWRIGHT_FORGE

export ANTHROPIC_API_KEY HOST PORT PATH
if [ "${DECKWRIGHT_ENV:-blue}" = "green" ]; then
  echo "STAGING :${PORT} — do not buy lists from this process"
fi
echo "DeckWright on http://${HOST}:${PORT}"
python -m deckwright
