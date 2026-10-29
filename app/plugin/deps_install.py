"""插件安装阶段的 pip 依赖：自动装声明依赖 + 失败提示。

`module.json` 用 ``requirements`` 声明依赖清单，框架有两条入口共用同一执行面：

- **安装时勾选自动装依赖**（本模块）：`ModuleRegistry.install_from_zip(auto_deps=True)`
  在加载模块之前先跑 pip，避免"入口就 import 第三方包"的插件装不上；
- **模块页手动「检查依赖 / 安装依赖」**：`ZipPluginInstaller.install_dependencies`。

本模块只做编排与错误收口，不解析清单、不执行 pip（分别在 `requirements.py` /
`installer.py`），避免长出一条独立的执行面。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger
from app.plugin.installer import ModuleInstallError


def _failure_payload(module_name: str, error: str, *, hint: str = "") -> dict:
    """自动装依赖失败时的结构化回执（形状与 installer 的成功回执保持一致）。"""
    return {
        "module_name": module_name,
        "ok": False,
        "requirements": None,
        "declared": [],
        "installed": [],
        "missing": [],
        "deps_missing": False,
        "exit_code": 1,
        "command": [],
        "stdout": "",
        "stderr": error,
        "error": error,
        "hint": hint,
    }


async def install_declared_dependencies(
    installer: Any, module_name: str, *, mirror: str = ""
) -> dict:
    """加载前装依赖：只装清单里声明且当前环境缺失的包；失败返回结构化结果（不抛）。

    依赖清单不合法（含 ``-e``/URL 等）时安装器会抛 ``ModuleInstallError``；这里收成
    ``ok=False``，由加载失败路径统一展示，避免自动装依赖把安装整体弄崩。
    """
    try:
        return await installer.install_dependencies(module_name, mirror=mirror)
    except ModuleInstallError as e:
        logger.warning(f"[Module] {module_name} 自动安装依赖失败: {e}")
        return _failure_payload(module_name, str(e), hint=e.hint)
    except Exception as e:  # noqa: BLE001 —— 自动装依赖是尽力而为，不能拖崩安装主流程
        logger.warning(f"[Module] {module_name} 自动安装依赖异常: {e}")
        return _failure_payload(module_name, str(e))


def annotate_install_info(info: dict, deps: dict | None) -> dict:
    """把自动装依赖的回执并进安装回执（没勾选自动装依赖时原样返回）。"""
    if deps is None:
        return info
    info["deps"] = deps
    info["deps_missing"] = bool(deps.get("missing"))
    return info


def deps_failure_hint(deps: dict | None) -> str:
    """自动装依赖失败时的可读提示（空字符串 = 依赖没出问题）。"""
    if not deps or deps.get("ok") is not False:
        return ""
    missing = "、".join(str(item) for item in (deps.get("missing") or [])) or "（未知）"
    detail = str(deps.get("error") or "").strip()
    if not detail:
        lines = str(deps.get("stderr") or "").strip().splitlines()
        detail = lines[-1][:200] if lines else ""
    return (
        f"依赖未装齐（{missing}）{('：' + detail) if detail else ''}；"
        "检查网络/镜像后重试，或在模块页点「安装依赖」"
    )
