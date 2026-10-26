"""Provider 预设独立管理测试。"""

import pytest

from app.application.provider_preset_service import PASSWORD_MASK, ProviderPresetService


@pytest.fixture
async def preset_service(config_service):
    return ProviderPresetService(config_service)


async def test_create_list_update_preset(preset_service):
    created = await preset_service.create_preset({
        "name": "DeepSeek 测试",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com/", "api_key": "sk-secret", "retry_attempts": 5},
    })
    assert created["id"]
    assert created["config"]["api_base"] == "https://api.deepseek.com"  # 尾部斜杠清洗
    assert created["config"]["api_key"] == "sk-secret"

    listed = preset_service.list_presets()
    assert len(listed) == 1
    assert listed[0]["config"]["api_key"] == "sk-secret"

    raw = preset_service.get_preset(created["id"], masked=False)
    assert raw["config"]["api_key"] == "sk-secret"

    updated = await preset_service.update_preset(created["id"], {
        "name": "DeepSeek 改名",
        "config": {"api_key": PASSWORD_MASK},
    })
    assert updated["name"] == "DeepSeek 改名"
    assert preset_service.get_preset(created["id"], masked=False)["config"]["api_key"] == "sk-secret"


async def test_delete_preset_clears_references(preset_service, config_service):
    created = await preset_service.create_preset({
        "name": "被引用",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-ref"},
    })
    await config_service.save_module_config("agent", 100, {
        "provider_preset_id": created["id"],
    })

    await preset_service.delete_preset(created["id"])

    assert preset_service.get_preset(created["id"]) is None
    cfg = config_service.get_module_config("agent", 100)
    assert "provider_preset_id" not in cfg


async def test_migrate_legacy_agent_config(preset_service, config_service):
    await config_service.save_module_config("agent", 7, {
        "api_key": "sk-legacy",
        "api_base": "https://api.deepseek.com/",
        "provider": "openai",
        "model": "deepseek-chat",
        "retry_attempts": 4,
    })
    migrated = await preset_service.migrate_legacy_agent_configs()
    assert migrated == 1

    cfg = config_service.get_module_config("agent", 7)
    assert cfg.get("provider_preset_id")
    assert "api_key" not in cfg
    assert "api_base" not in cfg

    preset = preset_service.get_preset(cfg["provider_preset_id"], masked=False)
    assert preset["config"]["api_key"] == "sk-legacy"
    assert preset["config"]["api_base"] == "https://api.deepseek.com"

    # 幂等：再次迁移不应重复创建
    assert await preset_service.migrate_legacy_agent_configs() == 0


# ── 内置预设（抄自 DSH 的 provider catalog）─────────────────────


def test_builtin_catalog_only_lists_supported_adapters():
    """内置模板必须只包含本项目真的注册了的适配器类型。"""
    from app.agent.providers import PROVIDERS
    from app.application.provider_preset_catalog import BUILTIN_PROVIDER_PRESETS

    keys = [entry["key"] for entry in BUILTIN_PROVIDER_PRESETS]
    assert len(keys) == len(set(keys)), "内置预设 key 不能重复"
    # 用户点名要的、以及最常用的几家必须在
    assert {"opencode-go", "opencode", "deepseek", "zai", "anthropic", "google"} <= set(keys)

    seen_connections = set()
    for entry in BUILTIN_PROVIDER_PRESETS:
        assert entry["provider"] in PROVIDERS, entry["key"]
        assert entry["api_base"].startswith("https://"), entry["key"]
        assert not entry["api_base"].endswith("/"), entry["key"]
        assert entry["env"], entry["key"]
        assert entry["models"], entry["key"]
        assert entry["name"] and entry["note"], entry["key"]
        connection = (entry["provider"], entry["api_base"])
        assert connection not in seen_connections, f"{entry['key']} 与其它条目端点重复"
        seen_connections.add(connection)


def test_builtin_catalog_uses_domestic_overseas_categories():
    """内置预设只分「国内 / 国外」两档；OpenCode 这类境外订阅归「国外」。"""
    from app.application.provider_preset_catalog import BUILTIN_PROVIDER_PRESETS

    by_key = {entry["key"]: entry for entry in BUILTIN_PROVIDER_PRESETS}
    assert {entry["category"] for entry in BUILTIN_PROVIDER_PRESETS} == {"国内", "国外"}
    assert by_key["opencode-go"]["category"] == "国外"
    assert by_key["opencode"]["category"] == "国外"
    assert by_key["deepseek"]["category"] == "国内"


def test_list_builtin_presets_marks_nothing_installed(preset_service):
    presets = preset_service.list_builtin_presets()
    assert presets
    assert all(p["installed_preset_id"] == "" for p in presets)


def test_list_builtin_presets_shows_domestic_first(preset_service):
    """界面分组顺序：国内在前、国外在后（同一类内保持目录原始顺序）。"""
    presets = preset_service.list_builtin_presets()
    order = {"国内": 0, "国外": 1}
    ranks = [order[p["category"]] for p in presets]
    assert ranks == sorted(ranks)
    assert presets[0]["category"] == "国内"


async def test_import_builtin_preset_creates_then_dedupes(preset_service):
    first = await preset_service.import_builtin_preset("opencode-go")
    assert first["created"] is True
    preset = first["preset"]
    assert preset["name"] == "OpenCode Go"
    assert preset["provider"] == "openai"
    assert preset["config"]["api_base"] == "https://opencode.ai/zen/go/v1"
    # OpenCode 兼容端点必须带会话头
    assert preset["config"]["headers"] == {"x-opencode-session": "qqbotnext6"}
    # 不预置模型实例：真实模型交给「拉取模型」按账号返回
    assert preset_service.config_service.list_provider_models(preset["id"]) == []

    again = await preset_service.import_builtin_preset("opencode-go")
    assert again["created"] is False
    assert again["preset"]["id"] == preset["id"]
    assert len(preset_service.list_presets()) == 1

    marked = {p["key"]: p["installed_preset_id"] for p in preset_service.list_builtin_presets()}
    assert marked["opencode-go"] == preset["id"]


async def test_import_builtin_preset_matches_existing_manual_preset(preset_service):
    """用户手写过的同端点预设也算「已存在」，不会导出一条重复的。"""
    manual = await preset_service.create_preset({
        "name": "我的 OpenCode Go",
        "provider": "openai",
        "config": {"api_base": "https://opencode.ai/zen/go/v1/", "api_key": "oc_sk_test"},
    })

    result = await preset_service.import_builtin_preset("opencode-go")
    assert result["created"] is False
    assert result["preset"]["id"] == manual["id"]
    assert len(preset_service.list_presets()) == 1


async def test_import_builtin_preset_matches_alias_provider(preset_service):
    """老预设用 ``deepseek`` 这类别名时，也要认出「同端点已存在」。"""
    manual = await preset_service.create_preset({
        "name": "老写法 deepseek",
        "provider": "deepseek",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-test"},
    })

    result = await preset_service.import_builtin_preset("deepseek")
    assert result["created"] is False
    assert result["preset"]["id"] == manual["id"]
    assert len(preset_service.list_presets()) == 1


async def test_import_builtin_preset_accepts_key_and_custom_name(preset_service):
    result = await preset_service.import_builtin_preset(
        "deepseek", api_key="sk-new", name="DeepSeek 备用"
    )
    assert result["created"] is True
    preset = result["preset"]
    assert preset["name"] == "DeepSeek 备用"
    assert preset["config"]["api_key"] == "sk-new"
    assert preset["config"]["api_base"] == "https://api.deepseek.com"


async def test_import_unknown_builtin_preset_raises(preset_service):
    with pytest.raises(ValueError):
        await preset_service.import_builtin_preset("no-such-provider")


@pytest.mark.parametrize(
    ("provider", "expected_model"),
    [
        ("openai", "deepseek-chat"),
        ("anthropic", "claude-haiku-4-5"),
        ("gemini", "gemini-2.5-flash"),
        ("openai-responses", "gpt-5.2"),
    ],
)
async def test_preset_test_uses_format_aware_default_model(
    monkeypatch, preset_service, provider, expected_model
):
    """预设下没有模型实例时，测连接不能拿 deepseek-chat 去问 Anthropic/Gemini。"""
    from types import SimpleNamespace

    created = await preset_service.create_preset({
        "name": f"测试-{provider}",
        "provider": provider,
        "config": {"api_base": "https://example.com", "api_key": "k"},
    })
    seen: dict = {}

    class _FakeProvider:
        async def get_models(self):
            return []

        async def chat(self, messages, **kwargs):
            seen["model"] = kwargs.get("model")
            return SimpleNamespace(ok=True, raw={}, text="ok")

    monkeypatch.setattr(
        "app.application.provider_preset_service.get_provider",
        lambda config: _FakeProvider(),
    )

    result = await preset_service.test_preset(created["id"])
    assert result["ok"] is True
    assert seen["model"] == expected_model
