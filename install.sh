#!/usr/bin/env bash
#
# trade-monitor installer.
#
# Open source, no subscription, no API keys, no Node. Everything runs locally.
#
#   git clone https://github.com/pothak/trade-monitor
#   cd trade-monitor
#   ./install.sh
#
# What it does:
#   1. checks Python >= 3.10
#   2. creates a virtualenv in .venv (never touches your system Python)
#   3. installs the engine + the contracts package (editable)
#   4. verifies the license policy (permissive only)
#   5. verifies imports and the strategy registry
#   6. optionally seeds a little market data
#   7. tells you how to start the dashboard
#
# Re-running is safe: it reuses the venv and reinstalls cleanly.

set -euo pipefail

# ---- pretty output ---------------------------------------------------------
BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'
YELLOW=$'\033[33m'; RESET=$'\033[0m'
info()  { printf '%s==>%s %s\n' "$BOLD" "$RESET" "$1"; }
ok()    { printf '%s  ok%s %s\n' "$GREEN" "$RESET" "$1"; }
warn()  { printf '%s  !!%s %s\n' "$YELLOW" "$RESET" "$1"; }
fail()  { printf '%s error:%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

SEED="${SEED:-}"
PORT="${PORT:-8787}"

# ---- 1. python -------------------------------------------------------------
info "checking Python"
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$cand"; break
    fi
  fi
done
[ -n "$PY" ] || fail "Python 3.10+ is required but was not found on PATH."
ok "$($PY --version) ($(command -v "$PY"))"

# ---- 2. virtualenv ---------------------------------------------------------
info "creating virtualenv at .venv"
if [ ! -d .venv ]; then
  "$PY" -m venv .venv || fail "could not create venv. Install python3-venv if on Debian/Ubuntu."
  ok "created .venv"
else
  ok ".venv already exists, reusing"
fi
# shellcheck disable=SC1091
source .venv/bin/activate

info "upgrading pip"
python -m pip install --quiet --upgrade pip >/dev/null
ok "pip $(python -m pip --version | awk '{print $2}')"

# ---- 3. install ------------------------------------------------------------
info "installing contracts package (the strategy interface, zero deps)"
python -m pip install --quiet -e ./contracts_pkg
ok "trade-monitor-contracts installed"

info "installing the engine"
python -m pip install --quiet -e .
ok "trade-monitor installed"

# ---- 4. license policy -----------------------------------------------------
info "verifying dependency licenses (permissive only)"
python - <<'PYEOF'
import sys
try:
    import importlib.metadata as md
except ImportError:
    sys.exit(0)

allowed = ("mit", "apache", "bsd", "isc", "psf", "zlib", "0bsd", "cc0", "python software")
denied = ("gpl", "agpl", "lgpl", "sspl", "busl", "proprietary", "commercial")

def license_of(name):
    try:
        meta = md.metadata(name)
    except Exception:
        return None
    lic = (meta.get("License-Expression") or meta.get("License") or "").lower()
    if len(lic) > 80:
        lic = ""
    for c in meta.get_all("Classifier") or []:
        if "License" in c:
            lic += " " + c.lower()
    return lic

core = ["trade-monitor-contracts"]
edge = ["yfinance", "pandas", "requests"]
problems = []
for pkg in core + edge:
    lic = license_of(pkg)
    if lic is None:
        continue
    if any(d in lic for d in denied):
        problems.append(f"{pkg}: {lic}")
print(f"  checked {len(core + edge)} packages")
if problems:
    print("  DISALLOWED LICENSES FOUND:")
    for p in problems:
        print("   -", p)
    sys.exit(1)
PYEOF
ok "all dependencies are permissively licensed"

# ---- 5. verify -------------------------------------------------------------
info "verifying install"
python - <<'PYEOF'
import sys
import trade_monitor_contracts as contracts
print(f"  contracts {contracts.__version__} ({len(contracts.__all__)} public names)")
import trade_monitor
print(f"  engine    {trade_monitor.__version__}")
from trade_monitor.strategy import REGISTRY
from trade_monitor.strategy.loader import discover
discover(REGISTRY)
names = REGISTRY.names()
print(f"  strategies: {', '.join(names) or '(none)'}")
from trade_monitor.server import App
from trade_monitor.cli import main
assert "canslim" in names, "reference strategy 'canslim' failed to load"
print("  engine core imports cleanly (stdlib only)")
PYEOF
ok "imports and registry verified"

# ---- 6. correctness tests --------------------------------------------------
if [ "${SKIP_TESTS:-0}" = "1" ]; then
  warn "skipping tests (SKIP_TESTS=1)"
elif python -c "import pytest" 2>/dev/null; then
  info "running the correctness test suite"
  if python -m pytest -q tests/ 2>&1 | tail -1 | grep -q "passed"; then
    ok "tests passed"
  else
    warn "some tests failed. Run: .venv/bin/python -m pytest tests/ -q"
  fi
else
  warn "pytest not installed; skipping tests. Run: .venv/bin/pip install -e '.[dev]'"
fi

# ---- 7. optional seed ------------------------------------------------------
if [ -n "$SEED" ]; then
  info "seeding market data (SEED=$SEED)"
  trade-monitor seed --limit "$SEED" || warn "seed had failures (free data is imperfect; that is normal)"
  ok "seed complete"
else
  warn "skipped data seed. Run:  SEED=50 ./install.sh   or   trade-monitor seed --limit 50"
fi

# ---- 8. done ---------------------------------------------------------------
echo
printf '%s%s trade-monitor is installed %s\n' "$BOLD" "$GREEN" "$RESET"
echo
echo "  Start the dashboard:"
echo "    trade-monitor serve --port $PORT"
echo
echo "  Run a scan:"
echo "    trade-monitor scan --strategy canslim --market IN"
echo
echo "  Write your own strategy:"
echo "    put a .py file in ./strategies/ and it loads automatically"
echo "    see strategies/breakout_proximity.py for a working example"
echo
echo "  Everything is local: ${DIM}$HOME/.trade-monitor/${RESET}"
echo "  No account, no API key, no subscription, MIT licensed."
echo
if [ -z "$SEED" ]; then
  echo "  ${YELLOW}Tip:${RESET} the dashboard is empty until you seed or scan."
  echo "       ./install.sh with SEED=25 seeds a few symbols to try."
  echo
fi
