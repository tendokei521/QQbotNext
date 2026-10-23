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


#: 常见 `api_base` 主机 → 服务商短名（用于面板显示，不必穷举）
_API_HOST_ALIAS = {
    "deepseek": "deepseek",
    "openai": "openai",
    "opencode": "opencode",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "googleapis": "gemini",
    "moonshot": "moonshot",
    "dashscope": "qwen",
    "bigmodel": "zhipu",
    "siliconflow": "siliconflow",
    "openrouter": "openrouter",
    "localhost": "local",
    "127.0.0.1": "local",
}


def _api_label(config: dict) -> str:
    """推断"这是哪家的 API"：优先 api_base 主机名，其次显式标签，最后适配器名。

    为什么不能直接用 `config["provider"]`：那是**适配器名**（openai / gemini），
    用户接 DeepSeek 时它也叫 openai，面板上看着就错了。
    """
    cfg = config or {}
    explicit = str(cfg.get("api_name") or cfg.get("preset_name") or "").strip()
    if explicit:
        return explicit
    base = str(cfg.get("api_base") or "").lower()
    if base:
        for host, alias in _API_HOST_ALIAS.items():
            if host in base:
                return alias
    return str(cfg.get("provider") or cfg.get("provider_type") or "")


def _api_model(config: dict, model: Any = None) -> tuple[str, str]:
    """从 provider 配置里取 `(api, model)`。"""
    cfg = config or {}
    mdl = str(model or cfg.get("model") or "")
    return _api_label(cfg), mdl


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

    async def _recorded_stream(self, stream_fn: Any, messages: list[dict], **kwargs: Any) -> Any:
        """把一次（流式）调用的 usage 收集起来，结束后记一条。

        `chat_stream` 与 `stream` 共用这段：上游 usage 通常在**最后一帧**，
        中途 break 时可能拿不到 —— 那种情况记 `estimated=1`，不假装精确。
        """
        from app.agent.providers.base import merge_usage

        started = time.monotonic()
        model = kwargs.get("model")
        usage: dict = {}
        ok = True
        err_kind = ""
        try:
            async for event in stream_fn(messages, **kwargs):
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
                estimated=not usage,
            )

    async def chat_stream(self, messages: list[dict], **kwargs: Any) -> Any:
        """流式对话入口（`iter_stream_with_fallback` 用的就是这个方法名）。"""
        async for event in self._recorded_stream(self._inner.chat_stream, messages, **kwargs):
            yield event

    async def stream(self, messages: list[dict], **kwargs: Any) -> Any:
        """另一套流式入口（部分调用方用 `stream`）。"""
        async for event in self._recorded_stream(self._inner.stream, messages, **kwargs):
            yield event


def wrap_metered(provider: Any) -> Any:
    """给 provider 套计量代理（幂等：已包过就不再包）。"""
    if isinstance(provider, MeteredProvider):
        return provider
    if get_recorder() is None:
        return provider
    return MeteredProvider(provider)


__all__ = ["MeteredProvider", "wrap_metered"]
