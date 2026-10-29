"""配置包导出：模块 `enabled` 只在账号确有显式启停时才写进清单。

回归背景：老实现把"没写过启停、走账号默认开"当成 `enabled: true` 导出，
再应用这个包时会把账号上所有插件固化成开启——用户看到的就是
「应用一次配置包 / 换一次包，插件开关全部自己打开了」。
"""

from __future__ import annotations

import io
import json
import zipfile

from app.application.bundle.exporter import export_bundle


def _modules(blob: bytes) -> dict[str, dict]:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        manifest = json.loads(archive.read("bundle.json"))
    return {item["name"]: item for item in manifest["components"]["modules"]}


def test_module_without_explicit_switch_omits_enabled():
    blob = export_bundle(
        bundle_id="b-portable",
        name="可迁移配置",
        module_configs={
            "llm_memory": {"enabled": None, "config": {"memory_enable": True}},
        },
    )
    entry = _modules(blob)["llm_memory"]
    assert "enabled" not in entry, "没写过启停的模块不能在包里固化 enabled"
    assert entry["source"] == "configs/module-llm_memory.json"


def test_explicit_switch_is_exported():
    blob = export_bundle(
        bundle_id="b-explicit",
        name="显式开关",
        module_configs={
            "llm_memory": {"enabled": False, "config": {}},
            "llm_context": {"enabled": True, "config": {}},
        },
    )
    modules = _modules(blob)
    assert modules["llm_memory"]["enabled"] is False
    assert modules["llm_context"]["enabled"] is True
