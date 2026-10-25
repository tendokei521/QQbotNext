"""工具并发分类（P3）：声明在 `ToolSpec` 上，判定按**每次调用的实参**。

为什么不能按工具名分类：OneBot 的同一个 ``_handler`` 既跑 ``send_poke`` 也跑
``set_group_kick``，按名字分就会把写操作放进并发池。这里的规矩是 fail-closed：
只有明确声明 ``concurrency="parallel"`` 且 ``concurrency_safe(args)`` 返回**精确 True**
才允许并发，其余一律独占。
"""

from __future__ import annotations

from app.agent.tools.spec import ToolSpec


def _spec(**kwargs) -> ToolSpec:
    async def _handler(ctx, args):
        return "ok"

    base = dict(
        name="probe",
        description="",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        scopes=("*",),
    )
    base.update(kwargs)
    return ToolSpec(**base)


def test_default_is_exclusive():
    """没声明 = 独占（旧构造点一个都不用改，行为也是最保守的那个）。"""
    spec = _spec()

    assert spec.concurrency == "exclusive"
    assert spec.runs_parallel({}) is False
    assert spec.timeout_s is None


def test_declared_parallel_runs_parallel():
    assert _spec(concurrency="parallel").runs_parallel({}) is True


def test_unknown_concurrency_value_is_treated_as_exclusive():
    assert _spec(concurrency="parallel-ish").runs_parallel({}) is False


def test_concurrency_safe_is_checked_per_call():
    """同一个工具：读实参放行、写实参拦下——这正是按实参分类的意义。"""
    spec = _spec(
        concurrency="parallel",
        concurrency_safe=lambda args: args.get("action") == "read",
    )

    assert spec.runs_parallel({"action": "read"}) is True
    assert spec.runs_parallel({"action": "write"}) is False
    assert spec.runs_parallel({}) is False


def test_concurrency_safe_must_return_exactly_true():
    """真值不算数：返回 1 / "yes" 一律按独占（拿不准就串行）。"""
    for value in (1, "yes", [1], None):
        spec = _spec(concurrency="parallel", concurrency_safe=lambda args, v=value: v)
        assert spec.runs_parallel({}) is False, value


def test_concurrency_safe_exception_falls_back_to_exclusive():
    def _boom(args):
        raise RuntimeError("判定坏了")

    assert _spec(concurrency="parallel", concurrency_safe=_boom).runs_parallel({}) is False


def test_timeout_can_be_declared_per_tool():
    assert _spec(timeout_s=5).timeout_s == 5
