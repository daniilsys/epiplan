#!/usr/bin/env bash
# Installs epiplan: virtualenv, browser, `epiplan` command, agent skill, MCP server registration.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/epiplan"

echo "==> virtualenv + dependencies"
if [ ! -x "$HERE/.venv/bin/pip" ]; then
  PYBIN=""
  for c in "${EPIPLAN_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null &&
       "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' &&
       "$c" -m venv --clear "$HERE/.venv" >/dev/null 2>&1 &&
       [ -x "$HERE/.venv/bin/pip" ]; then
      PYBIN="$c"
      break
    fi
  done
  [ -n "$PYBIN" ] || { echo "Python 3.10+ with venv and pip is required." >&2; exit 1; }
fi
"$HERE/.venv/bin/pip" install -q --upgrade pip
"$HERE/.venv/bin/pip" install -q -r "$HERE/requirements.txt"
"$HERE/.venv/bin/playwright" install chromium

if [[ "$(uname -s)" == Darwin ]]; then
  echo "==> Apple Calendar bridge"
  if ! bash "$HERE/bin/build-calendar"; then
    echo "Apple Calendar bridge could not be built. Epitech tools still work. Install Xcode command line tools and run bin/build-calendar to enable Calendar." >&2
  fi
fi

echo "==> config in $CONF"
mkdir -p "$CONF"; chmod 700 "$CONF"
[ -f "$CONF/rules.md" ] || cp "$HERE/rules.example.md" "$CONF/rules.md"

echo "==> epiplan command"
mkdir -p "$HOME/.local/bin"
ln -sfn "$HERE/bin/epiplan" "$HOME/.local/bin/epiplan"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "    add ~/.local/bin to your PATH";; esac

echo "==> skill"
if command -v claude >/dev/null; then
  mkdir -p "$HOME/.claude/skills"
  ln -sfn "$HERE/skills/planning" "$HOME/.claude/skills/planning"
fi
if command -v codex >/dev/null; then
  CODEX_SKILLS="${CODEX_HOME:-$HOME/.codex}/skills"
  mkdir -p "$CODEX_SKILLS"
  ln -sfn "$HERE/skills/planning" "$CODEX_SKILLS/planning"
fi

echo "==> MCP servers"
if command -v claude >/dev/null; then
  claude mcp remove epitech-planning --scope user >/dev/null 2>&1 || true
  claude mcp add epitech-planning --scope user \
    -e EPIPLAN_ENABLE_WRITES=1 -e EPIPLAN_MAX_WRITES=5 -e EPIPLAN_MODULE_ALLOWLIST= \
    -- "$HERE/.venv/bin/python" "$HERE/server.py"
fi
if command -v codex >/dev/null; then
  codex mcp remove epitech-planning >/dev/null 2>&1 || true
  codex mcp add epitech-planning \
    --env EPIPLAN_ENABLE_WRITES=1 --env EPIPLAN_MAX_WRITES=5 --env EPIPLAN_MODULE_ALLOWLIST= \
    -- "$HERE/.venv/bin/python" "$HERE/server.py"
fi
if ! command -v claude >/dev/null && ! command -v codex >/dev/null; then
  cat <<EOF
    No supported CLI found. Register the server in your MCP client with:
      command: $HERE/.venv/bin/python
      args:    ["$HERE/server.py"]
      env:     EPIPLAN_ENABLE_WRITES=1, EPIPLAN_MAX_WRITES=5, EPIPLAN_MODULE_ALLOWLIST=
EOF
fi

cat <<EOF

Done. Next:
  1. epiplan login        (log in once; MFA works)
  2. edit $CONF/rules.md  (your registration preferences)
  3. restart your agent, then ask it to check your planning (/planning)
  4. optional on macOS: epiplan calendar connect (Apple Calendar conflict checks + automatic sync)
EOF
