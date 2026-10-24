"""模块用的 LLM 客户端（``ctx.services.llm``）：一行调用框架 provider。

**为什么要有它**：模块想做一次模型请求（翻译 / 摘要 / 分类…）时，原先只能
``get_provider(dict(module.config.raw_config))`` 自己拼配置、自备 api_key —— 密钥散落进
模块配置、没有回退链、不计 telemetry。这里把「用哪个模型」与「怎么调」都留在框架：

```python
resp = await module.llm_chat([{"role": "user", "content": "把这句话翻成英文：你好"}])
# 或显式指定（model_id 通常来自配置里 type="resource" 的下拉选择）
model_id = module.config.get("model_id")
resp = await module.llm_chat(messages, model_id=model_id, system="你是翻译官")
```

解析顺序（``resolve()`` 可查看结果，已脱敏）：

1. 显式 ``model_id`` → ``ProviderRuntimeManager.resolve_provider_config``（连接预设 + 模型实例合并）；
2. 显式 ``preset_id`` → ``resolve_preset_config``；
3. 都没给 → 该账号 Agent 运行时的 ``provider_chain()``，即**账号自己配的模型池**
   （这就是"用系统的 provider"）。
"""

from __future__ import annotations

from app.core.logger import account_label

from collections.abc import AsyncIterator
from typing import Any

from app.agent import logger
from app.agent.providers.base import LLMResponse


class ModuleLLMClient:
    """模块 ↔ 框架的 LLM 调用门面（进程内单例，由容器注入）。"""

    def __init__(
        self,
        *,
        agent_manager: Any = None,
        runtime_manager: Any = None,
        model_service: Any = None,
        log: Any = None,
    ) -> None:
        self.agent_manager = agent_manager
        self.runtime_manager = runtime_manager
        self.model_service = model_service
        self.log = log or logger

    # ── 内部：账号运行时 / 日志 ──────────────────────────────────
    def _resolve_safely(self, kind: str, resource_id: str, *, bot_id: Any = None) -> dict | None:
        """解析模型/预设配置；解析异常只留痕并返回 None（回退账号模型池）。"""
        resolver = (
            self.runtime_manager.resolve_provider_config
            if kind == "model"
            else self.runtime_manager.resolve_preset_config
        )
        try:
            return resolver(resource_id) or None
        except Exception as e:  # noqa: BLE001 —— 坏配置不该让模块调用整体炸掉
            self._account_log(bot_id).warning(
                f"[ModuleLLM] 解析 provider {kind} 配置失败（id={resource_id}）: {e}"
            )
            return None

    def _runtime(self, bot_id: Any) -> Any:
        if self.agent_manager is None or bot_id is None:
            return None
        try:
            return self.agent_manager.get_runtime(bot_id)
        except Exception as e:  # noqa: BLE001 —— 运行时取不到按"没有 provider"处理
            logger.debug(f"[client] 68: 已忽略异常: {e}")
            return None

    def _account_log(self, bot_id: Any):
        return self.log.add_info(account_label(bot_id if bot_id is not None else '?'))

    # ── 解析 ────────────────────────────────────────────────────
    def _chain(self, *, bot_id: Any = None, model_id: str = "", preset_id: str = "") -> list[dict]:
        """按「model_id → preset_id → 账号模型池」的顺序解析出可用配置链。"""
        wanted_model = str(model_id or "").strip()
        wanted_preset = str(preset_id or "").strip()

        if wanted_model and self.runtime_manager is not None:
            config = self._resolve_safely("model", wanted_model, bot_id=bot_id)
            if config:
                return [config]
            self._account_log(bot_id).warning(
                f"[ModuleLLM] 指定的模型不可用（id={wanted_model}）：模型或所属预设不存在"
            )
        if wanted_preset and self.runtime_manager is not None:
            config = self._resolve_safely("preset", wanted_preset, bot_id=bot_id)
            if config:
                return [config]
            self._account_log(bot_id).warning(f"[ModuleLLM] 指定的 Provider 预设不存在（id={wanted_preset}）")

        runtime = self._runtime(bot_id)
        if runtime is not None:
            provider_chain = getattr(runtime, "provider_chain", None)
            if callable(provider_chain):
                chain = provider_chain() or []
                if chain:
                    return list(chain)
        return []

    def resolve(self, *, bot_id: Any = None, model_id: str = "", preset_id: str = "") -> dict:
        """诊断用：返回**脱敏**的解析结果（只说明用了谁，不含密钥）。"""
        chain = self._chain(bot_id=bot_id, model_id=model_id, preset_id=preset_id)
        rows = [
            {
                "provider": str(cfg.get("provider") or ""),
                "model": str(cfg.get("model") or ""),
                "api_base": str(cfg.get("api_base") or ""),
                "provider_model_id": str(cfg.get("provider_model_id") or ""),
                "provider_preset_id": str(cfg.get("provider_preset_id") or ""),
                "has_api_key": bool(cfg.get("api_key")),
            }
            for cfg in chain
        ]
        return {"count": len(rows), "chain": rows}

    def models(self, *, kind: str = "chat", preset_id: str | None = None) -> list[dict]:
        """可用模型清单（与资源注册表的 ``provider_model`` 同源），供模块自渲染选择器。

        ``kind`` 用于按能力类型过滤（``chat`` / ``embedding`` / ``rerank`` / ...；
        传 ``all`` 不过滤）。
        """
        if self.model_service is None:
            return []
        wanted = str(kind or "").strip()
        out = []
        for row in self.model_service.list_models(preset_id) or []:
            provider_type = str(row.get("provider_type") or "chat")
            if wanted and wanted != "all" and provider_type != wanted:
                continue
            name = str(row.get("name") or row.get("model") or row.get("id") or "")
            out.append({
                "id": str(row.get("id") or ""),
                "label": name,
                "model": str(row.get("model") or ""),
                "provider_type": provider_type,
                "preset_id": str(row.get("preset_id") or ""),
            })
        return out

    # ── 完整一轮对话 ────────────────────────────────────────────
    async def turn(
        self,
        user_text: str,
        *,
        session_id: str,
        bot_id: Any = None,
        is_private: bool | None = None,
        record_history: bool = False,
        stream: bool | None = None,
        on_sentence: Any = None,
        **kwargs: Any,
    ):
        """跑一轮**完整对话**（取值 + 组装 + 工具循环 + 记忆），返回 ``TurnResult``。

        与 ``chat`` 的区别：``chat`` 只发一次裸请求；``turn`` 吃框架的全部上下文能力，
        且**不需要真实事件对象** —— 主动消息 / 定时任务 / 模块自建循环都用它。

        Args:
            user_text: 本轮用户内容（可空）。
            session_id: ``group_x`` / ``private_y``。
            bot_id: 账号；缺省 None（取不到运行时时返回 error 结果，不抛）。
            is_private: 缺省按 ``session_id`` 前缀推断。
            record_history: 是否写进会话历史（默认 False）。
            stream / on_sentence: 流式与逐句回调。
            **kwargs: 透传给 ``TurnRequest``（如 ``user_id`` / ``group_id`` / ``kind``）。

        Returns:
            ``TurnResult``；无运行时返回 ``error="no_runtime"`` 且留痕。
        """
        from app.agent.loop.turn import TurnRequest, TurnResult, run_turn
        from app.domain.session import try_parse_session_id

        runtime = self._runtime(bot_id)
        if runtime is None:
            self._account_log(bot_id).warning(
                f"[ModuleLLM] 没有可用运行时，turn 已跳过（{session_id}）"
            )
            return TurnResult(session_id=session_id, error="no_runtime")

        if is_private is None:
            parsed = try_parse_session_id(session_id)
            is_private = not parsed[0] if parsed else False

        request = TurnRequest(
            session_id=session_id,
            is_private=bool(is_private),
            user_text=user_text,
            record_history=record_history,
            stream=stream,
            **kwargs,
        )
        return await run_turn(runtime, request, on_sentence=on_sentence)

    # ── 调用 ────────────────────────────────────────────────────
    @staticmethod
    def _prepare(messages: list[dict], system: str) -> list[dict]:
        """拷贝消息并（可选）在最前面补一条 system。"""
        prepared = [dict(m) for m in (messages or []) if isinstance(m, dict)]
        text = str(system or "").strip()
        if text and not (prepared and prepared[0].get("role") == "system"):
            prepared.insert(0, {"role": "system", "content": text})
        return prepared

    @staticmethod
    def _optional_kwargs(
        *,
        temperature: Any = None,
        max_tokens: Any = None,
        timeout: Any = None,
        tools: Any = None,
        tool_executor: Any = None,
        max_tool_rounds: Any = None,
        max_empty_retries: Any = None,
    ) -> dict:
        """只透传显式给出的参数：没给就让 provider 用自己配置里的值。"""
        kwargs: dict = {}
        if temperature is not None:
            kwargs["temperature"] = float(temperature)
        if max_tokens is not None:
            kwargs["max_tokens"] = int(max_tokens)
        if timeout is not None:
            kwargs["timeout"] = int(timeout)
        if tools is not None:
            kwargs["tools"] = tools
        if tool_executor is not None:
            kwargs["tool_executor"] = tool_executor
        if max_tool_rounds is not None:
            kwargs["max_tool_rounds"] = int(max_tool_rounds)
        if max_empty_retries is not None:
            kwargs["max_empty_retries"] = int(max_empty_retries)
        return kwargs

    async def chat(
        self,
        messages: list[dict],
        *,
        bot_id: Any = None,
        model_id: str = "",
        preset_id: str = "",
        system: str = "",
        temperature: Any = None,
        max_tokens: Any = None,
        timeout: Any = None,
        tools: Any = None,
        tool_executor: Any = None,
        max_tool_rounds: Any = None,
        max_empty_retries: Any = None,
        purpose: str = "module",
    ) -> LLMResponse:
        """非流式请求（含框架的多 provider 回退与空回复重试）。

        没有可用 provider 配置时返回空 ``LLMResponse``（``resp.ok`` 为假）并留痕，
        不抛异常 —— 模块据此走自己的降级分支。
        """
        from app.agent import providers  # 属性查找：便于 monkeypatch / 运行期替换

        chain = self._chain(bot_id=bot_id, model_id=model_id, preset_id=preset_id)
        if not chain:
            self._account_log(bot_id).warning(
                f"[ModuleLLM] 没有可用的 provider 配置（purpose={purpose}）："
                "请检查账号的 Agent 模型配置或传入 model_id"
            )
            return LLMResponse(text="", raw=None)
        kwargs = self._optional_kwargs(
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            tools=tools,
            tool_executor=tool_executor,
            max_tool_rounds=max_tool_rounds,
            max_empty_retries=max_empty_retries,
        )
        return await providers.chat_with_fallback(chain, self._prepare(messages, system), **kwargs)

    async def stream(
        self,
        messages: list[dict],
        *,
        bot_id: Any = None,
        model_id: str = "",
        preset_id: str = "",
        system: str = "",
        temperature: Any = None,
        max_tokens: Any = None,
        timeout: Any = None,
        tools: Any = None,
        tool_executor: Any = None,
        max_empty_retries: Any = None,
        usage_sink: dict | None = None,
        purpose: str = "module",
    ) -> AsyncIterator:
        """流式请求（逐个事件产出）。没有可用配置时一个事件都不产出。"""
        from app.agent import providers

        chain = self._chain(bot_id=bot_id, model_id=model_id, preset_id=preset_id)
        if not chain:
            self._account_log(bot_id).warning(
                f"[ModuleLLM] 没有可用的 provider 配置（purpose={purpose}）：流式请求已跳过"
            )
            return
        kwargs = self._optional_kwargs(
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            tools=tools,
            tool_executor=tool_executor,
            max_empty_retries=max_empty_retries,
        )
        if usage_sink is not None:
            kwargs["usage_sink"] = usage_sink
        async for event in providers.iter_stream_with_fallback(chain, self._prepare(messages, system), **kwargs):
            yield event
