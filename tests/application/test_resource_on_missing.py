"""资源字段的悬挂引用策略（`on_missing`）：保存在**写路径**校验，两套 UI 天然一致。

默认 `keep`（保留悬挂 id，界面显示「已删除或未找到」）；`on_missing: "clear"` 时
保存即丢弃失效值，避免模块长期拿着解析不到的 id 运行。
"""

from __future__ import annotations

from app.plugin.resource_validation import apply_on_missing


class _Registry:
    """只实现 has/options 的最小替身（与真实 ResourceRegistry 的同名接口一致）。"""

    def __init__(self, kinds=None, options=None, boom=False):
        self._kinds = set(kinds or ["role_preset", "provider_model"])
        self._options = dict(options or {})
        self._boom = boom
        self.calls: list[tuple] = []

    def has(self, kind):
        return kind in self._kinds

    async def options(self, kind, *, bot_id=None, filters=None):
        self.calls.append((kind, dict(filters or {})))
        if self._boom:
            raise RuntimeError("候选拉取炸了")
        return self._options.get(kind, [])


def _schema(**field):
    base = {"type": "resource", "resource": "role_preset", "on_missing": "clear"}
    base.update(field)
    return {"persona_preset_id": base}


ALIVE = [{"value": "p1", "label": "在的"}]


# ==================== keep（默认）= 不改动 ====================


async def test_default_keep_leaves_dangling_value():
    reg = _Registry(options={"role_preset": ALIVE})
    schema = {"persona_preset_id": {"type": "resource", "resource": "role_preset"}}
    data, cleared = await apply_on_missing(schema, {"persona_preset_id": "gone"}, reg)
    assert data == {"persona_preset_id": "gone"} and cleared == []


# ==================== clear = 丢弃失效值 ====================


async def test_clear_drops_dangling_single_value():
    reg = _Registry(options={"role_preset": ALIVE})
    data, cleared = await apply_on_missing(_schema(), {"persona_preset_id": "gone", "other": 1}, reg)
    assert data == {"persona_preset_id": "", "other": 1}
    assert cleared == ["persona_preset_id"]


async def test_clear_keeps_alive_value():
    reg = _Registry(options={"role_preset": ALIVE})
    data, cleared = await apply_on_missing(_schema(), {"persona_preset_id": "p1"}, reg)
    assert data == {"persona_preset_id": "p1"} and cleared == []


async def test_clear_filters_multi_values():
    reg = _Registry(options={"provider_model": [{"value": "m1", "label": "在的"}]})
    schema = {"pool": {"type": "resources", "resource": "provider_model", "on_missing": "clear"}}
    data, cleared = await apply_on_missing(schema, {"pool": ["m1", "gone"]}, reg)
    assert data == {"pool": ["m1"]} and cleared == ["pool"]


async def test_clear_ignores_blank_and_absent_values():
    reg = _Registry(options={"role_preset": ALIVE})
    data, cleared = await apply_on_missing(_schema(), {"persona_preset_id": "", "extra": "x"}, reg)
    assert data == {"persona_preset_id": "", "extra": "x"} and cleared == []

    data, cleared = await apply_on_missing(_schema(), {"extra": "x"}, reg)
    assert data == {"extra": "x"} and cleared == []


async def test_filters_are_passed_to_candidates():
    """只声明了 embedding 过滤的字段，不应被全量候选判定为"存在"。"""
    reg = _Registry(options={"provider_model": []})
    schema = {
        "embed": {
            "type": "resource", "resource": "provider_model",
            "filters": {"provider_type": "embedding"}, "on_missing": "clear",
        }
    }
    await apply_on_missing(schema, {"embed": "m1"}, reg)
    assert reg.calls == [("provider_model", {"provider_type": "embedding"})]


# ==================== 安全兜底：宁可保留也不误删 ====================


async def test_unknown_kind_or_missing_registry_keeps_value():
    reg = _Registry(kinds=["other"])
    data, cleared = await apply_on_missing(_schema(), {"persona_preset_id": "gone"}, reg)
    assert data == {"persona_preset_id": "gone"} and cleared == []

    data, cleared = await apply_on_missing(_schema(), {"persona_preset_id": "gone"}, None)
    assert data == {"persona_preset_id": "gone"} and cleared == []


async def test_candidate_error_keeps_value():
    reg = _Registry(boom=True)
    data, cleared = await apply_on_missing(_schema(), {"persona_preset_id": "gone"}, reg)
    assert data == {"persona_preset_id": "gone"} and cleared == [], "拉候选失败时不该误删配置"


async def test_non_resource_fields_untouched_and_input_not_mutated():
    reg = _Registry(options={"role_preset": ALIVE})
    original = {"persona_preset_id": "gone", "mode": "auto"}
    schema = dict(_schema())
    schema["mode"] = {"type": "select", "options": {"auto": "自动"}, "on_missing": "clear"}
    data, cleared = await apply_on_missing(schema, original, reg)
    assert data["mode"] == "auto" and cleared == ["persona_preset_id"]
    assert original == {"persona_preset_id": "gone", "mode": "auto"}, "不得改动调用方传入的 dict"


# ==================== 端点接线 ====================


async def test_config_endpoint_reports_cleared_fields(container):
    """保存接口会调用它并把被清空的字段回给前端（两套 UI 共用同一条 API）。"""
    from pathlib import Path

    from starlette.testclient import TestClient

    from app.plugin.registry import ModuleRegistry
    from app.webui.app import create_app

    registry = container.get(ModuleRegistry)
    registry.plugins_dir = Path(__file__).resolve().parents[2] / "app" / "plugins"
    account_id = "acc_t1"
    assert await registry.load_single("astrbot_alignment", account_id) is True
    try:
        client = TestClient(create_app(container))
        # 该插件的 persona_preset_id 未声明 on_missing（默认 keep）→ 悬挂值原样保留
        resp = client.post(
            f"/api/module/astrbot_alignment/config?account_id={account_id}",
            json={"persona_preset_id": "不存在的预设"},
        )
        assert resp.status_code == 200
        assert "cleared" not in resp.json(), "默认 keep 不该清空"
    finally:
        await registry.unload_single("astrbot_alignment", account_id)
