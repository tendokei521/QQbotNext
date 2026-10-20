#!/usr/bin/env bash
# ============================================================
#  QQBot Next - launcher
#    [1/3] bootstrap virtualenv
#    [2/3] verify dependencies   -> scripts/ops/check_deps.py
#    [3/3] hand control to the bot
#  Dependency logic lives in check_deps.py only: this shell is a
#  thin wrapper so bat/sh can never drift apart.
# ============================================================
set -u
cd "$(dirname "$0")"

stage() {
    printf '  [%s/3] %-16s %s\n' "$1" "$2" "$3"
}

echo
echo "  QQBot Next  ::  launcher"
echo "  ----------------------------------------------------------"

# ---------- [1/3] interpreter ----------
PY="venv/bin/python3"
[ -x "$PY" ] || PY="venv/bin/python"
if [ -x "$PY" ]; then
    stage 1 interpreter "$PY"
else
    stage 1 interpreter "creating venv (first run)"
    if ! python3 -m venv venv; then
        echo
        echo "  [FAIL] could not create the virtualenv."
        echo "         Install Python 3.10+ and make sure it is on PATH."
        exit 1
    fi
    PY="venv/bin/python3"
    [ -x "$PY" ] || PY="venv/bin/python"
fi

# ---------- [2/3] dependencies ----------
if "$PY" scripts/ops/check_deps.py --quiet; then
    stage 2 dependencies "ok"
else
    stage 2 dependencies "incomplete (see above)"
    echo
    echo "  Continuing anyway. If the bot exits with ModuleNotFoundError,"
    echo "  go online and run:  scripts/ops/requirements.sh"
fi

# ---------- [3/3] launch ----------
stage 3 launch "main.py"
echo
exec "$PY" main.py
