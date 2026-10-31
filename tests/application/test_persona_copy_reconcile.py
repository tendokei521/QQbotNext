"""人格**单一来源**的启动对账：账号里的 ``system_prompt`` 副本必须跟绑定预设一致。

## 为什么会有这个测试

人格曾经是"两份"：``persona_preset_id``（活引用）与 ``system_prompt``（内容快照）。
装配时预设优先、副本只在预设缺失时兜底，于是两份一旦分叉就会出现：

- 改预设 → 生效；改账号级 ``system_prompt`` → **不生效**（用户以为改了没反应）；
- 预设一删，旧副本顶上（"过时的提示词又回来了"）。

``reconcile_persona_copy`` 在启动时把副本对齐到预设内容。这里锁住四条语义：
对齐一次、幂等、预设缺失时不覆盖（副本是唯一来源）、没有绑定的账号不动。
"""

from __future__ import annotations

import pytest

from app.application.agent_prompt_preset_migration import reconcile_persona_copy


class _ConfigService:
    """最小配置中心替身：只实现对账用到的三个方法。"""

    def __init__(self, configs: dict, presets: dict) -> None:
        self._configs = {key: dict(value) for key, value in configs.items()}
        self._presets = dict(presets)
        self.saved: list[tuple[str, dict]] = []

    def get_all_module_configs(self, capability):
        assert capability == "agent"
        return {key: dict(value) for key, value in self._configs.items()}

    def get_role_preset(self, role_id):
        return self._presets.get(str(role_id))

    async def save_module_config(self, capability, account_id, config):
        self.saved.append((str(account_id), dict(config)))
        self._configs[str(account_id)] = dict(config)


PRESET_NEW = {"id": "p1", "name": "新人格", "system_prompt": "新的人格正文"}


@pytest.mark.asyncio
async def test_copy_is_aligned_to_the_bound_preset():
    service = _ConfigService(
        {"acc1": {"persona_preset_id": "p1", "system_prompt": "旧的人格正文"}},
        {"p1": PRESET_NEW},
    )

    fixed = await reconcile_persona_copy(service)

    assert fixed == 1
    assert service.saved == [("acc1", {"persona_preset_id": "p1", "system_prompt": "新的人格正文"})]


@pytest.mark.asyncio
async def test_already_aligned_is_not_rewritten():
    service = _ConfigService(
        {"acc1": {"persona_preset_id": "p1", "system_prompt": "新的人格正文"}},
        {"p1": PRESET_NEW},
    )

    assert await reconcile_persona_copy(service) == 0
    assert service.saved == [], "内容一致时不该产生写入（否则每次启动都刷一次库）"


@pytest.mark.asyncio
async def test_missing_preset_keeps_the_copy_as_fallback():
    """预设被删：副本就是唯一来源，不能被清空或覆盖成空串。"""
    service = _ConfigService(
        {"acc1": {"persona_preset_id": "gone", "system_prompt": "旧副本"}},
        {},
    )

    assert await reconcile_persona_copy(service) == 0
    assert service.saved == []
    assert service.get_all_module_configs("agent")["acc1"]["system_prompt"] == "旧副本"


@pytest.mark.asyncio
async def test_account_without_preset_is_untouched():
    """没选人格的账号靠 system_prompt 本身，绝不能被"对齐"成空。"""
    service = _ConfigService(
        {"acc1": {"persona_preset_id": "", "system_prompt": "自己写的人格"}},
        {"p1": PRESET_NEW},
    )

    assert await reconcile_persona_copy(service) == 0
    assert service.saved == []


@pytest.mark.asyncio
async def test_one_broken_account_does_not_block_others():
    class _Flaky(_ConfigService):
        async def save_module_config(self, capability, account_id, config):
            if account_id == "acc_bad":
                raise RuntimeError("写库失败")
            await super().save_module_config(capability, account_id, config)

    service = _Flaky(
        {
            "acc_bad": {"persona_preset_id": "p1", "system_prompt": "旧1"},
            "acc_ok": {"persona_preset_id": "p1", "system_prompt": "旧2"},
        },
        {"p1": PRESET_NEW},
    )

    assert await reconcile_persona_copy(service) == 1, "坏账号跳过，好账号照常对账"
    assert [row[0] for row in service.saved] == ["acc_ok"]
