"""P3-3：套装应用到账号（计划 → 事务 → 校验 → 可回滚）。

锁定四件事：
1. 应用后对象真的建出来（人格/Provider/模型），Agent 配置指向它们；
2. 记账：`bundle_objects` 记下"本套装创建了什么"，`account_active_bundle` 记激活；
3. 回滚恢复应用前的绑定，并删掉本套装创建的对象（用户后建的不动）；
4. 中途失败 / 进程崩溃（`applying` 残留）→ 自动回到应用前。
"""

from __future__ import annotations

import json

import pytest

from app.application.bundle.applier import BundleApplier
from app.application.bundle.errors import BundleError
from app.application.bundle.manifest import normalize_manifest
from app.infrastructure.config.config_service import ConfigOp

MANIFEST = {
    "schema": "qqbotnext.bundle/1",
    "id": "demo-pack",
    "name": "演示套装",
    "version": "1.0.0",
    "framework": {"api_version": 1},
    "components": {
        "personas": [{"source": "persona/default.md", "name": "群聊搭子"}],
        "providers": [{
            "provider": "openai", "api_base": "https://api.deepseek.com/v1",
            "model": "deepseek-chat", "preset_name": "DeepSeek",
        }],
        "agent": [{"source": "configs/agent.json"}],
        "modules": [{"name": "llm_memory", "enabled": True, "source": "configs/module-llm_memory.json"}],
    },
    "secrets": [{"key": "providers[0].api_key", "label": "Key", "required": True}],
}

FILES = {
    "persona/default.md": "你是一个普通群友。".encode(),
    "configs/agent.json": json.dumps({"group_enable": True, "private_enable": True}).encode(),
    "configs/module-llm_memory.json": json.dumps({"memory_enable": True}).encode(),
}


async def _seed(container, config_service, account_id="acc1"):
    await config_service.save_bots([
        {"id": account_id, "name": "主号", "ws_url": "ws://127.0.0.1:3001"},
    ])
    await config_service.apply_ops([
        ConfigOp("binding", ("agent", "account", account_id), {
            "enabled": True,
            "config": {"group_enable": False, "persona_preset_id": "", "provider_model_pool": []},
            "permission": {"user_mode": "blacklist", "user_list": []},
        }),
    ])


async def test_apply_creates_objects_and_activates(container, config_service, db):
    await _seed(container, config_service)
    manifest = normalize_manifest(MANIFEST)
    applier = BundleApplier(config_service)

    result = await applier.apply(
        "demo-pack", "acc1", manifest, FILES,
        secrets={"providers[0].api_key": "sk-test"},
    )

    assert result["status"] == "active"
    activation = await db.fetchone("SELECT * FROM bundle_activations WHERE id = ?", (result["activation_id"],))
    assert activation["status"] == "active"

    agent = config_service.get_module_config("agent", "acc1")
    assert agent["group_enable"] is True, "套装里的 Agent 配置应合并进来"
    assert agent["persona_preset_id"].startswith("role_")
    assert agent["provider_model_pool"], "模型应入池"
    model_id = agent["provider_model_id"]
    model = config_service.get_provider_model(model_id)
    assert model["model"] == "deepseek-chat"
    preset = config_service.get_provider_preset(model["preset_id"])
    assert preset["config"]["api_key"] == "sk-test"

    assert config_service.get_role_preset(agent["persona_preset_id"])["system_prompt"] == "你是一个普通群友。"
    assert config_service.get_module_config("llm_memory", "acc1")["memory_enable"] is True
    assert config_service.get_module_authority("llm_memory", "acc1")["enabled"] is True

    objects = await db.fetchall("SELECT kind FROM bundle_objects WHERE bundle_id = ?", ("demo-pack",))
    assert {row["kind"] for row in objects} == {"role_preset", "provider_preset", "provider_model"}
    active = await db.fetchone("SELECT bundle_id FROM account_active_bundle WHERE account_id = ?", ("acc1",))
    assert active["bundle_id"] == "demo-pack"


async def test_rollback_restores_and_removes_created_objects(container, config_service, db):
    await _seed(container, config_service)
    applier = BundleApplier(config_service)
    result = await applier.apply(
        "demo-pack", "acc1", normalize_manifest(MANIFEST), FILES,
        secrets={"providers[0].api_key": "sk-test"},
    )
    created_role = config_service.get_module_config("agent", "acc1")["persona_preset_id"]

    rollback = await applier.rollback(result["activation_id"])

    assert rollback["status"] == "rolled_back"
    agent = config_service.get_module_config("agent", "acc1")
    assert agent["group_enable"] is False, "回滚后应恢复应用前的值"
    assert agent["persona_preset_id"] == ""
    assert config_service.get_role_preset(created_role) is None, "本套装创建的对象应被删除"
    assert await db.fetchone(
        "SELECT account_id FROM account_active_bundle WHERE account_id = ?", ("acc1",)
    ) is None
    assert await db.fetchone(
        "SELECT id FROM role_presets WHERE id = ?", (created_role,)
    ) is None


async def test_apply_failure_rolls_back_and_marks_failed(container, config_service, db, monkeypatch):
    await _seed(container, config_service)
    applier = BundleApplier(config_service)
    before = config_service.get_module_config("agent", "acc1")

    async def _boom(_ops):
        raise RuntimeError("模拟落库失败")

    monkeypatch.setattr(config_service, "apply_ops", _boom)

    with pytest.raises(BundleError, match="已回滚"):
        await applier.apply(
            "demo-pack", "acc1", normalize_manifest(MANIFEST), FILES,
            secrets={"providers[0].api_key": "sk-test"},
        )

    assert config_service.get_module_config("agent", "acc1") == before, "失败后配置必须原样"
    row = await db.fetchone("SELECT status, error FROM bundle_activations")
    assert row["status"] == "failed"
    assert "模拟落库失败" in row["error"]


async def test_recover_interrupted_rolls_back_applying(container, config_service, db):
    await _seed(container, config_service)
    applier = BundleApplier(config_service)
    result = await applier.apply(
        "demo-pack", "acc1", normalize_manifest(MANIFEST), FILES,
        secrets={"providers[0].api_key": "sk-test"},
    )
    # 模拟"应用写完，进程立刻崩了"：把状态改回 applying
    await db.execute(
        "UPDATE bundle_activations SET status = ? WHERE id = ?", ("applying", result["activation_id"])
    )

    recovered = await applier.recover_interrupted()

    assert recovered == [result["activation_id"]]
    row = await db.fetchone("SELECT status FROM bundle_activations WHERE id = ?", (result["activation_id"],))
    assert row["status"] == "rolled_back"
    assert config_service.get_module_config("agent", "acc1")["persona_preset_id"] == ""


async def test_upgrade_preserves_user_edits_and_reuses_secret(container, config_service, db):
    """P4-3：同 id 升版本 → 用户改过的键不被覆盖、未重填的密钥复用旧值、旧对象清理。"""
    await _seed(container, config_service)
    applier = BundleApplier(config_service)
    first = await applier.apply(
        "demo-pack", "acc1", normalize_manifest(MANIFEST), FILES,
        secrets={"providers[0].api_key": "sk-old"},
    )
    agent = config_service.get_module_config("agent", "acc1")
    old_model = agent["provider_model_id"]
    old_role = agent["persona_preset_id"]

    # 用户后来：关掉群聊，并换了 Key
    await config_service.save_module_config("agent", "acc1", {**agent, "group_enable": False})
    old_preset_id = config_service.get_provider_model(old_model)["preset_id"]
    old_preset = config_service.get_provider_preset(old_preset_id)
    await config_service.save_provider_preset(old_preset_id, {
        **old_preset, "config": {**old_preset["config"], "api_key": "sk-user-new"},
    })

    # 新版本：想打开群聊 + 新增 temperature
    files_v2 = dict(FILES)
    files_v2["configs/agent.json"] = json.dumps(
        {"group_enable": True, "temperature": 0.5}
    ).encode()
    manifest_v2 = normalize_manifest({**MANIFEST, "version": "2.0.0"})

    second = await applier.apply("demo-pack", "acc1", manifest_v2, files_v2, secrets={})

    agent2 = config_service.get_module_config("agent", "acc1")
    assert agent2["group_enable"] is False, "用户改过的键不能被新包覆盖"
    assert agent2["temperature"] == 0.5, "用户没改过的新键应生效"
    assert agent2["provider_model_id"] != old_model, "应以新对象替换"
    assert "agent.group_enable" in second["plan"]["preserved"]

    new_preset = config_service.get_provider_preset(
        config_service.get_provider_model(agent2["provider_model_id"])["preset_id"]
    )
    assert new_preset["config"]["api_key"] == "sk-user-new", "未重填密钥应复用旧值"
    assert config_service.get_role_preset(old_role) is None, "旧人格已无人引用，应清理"

    # 激活历史：两条记录，旧的仍可追溯
    rows = await db.fetchall(
        "SELECT status FROM bundle_activations WHERE bundle_id = ? ORDER BY started_at", ("demo-pack",)
    )
    assert [row["status"] for row in rows] == ["superseded", "active"], "旧激活应标记 superseded"
