#!/bin/bash
# ============================================================
#  QQBot Next - 打开一个已激活 venv 的 shell
#  本文件与 venv/ 同在仓库根目录，所以 cd 到脚本目录就够；
#  多退一级会跑到仓库外去白建一个空 venv。
# ============================================================
set -u
cd "$(dirname "$0")" || exit 1

if [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
else
    echo "No venv, creating..."
    python3 -m venv venv
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

if [ -n "${SHELL:-}" ]; then
    exec "$SHELL"
fi
