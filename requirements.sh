#!/bin/bash
# ============================================================
#  QQBot Next - 手动装依赖（离线/首次安装的兜底入口）
#  依赖清单的唯一事实源是 pyproject.toml；本脚本经由
#  requirements.txt 这层"指针"安装，不再自己列包名。
#  一键入口是 start.py（建 venv → 查/装依赖 → 启动）；
#  本脚本是它跑不动时的兜底，两者都在仓库根目录。
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

# 老 pip / 老 setuptools 装不了本项目的 pyproject 后端（需要 setuptools>=68）
python -m pip install --upgrade pip setuptools
python -m pip install -r requirements.txt

echo
echo "Other ways to do the same thing:"
echo "  python start.py                # one-click: venv + deps + launch"
echo "  python check_deps.py --dev     # checks and installs only what is missing"
