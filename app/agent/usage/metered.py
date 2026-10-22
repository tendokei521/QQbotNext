"""计量装饰器：给 provider 套一层，**每次上游调用**记一条 `stat_event`。

## 为什么挂在 provider 层

模块调 LLM 有四条路径，只有一条走流水线：

```
BaseModule.llm_turn     → run_turn → invoker.chat ─┐
BaseModule.llm_chat     → client.chat ─────────────┤
模块 resolve_invoker()  → invoker.chat ─────────────┼→ chat_with_fallback → get_provider().chat()
模块 get_provider()     → provider.chat ───────────┘
```

`get_provider()` 是**所有** provider 实例的工厂，在这里套一层即可一次收住四条路径。

## 与 `usage_sink` 的关系

`usage_sink` 是 turn 内部的**累计口径**（一轮多次请求累加，供调试日志/遥测用）；
统计走这里，**每次上游调用一行**，能真实反映回退/重试造成的多次消耗。两者互不干扰。
"""

from __future__ import annotations

import time
from typing import Any

from app.agent.usage.context import current_usage_ctx
from app.agent.usage.labels import label_from_session_id
from app.agent.usage.recorder import get_recorder


def _api_model(config: dict, model: Any = None) -> tuple[str, str]:
    """从 provider 配置里取 `(api, model)`。"""
    cfg = config or {}
    api = str(cfg.get("provider") or cfg.get("provider_type") or "")
    mdl = str(model or cfg.get("model") or "")
    return api, mdl


def _record(
    *,
    usage: dict | None,
    config: dict,
    model: Any,
    latency_ms: int,
    ok: bool,
    err_kind: str = "",
    estimated: bool = False,
) -> None:
    recorder = get_recorder()
    if recorder is None:
        return
    ctx = current_usage_ctx()
    api, mdl = _api_model(config, model)
    recorder.record_llm(
        usage=usage,
        latency_ms=latency_ms,
        ok=ok,
        err_kind=err_kind,
        estimated=estimated,
        bot_id=ctx.bot_id,
        source=ctx.source,
        api=api,
        model=mdl,
        session_id=ctx.session_id,
        session_label=ctx.session_label or label_from_session_id(ctx.session_id),
        group_id=ctx.group_id,
        user_id=ctx.user_id,
    )


class MeteredProvider:
    """provider 透明代理：记完账原样返回/透传。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    # ---- 属性透传（name / capabilities / supports / get_models …）----
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def inner(self) -> Any:
        return self._inner

    @property
    def config(self) -> dict:
        return getattr(self._inner, "config", {}) or {}

    async def chat(self, messages: list[dict], **kwargs: Any) -> Any:
        started = time.monotonic()
        model = kwargs.get("model")
        try:
            response = await self._inner.chat(messages, **kwargs)
        except Exception as e:  # noqa: BLE001 —— 失败也要记账，然后原样抛出
            _record(
                usage=None,
                config=self.config,
                model=model,
                latency_ms=int((time.monotonic() - started) * 1000),
                ok=False,
                err_kind=type(e).__name__,
            )
            raise
        _record(
            usage=getattr(response, "usage", None),
            config=self.config,
            model=model or getattr(response, "model", None),
            latency_ms=int((time.monotonic() - started) * 1000),
            ok=bool(getattr(response, "ok", True)),
            err_kind="" if getattr(response, "ok", True) else "not_ok",
        )
        return response

    async def stream(self, messages: list[dict], **kwargs: Any) -> Any:
        from app.agent.providers.base import merge_usage

        started = time.monotonic()
        model = kwargs.get("model")
        usage: dict = {}
        ok = True
        err_kind = ""
        try:
            async for event in self._inner.stream(messages, **kwargs):
                event_usage = getattr(event, "usage", None)
                if event_usage:
                    merge_usage(usage, event_usage)
                yield event
        except Exception as e:  # noqa: BLE001 —— 流中断也要记一笔（可能已消耗 token）
            ok = False
            err_kind = type(e).__name__
            raise
        finally:
            _record(
                usage=usage or None,
                config=self.config,
                model=model,
                latency_ms=int((time.monotonic() - started) * 1000),
                ok=ok,
                err_kind=err_kind,
                # 流式没拿到 usage → 标 estimated（上游没回，值不可信）
                estimated=not usage,
            )


def wrap_metered(provider: Any) -> Any:
    """给 provider 套计量代理（幂等：已包过就不再包）。"""
    if isinstance(provider, MeteredProvider):
        return provider
    if get_recorder() is None:
        return provider
    return MeteredProvider(provider)


__all__ = ["MeteredProvider", "wrap_metered"]
