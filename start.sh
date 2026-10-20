#!/usr/bin/env bash
# 启动入口：自举 venv → 依赖自检（缺什么装什么）→ 跑主程序。
# 依赖判定逻辑只在 scripts/ops/check_deps.py，这里保持薄壳，避免 bat/sh 各写一份漂移。
set -u
cd "$(dirname "$0")"

PY="venv/bin/python3"
[ -x "$PY" ] || PY="venv/bin/python"

# ===== 1) 虚拟环境：没有就建 =====
if [ ! -x "$PY" ]; then
    echo "[start] 未找到 venv，正在创建虚拟环境..."
    python3 -m venv venv || {
        echo "[start] 创建 venv 失败：请确认已安装 Python 3.10+"
        exit 1
    }
    PY="venv/bin/python3"
    [ -x "$PY" ] || PY="venv/bin/python"
fi

# ===== 2) 启动前依赖自检（失败只提示，不阻塞启动） =====
"$PY" scripts/ops/check_deps.py || \
    echo "[start] 依赖自检未通过，仍尝试启动；若启动时报 ModuleNotFoundError，请联网后运行 scripts/ops/requirements.sh"

# ===== 3) 启动 =====
echo "[start] 启动 QQBot Next ..."
exec "$PY" main.py
