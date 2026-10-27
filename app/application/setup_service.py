"""首启向导的后端动作：把"填一次 Key 就能用"压成一次调用。

第一步只做 ``quick-model``：一次完成「建连接预设 → 建模型 → 加入 Agent 模型池」。

为什么要它：现有流程要建预设、再建模型、再回 Agent 页"添加模型"进池——三次跳页，
而任意一步漏掉都表现为"机器人不回话"（模型池为空）。向导把这三步做成一个事务性动作，
失败时给出可读原因，成功时返回最终入池结果。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


class SetupError(ValueError):
    """向导动作失败（消息可直接展示给用户）。"""


class SetupService:
    def __init__(
        self,
        config_service: Any,
        agent_manager: Any,
        provider_preset_service: Any,
        provider_model_service: Any,
    ) -> None:
        self.config_service = config_service
        self.agent_manager = agent_manager
        self.provider_preset_service = provider_preset_service
        self.provider_model_service = provider_model_service

    # ---------- 内部 ----------

    @staticmethod
    def _normalize_base(api_base: str) -> str:
        return str(api_base or "").strip().rstrip("/")

    def _find_preset(self, provider: str, api_base: str) -> dict | None:
        target = self._normalize_base(api_base)
        for preset in self.config_service.list_provider_presets():
            config = preset.get("config") or {}
            if str(preset.get("provider") or "") != provider:
                continue
            if self._normalize_base(config.get("api_base", "")) == target:
                return preset
        return None

    def _runtime(self, account_id: str):
        runtime = None
        if self.agent_manager is not None:
            try:
                runtime = self.agent_manager.get_runtime(account_id)
                if runtime is None:
                    runtime = self.agent_manager.ensure_runtime(account_id)
            except Exception as e:  # noqa: BLE001 —— 账号不存在等按可读错误抛出
                raise SetupError(f"账号 {account_id} 没有可用的 Agent 运行时: {e}") from e
        if runtime is None:
            raise SetupError(f"账号 {account_id} 没有可用的 Agent 运行时")
        return runtime

    # ---------- 向导动作 ----------

    async def quick_model(
        self,
        account_id: str,
        *,
        provider: str = "openai",
        api_base: str = "",
        api_key: str = "",
        model: str = "",
        preset_name: str = "",
    ) -> dict:
        """一次完成「连接预设 + 模型 + 加入模型池」。

        幂等：同 provider + api_base 复用已有预设；同预设下同名模型复用已有模型。
        """
        account_id = str(account_id or "").strip()
        if not account_id:
            raise SetupError("缺少账号")
        provider = str(provider or "openai").strip() or "openai"
        api_base = self._normalize_base(api_base)
        model = str(model or "").strip()
        if not api_base:
            raise SetupError("请填写 API 基础 URL")
        if not api_base.startswith(("http://", "https://")):
            raise SetupError("API 基础 URL 需要以 http:// 或 https:// 开头，例如 https://api.deepseek.com/v1")
        if not model:
            raise SetupError("请选择或填写模型名称")

        # 1) 连接预设：同 provider + api_base 复用，否则新建
        preset = self._find_preset(provider, api_base)
        if preset is None:
            display = str(preset_name or "").strip()
            if not display:
                host = urlsplit(api_base).hostname or api_base
                display = f"{provider} · {host}"
            preset = await self.provider_preset_service.create_preset({
                "name": display,
                "provider": provider,
                "config": {"api_base": api_base, "api_key": api_key},
            })
        elif api_key:
            # 复用时只更新密钥（用户可能换了 Key），不动名称
            await self.provider_preset_service.update_preset(
                str(preset["id"]), {"config": {"api_key": api_key}}
            )
        preset_id = str(preset["id"])

        # 2) 模型：同预设下同名复用
        model_id = ""
        for row in self.config_service.list_provider_models(preset_id):
            if str(row.get("model") or "") == model:
                model_id = str(row["id"])
                break
        if not model_id:
            created = await self.provider_model_service.create_model(preset_id, {"model": model})
            model_id = str(created["id"])

        # 3) 加入 Agent 模型池（这一步漏掉 = 模型建了但 Agent 不会用）
        runtime = self._runtime(account_id)
        config = runtime.config
        pool = [str(item) for item in (config.get("provider_model_pool") or []) if str(item)]
        if model_id not in pool:
            pool.append(model_id)
        config.set("provider_model_pool", pool)
        config.set("provider_model_id", model_id)
        config.set("provider_preset_id", preset_id)
        config.save()

        return {
            "account_id": account_id,
            "preset_id": preset_id,
            "preset_name": str(preset.get("name") or ""),
            "model_id": model_id,
            "model": model,
            "pool": pool,
        }
