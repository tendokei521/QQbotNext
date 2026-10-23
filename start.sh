#!/usr/bin/env bash
# ============================================================
#  QQBot Next - launcher（薄壳）
#  真正的逻辑全在 start.py：建 venv → 升 pip/setuptools →
#  查/装依赖 → 启动 main.py。本脚本只负责找到解释器并转交，
#  所以 bat / sh 不可能再各写一套而漂移。
#
#  用系统 Python 跑 start.py（而不是 venv 里的）：venv 还没建或
#  已经坏掉时，venv 解释器根本起不来，而 start.py 恰恰要负责重建。
# ============================================================
set -u
cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
elif [ -x venv/bin/python3 ]; then
    PY=venv/bin/python3
elif [ -x venv/bin/python ]; then
    PY=venv/bin/python
else
    echo "  [FAIL] 找不到 Python 3.10+，请先安装后再运行 start.sh。" >&2
    exit 1
fi

exec "$PY" start.py "$@"
