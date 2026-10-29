"""插件 pip 依赖：预检清单、确认式安装、安全边界（只装清单声明的包名）。

安全约束（对应 task-6 第 4 项）：
- 只允许安装 ``module.json`` → ``requirements`` 文件里声明、且通过字符集白名单的行；
  ``-e`` / ``-r`` / URL / 环境标记一律拒绝（它们能把 pip 变成任意指令入口）；
- 端点必须 ``confirm_deps=true`` 才真正装包；
- 依赖清单路径不得越出插件目录（防 ``../../.env`` 任意读）；
- pip 用子进程 + 180s 超时，stdout/stderr 截断回传；失败不阻断插件本身。
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

from app.application.module_install_service import ModuleInstallService
from app.infrastructure.config.config_service import ConfigService
from app.plugin import requirements as req_module
from app.plugin.base import ServiceAccess
from app.plugin.installer import ModuleInstallError, ZipPluginInstaller
from app.plugin.registry import ModuleRegistry

MAIN_SRC = '''
from app.plugin import BaseModule


class Module(BaseModule):
    name = "依赖测试"
    sign = "DepMod"
    description = "测试用"
    permission = "member"
'''

MISSING_PKG = "qqbot-not-installed-dep==1.0.0"

_FAKE_PIP_OK = """
import sys
from pathlib import Path

Path(sys.argv[1]).write_text("installed", encoding="utf-8")
print("fake-pip specs: " + " ".join(sys.argv[2:]))
sys.exit(0)
"""

_FAKE_PIP_FAIL = """
import sys

print("fake-pip specs: " + " ".join(sys.argv[1:]))
print("boom: network unreachable", file=sys.stderr)
sys.exit(1)
"""


def _manifest(name: str = "depmod", requirements: str | None = "requirements.txt") -> str:
    data = {"name": name, "display_name": "依赖测试", "version": "1.0.0", "api_version": 1}
    if requirements is not None:
        data["requirements"] = requirements
    return json.dumps(data, ensure_ascii=False)


def _write_plugin(plugin_dir, *, requirements_text: str | None = None, requirements_file: str = "requirements.txt"):
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "module.json").write_text(_manifest(requirements=requirements_file), encoding="utf-8")
    (plugin_dir / "main.py").write_text(MAIN_SRC, encoding="utf-8")
    if requirements_text is not None:
        (plugin_dir / requirements_file).write_text(requirements_text, encoding="utf-8")


def _make_installer(tmp_path) -> ZipPluginInstaller:
    builtin = tmp_path / "builtin"
    installed = tmp_path / "plugins"
    builtin.mkdir()
    installed.mkdir()
    return ZipPluginInstaller(
        install_dir=installed,
        builtin_dir=builtin,
        install_service=None,
        staging_dir=tmp_path / ".staging",
        backup_dir=tmp_path / ".backup",
    )


def _zip_plugin(tmp_path, *, requirements_text: str, name: str = "depmod", main: str = MAIN_SRC):
    zip_path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(f"{name}/module.json", _manifest(name))
        archive.writestr(f"{name}/main.py", main)
        archive.writestr(f"{name}/requirements.txt", requirements_text)
    return zip_path


def _use_fake_pip(monkeypatch, tmp_path, *, ok: bool = True):
    """把 pip 换成假脚本：成功时写 marker（模拟"包已装好"），不联网。"""
    script = tmp_path / "fake_pip.py"
    script.write_text(_FAKE_PIP_OK if ok else _FAKE_PIP_FAIL, encoding="utf-8")
    marker = tmp_path / "pip-marker.txt"

    def fake_command(self, specs, mirror=""):
        prefix = [sys.executable, str(script)] + ([str(marker)] if ok else [])
        return [*prefix, *specs]

    monkeypatch.setattr(ZipPluginInstaller, "_pip_command", fake_command)
    return marker


# ==================== 预检 ====================


def test_preflight_lists_missing_dependencies(tmp_path, monkeypatch):
    installer = _make_installer(tmp_path)
    monkeypatch.setattr(req_module, "installed_distributions", lambda: {"pytest"})
    zip_path = _zip_plugin(tmp_path, requirements_text=f"{MISSING_PKG}\npytest\n")

    preview = installer.preflight(zip_path)

    assert preview["deps_file"] == "requirements.txt"
    assert preview["deps_declared"] == [MISSING_PKG, "pytest"]
    assert preview["deps_missing"] == [MISSING_PKG], "已安装的包不应算缺失"
    assert preview["deps_rejected"] == []


def test_requirements_path_traversal_is_not_read(tmp_path, monkeypatch):
    """清单指到插件目录之外：不读、不报缺失（防任意文件读取）。"""
    installer = _make_installer(tmp_path)
    plugin_dir = tmp_path / "plugins" / "depmod"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "module.json").write_text(_manifest(requirements="../../secret.txt"), encoding="utf-8")
    (plugin_dir / "main.py").write_text(MAIN_SRC, encoding="utf-8")
    (tmp_path / "secret.txt").write_text("-e git+https://evil/pkg.git\n", encoding="utf-8")
    monkeypatch.setattr(req_module, "installed_distributions", lambda: set())

    status = installer.dependencies("depmod")

    assert status["requirements"] is None
    assert status["declared"] == []
    assert status["missing"] == []


# ==================== 安装（假 pip，不联网） ====================


async def test_install_dependencies_installs_only_missing_declared(tmp_path, monkeypatch):
    installer = _make_installer(tmp_path)
    _write_plugin(tmp_path / "plugins" / "depmod", requirements_text=f"{MISSING_PKG}\npytest\n")
    marker = _use_fake_pip(monkeypatch, tmp_path, ok=True)
    monkeypatch.setattr(
        req_module,
        "installed_distributions",
        lambda: {"pytest"} if not marker.exists() else {"pytest", "qqbot-not-installed-dep"},
    )

    result = await installer.install_dependencies("depmod")

    assert result["ok"] is True
    assert result["installed"] == [MISSING_PKG]
    assert result["missing"] == []
    assert MISSING_PKG in result["stdout"]
    assert "pytest" not in result["stdout"], "已安装的包不应传给 pip"
    assert result["command"][-1] == MISSING_PKG
    assert result["exit_code"] == 0


async def test_install_dependencies_failure_reports_command_and_keeps_missing(tmp_path, monkeypatch):
    installer = _make_installer(tmp_path)
    _write_plugin(tmp_path / "plugins" / "depmod", requirements_text=f"{MISSING_PKG}\n")
    _use_fake_pip(monkeypatch, tmp_path, ok=False)
    monkeypatch.setattr(req_module, "installed_distributions", lambda: set())

    result = await installer.install_dependencies("depmod")

    assert result["ok"] is False
    assert result["exit_code"] == 1
    assert "boom" in result["stderr"]
    assert result["missing"] == [MISSING_PKG]
    assert result["deps_missing"] is True
    assert MISSING_PKG in result["command"], "失败时必须给可复制命令"


async def test_install_dependencies_rejects_unsupported_lines(tmp_path):
    installer = _make_installer(tmp_path)
    _write_plugin(
        tmp_path / "plugins" / "depmod",
        requirements_text="-e git+https://evil/pkg.git\nfoo; rm -rf /\n",
    )

    with pytest.raises(ModuleInstallError) as exc_info:
        await installer.install_dependencies("depmod")

    assert "不支持" in str(exc_info.value)
    assert exc_info.value.hint


async def test_install_dependencies_rejects_unsafe_mirror(tmp_path):
    installer = _make_installer(tmp_path)
    _write_plugin(tmp_path / "plugins" / "depmod", requirements_text=f"{MISSING_PKG}\n")

    for mirror in ("ftp://mirror/simple", "--index-url https://evil/simple", "https://ok/simple; rm -rf /"):
        with pytest.raises(ModuleInstallError) as exc_info:
            await installer.install_dependencies("depmod", mirror=mirror)
        assert "镜像" in str(exc_info.value)


async def test_install_dependencies_without_missing_skips_pip(tmp_path, monkeypatch):
    installer = _make_installer(tmp_path)
    _write_plugin(tmp_path / "plugins" / "depmod", requirements_text=f"{MISSING_PKG}\n")
    monkeypatch.setattr(req_module, "installed_distributions", lambda: {"qqbot-not-installed-dep"})

    async def no_run(self, specs, mirror, timeout):
        raise AssertionError("没有缺失包时不应该启动 pip")

    monkeypatch.setattr(ZipPluginInstaller, "_run_pip", no_run)

    result = await installer.install_dependencies("depmod")

    assert result["ok"] is True
    assert result["missing"] == []
    assert result["installed"] == []
    assert result["exit_code"] == 0


# ==================== WebUI 端点契约 ====================


async def test_install_deps_endpoint_requires_confirm():
    from app.webui.api import modules as api

    class _Registry:
        async def install_dependencies(self, *args, **kwargs):
            raise AssertionError("未确认时不得触发安装")

    response = await api.install_module_dependencies(
        module_name="depmod", request=None, confirm_deps=False, mirror="", bot_id=None, registry=_Registry()
    )

    assert response.status_code == 400
    assert "确认" in json.loads(response.body)["message"]


async def test_install_deps_endpoint_success_contract(monkeypatch):
    from app.webui.api import modules as api
    from app.webui.ws import manager

    async def fake_broadcast(message: str) -> None:
        return None

    monkeypatch.setattr(manager, "broadcast", fake_broadcast)

    class _Registry:
        async def install_dependencies(self, name, *, mirror="", bot_id=None):
            return {
                "module_name": name,
                "ok": True,
                "requirements": "requirements.txt",
                "declared": [MISSING_PKG],
                "installed": [MISSING_PKG],
                "missing": [],
                "deps_missing": False,
                "exit_code": 0,
                "command": ["pip", "install", MISSING_PKG],
                "stdout": "ok",
                "stderr": "",
                "loaded": True,
            }

    response = await api.install_module_dependencies(
        module_name="depmod", request=None, confirm_deps=True, mirror="", bot_id="acc1", registry=_Registry()
    )
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["status"] == "success"
    assert body["installed"] == [MISSING_PKG]
    assert body["loaded"] is True
    assert "message" in body


async def test_install_deps_endpoint_failure_returns_502_with_command(monkeypatch):
    from app.webui.api import modules as api

    class _Registry:
        async def install_dependencies(self, name, *, mirror="", bot_id=None):
            return {
                "module_name": name,
                "ok": False,
                "requirements": "requirements.txt",
                "declared": [MISSING_PKG],
                "installed": [],
                "missing": [MISSING_PKG],
                "deps_missing": True,
                "exit_code": 1,
                "command": ["pip", "install", MISSING_PKG],
                "stdout": "",
                "stderr": "boom",
                "loaded": False,
            }

    response = await api.install_module_dependencies(
        module_name="depmod", request=None, confirm_deps=True, mirror="", bot_id=None, registry=_Registry()
    )
    body = json.loads(response.body)

    assert response.status_code == 502
    assert body["ok"] is False
    assert body["hint"]
    assert body["command"]
    assert body["stderr"] == "boom"


async def test_dependencies_endpoint_returns_missing_list():
    from app.webui.api import modules as api

    class _Registry:
        def dependencies_status(self, name):
            return {
                "module_name": name,
                "requirements": "requirements.txt",
                "declared": [MISSING_PKG],
                "rejected": [],
                "missing": [MISSING_PKG],
                "command": ["pip", "install", MISSING_PKG],
            }

    response = await api.module_dependencies(module_name="depmod", request=None, registry=_Registry())

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["missing"] == [MISSING_PKG]
    assert body["command"]


# ==================== 注册表集成：装完依赖后重试加载 ====================


# ==================== 安装时自动装依赖（auto_deps） ====================

#: 模拟"入口就 import 第三方包"的插件：缺依赖时加载必然失败（必须"先装依赖再加载"）
IMPORT_MISSING_MAIN = '''
import qqbot_definitely_missing_runtime_dep  # noqa: F401

from app.plugin import BaseModule


class Module(BaseModule):
    name = "依赖测试"
    sign = "DepMod"
    description = "测试用"
    permission = "member"
'''


def _make_registry(container, root: Path) -> ModuleRegistry:
    builtin = root / "builtin"
    installed = root / "plugins"
    builtin.mkdir(parents=True)
    installed.mkdir(parents=True)
    return ModuleRegistry(
        plugins_dir=builtin,
        zip_plugins_dir=installed,
        config_service=container.get(ConfigService),
        services=container.get(ServiceAccess),
        install_service=ModuleInstallService(root / "uninstalled_modules.json"),
        staging_dir=root / ".staging",
        backup_dir=root / ".backup",
    )


async def _cleanup_registry(registry: ModuleRegistry, name: str = "depmod") -> None:
    for bot_id in list(registry.loaded_map().get(name, {})):
        await registry.unload_single(name, bot_id)
    for key in [k for k in sys.modules if k.startswith(f"qqbot_plugin_{name}")]:
        del sys.modules[key]


async def test_install_from_zip_auto_deps_installs_before_load(container, tmp_path, monkeypatch):
    """auto_deps=True：先 pip 装声明依赖，再加载模块（一次性装好就能用）。"""
    registry = _make_registry(container, tmp_path / "plugin_root")
    marker = _use_fake_pip(monkeypatch, tmp_path, ok=True)
    monkeypatch.setattr(
        req_module,
        "installed_distributions",
        lambda: {"pytest"} if not marker.exists() else {"pytest", "qqbot-not-installed-dep"},
    )
    try:
        info = await registry.install_from_zip(
            _zip_plugin(tmp_path, requirements_text=f"{MISSING_PKG}\n"), auto_deps=True
        )

        assert info["loaded"] is True
        assert info["deps"]["ok"] is True
        assert info["deps"]["installed"] == [MISSING_PKG]
        assert info["deps"]["missing"] == []
        assert info["deps_missing"] is False
        assert marker.exists(), "auto_deps=True 时 pip 应被真正调用"
    finally:
        await _cleanup_registry(registry)


async def test_install_from_zip_without_auto_deps_does_not_run_pip(
    container, tmp_path, monkeypatch
):
    """默认（auto_deps=False）安装时不能碰 pip：保持"手动按钮"语义，装依赖要显式确认。"""
    registry = _make_registry(container, tmp_path / "plugin_root")
    monkeypatch.setattr(req_module, "installed_distributions", lambda: set())

    async def no_run(self, specs, mirror, timeout):
        raise AssertionError("未勾选自动装依赖时不应该启动 pip")

    monkeypatch.setattr(ZipPluginInstaller, "_run_pip", no_run)
    try:
        info = await registry.install_from_zip(
            _zip_plugin(tmp_path, requirements_text=f"{MISSING_PKG}\n")
        )
        assert info["loaded"] is True
        assert "deps" not in info, "没跑自动装依赖就不该有 deps 回执"
    finally:
        await _cleanup_registry(registry)


async def test_install_from_zip_auto_deps_failure_reports_pip_output_and_rolls_back(
    container, tmp_path, monkeypatch
):
    """自动装依赖失败 + 模块加载失败 → 回滚安装，并在错误里给 pip 输出（可复现定位）。"""
    root = tmp_path / "plugin_root"
    registry = _make_registry(container, root)
    _use_fake_pip(monkeypatch, tmp_path, ok=False)
    monkeypatch.setattr(req_module, "installed_distributions", lambda: set())
    try:
        with pytest.raises(ModuleInstallError) as exc_info:
            await registry.install_from_zip(
                _zip_plugin(
                    tmp_path,
                    requirements_text=f"{MISSING_PKG}\n",
                    main=IMPORT_MISSING_MAIN,
                ),
                auto_deps=True,
            )

        assert "依赖未装齐" in exc_info.value.hint
        assert "boom" in exc_info.value.hint, "pip stderr 的最后一行要带给用户"
        assert not (root / "plugins" / "depmod").is_dir(), "加载失败应回滚安装目录"
    finally:
        await _cleanup_registry(registry)


async def test_install_zip_endpoint_passes_auto_deps_and_mirror(monkeypatch, tmp_path):
    """install-zip 接口：auto_deps / mirror 原样透传给安装器（前端勾选即生效）。"""
    from starlette.datastructures import UploadFile

    from app.webui.api import modules as api
    from app.webui.ws import manager

    captured: dict = {}

    class _Registry:
        async def install_from_zip(self, path, *, force=False, auto_deps=False, mirror=""):
            captured.update(force=force, auto_deps=auto_deps, mirror=mirror)
            return {"module_name": "depmod", "version": "1.0.0"}

    class _BotService:
        async def ensure_module_instances(self, name):
            return None

    async def fake_broadcast(message: str) -> None:
        return None

    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    zip_path = _zip_plugin(tmp_path, requirements_text=f"{MISSING_PKG}\n")

    with open(zip_path, "rb") as handle:
        response = await api.install_module_zip(
            request=None,
            file=UploadFile(file=handle, filename="depmod.zip"),
            force=False,
            auto_deps=True,
            mirror="https://pypi.tuna.tsinghua.edu.cn/simple",
            bot_service=_BotService(),
            registry=_Registry(),
        )

    assert response.status_code == 200
    assert captured == {
        "force": False,
        "auto_deps": True,
        "mirror": "https://pypi.tuna.tsinghua.edu.cn/simple",
    }


# ==================== 注册表集成：装完依赖后重试加载 ====================


async def test_registry_install_dependencies_reloads_module(container, tmp_path, monkeypatch):
    root = tmp_path / "plugin_root"
    builtin = root / "builtin"
    installed = root / "plugins"
    builtin.mkdir(parents=True)
    installed.mkdir(parents=True)
    registry = ModuleRegistry(
        plugins_dir=builtin,
        zip_plugins_dir=installed,
        config_service=container.get(ConfigService),
        services=container.get(ServiceAccess),
        install_service=ModuleInstallService(root / "uninstalled_modules.json"),
        staging_dir=root / ".staging",
        backup_dir=root / ".backup",
    )
    marker = _use_fake_pip(monkeypatch, tmp_path, ok=True)
    monkeypatch.setattr(
        req_module,
        "installed_distributions",
        lambda: {"pytest"} if not marker.exists() else {"pytest", "qqbot-not-installed-dep"},
    )
    try:
        await registry.install_from_zip(_zip_plugin(tmp_path, requirements_text=f"{MISSING_PKG}\n"))
        result = await registry.install_dependencies("depmod", bot_id="acc1")
        assert result["ok"] is True
        assert result["loaded"] is True, "依赖装好后应重试加载模块"
        assert registry.get("depmod", "acc1") is not None
        # 只读查询接口同样能看到"已无缺失"
        status = registry.dependencies_status("depmod")
        assert status["missing"] == []
    finally:
        for bot_id in list(registry.loaded_map().get("depmod", {})):
            await registry.unload_single("depmod", bot_id)
        for key in [k for k in sys.modules if k.startswith("qqbot_plugin_depmod")]:
            del sys.modules[key]
