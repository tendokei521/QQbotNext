"""可选 embedding 客户端（设计契约 §5.3）：把文本批量转成向量，供混合召回使用。

零依赖原则：没有配 embedding 模型 / 没有密钥 / 没有 runtime 时，本模块**不抛异常**，
``available()`` 返回 False、``embed()`` 返回空列表（``logger.debug`` 留痕），调用方
自动退化为纯词法召回，绝不阻断聊天。

职责只有三件：
1. 解析「该用哪个 embedding 模型」：显式 ``model_id`` → 服务层的 embedding 模型清单
   → runtime 配置服务的模型清单；
2. 用 ``app.agent.providers.get_provider(config)`` 拿到 ``OpenAIEmbeddingProvider``，
   按 batch ≤ 16 调 ``embed``，逐批校验返回数量，宁可整批失败也不回写错位向量；
3. 实例内按文本做 LRU 缓存（最多 256 条），相同文本不重复请求、不重复计费。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from app.agent import logger
from app.core.logger import account_label

#: 单次请求的文本条数上限（多数 embedding 接口对 batch 有硬限制）
_BATCH_SIZE = 16
#: 实例内文本 → 向量的 LRU 缓存上限
_CACHE_MAX = 256


class EmbeddingClient:
    """embedding 门面：懒解析 provider + 批量向量化 + 文本级 LRU 缓存。

    典型用法::

        client = EmbeddingClient(runtime=runtime, services=services, bot_id=bot_id,
                                 model_id=cfg.get("memory_embedding_model_id", ""))
        if await client.available():
            vectors = await client.embed(["我喜欢喝美式", "他在北京"])
    """

    def __init__(
        self,
        *,
        runtime: Any = None,
        services: Any = None,
        bot_id: Any = "",
        model_id: str = "",
    ) -> None:
        self.runtime = runtime
        self.services = services
        self.bot_id = bot_id
        self.model_id = str(model_id or "").strip()
        #: 解析到的模型名（describe 用）
        self.model = ""
        #: 向量维度（配置声明或首次成功请求后学到）
        self.dim = 0
        self._reason = "尚未解析"
        self._provider: Any = None
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    # ── 日志 ────────────────────────────────────────────────
    def _log(self):
        return logger.add_info(account_label(self.bot_id))

    def _set_reason(self, reason: str) -> None:
        """记录不可用原因；只在原因变化时 debug 留痕，避免每次召回刷屏。"""
        if reason != self._reason:
            self._log().debug(f"[记忆] embedding 不可用: {reason}")
        self._reason = reason

    # ── 模型解析 ────────────────────────────────────────────
    def _service_model_rows(self) -> list[dict]:
        """服务层的 provider 模型清单（取不到按空列表降级）。"""
        service = getattr(self.services, "provider_models", None)
        lister = getattr(service, "list_models", None)
        if not callable(lister):
            return []
        try:
            return [row for row in (lister() or []) if isinstance(row, dict)]
        except Exception as e:  # noqa: BLE001 —— 取清单失败按"没有模型"降级
            self._log().debug(f"[记忆] 读取 embedding 模型清单失败（忽略）: {e}")
            return []

    def _config_model_rows(self) -> list[dict]:
        """runtime 配置服务的 provider 模型清单（取不到按空列表降级）。"""
        config_service = getattr(self.runtime, "config_service", None)
        lister = getattr(config_service, "list_provider_models", None)
        if not callable(lister):
            return []
        try:
            return [row for row in (lister() or []) if isinstance(row, dict)]
        except Exception as e:  # noqa: BLE001 —— 同上，纯降级路径
            self._log().debug(f"[记忆] 读取 provider 模型配置失败（忽略）: {e}")
            return []

    def _first_embedding_model_id(self) -> str:
        """按契约顺序找第一个启用中的 embedding 模型 id（服务层优先）。"""
        for rows in (self._service_model_rows(), self._config_model_rows()):
            for row in rows:
                if self._is_embedding_model(row):
                    model_id = str(row.get("id") or "").strip()
                    if model_id:
                        return model_id
        return ""

    @staticmethod
    def _is_embedding_model(row: dict) -> bool:
        """是否是**启用中**的 embedding 能力模型。"""
        return (
            str(row.get("provider_type") or "").strip().lower() == "embedding"
            and bool(row.get("enabled", True))
        )

    @staticmethod
    def _has_credentials(config: dict) -> bool:
        """是否具备调用凭证：api_key / key 列表 / 自定义鉴权头，任一非空即可。"""
        for key in ("api_key", "key", "token"):
            value = config.get(key)
            if isinstance(value, (list, tuple)):
                if any(str(item or "").strip() for item in value):
                    return True
            elif str(value or "").strip():
                return True
        for key in ("extra_headers", "headers"):
            value = config.get(key)
            if isinstance(value, dict) and value:
                return True
        return False

    @staticmethod
    def _dim_from_config(config: dict) -> int:
        """从模型配置里读声明的向量维度（没有返回 0，等首次请求学到）。"""
        for key in ("dim", "dimensions", "embedding_dim", "dimension"):
            value = config.get(key)
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                return number
        return 0

    def _resolve_config(self) -> dict | None:
        """按契约顺序解析 provider 配置；解析不到返回 None 并记录原因。"""
        manager = getattr(self.runtime, "provider_runtime_manager", None)
        resolver = getattr(manager, "resolve_provider_config", None)
        if not callable(resolver):
            self._set_reason("未配置 Provider 运行时")
            return None

        model_id = self.model_id
        if not model_id:
            model_id = self._first_embedding_model_id()
        if not model_id:
            self._set_reason("未找到可用的 embedding 模型")
            return None

        try:
            config = resolver(model_id)
        except Exception as e:  # noqa: BLE001 —— 坏配置只降级，不影响聊天
            self._log().warning(f"[记忆] 解析 embedding 模型配置失败（id={model_id}）: {e}")
            self._set_reason(f"解析模型配置异常: {e}")
            return None
        if not isinstance(config, dict) or not config:
            self._set_reason(f"模型配置为空（id={model_id}）")
            return None
        if not self._has_credentials(config):
            self._set_reason("embedding 模型缺少 api_key")
            return None
        if not str(config.get("model") or "").strip():
            self._set_reason("embedding 模型名为空")
            return None
        self.model = str(config.get("model") or "").strip()
        return dict(config)

    def _ensure_provider(self) -> Any:
        """懒解析并缓存 provider；不可用返回 None（不抛异常）。"""
        if self._provider is not None:
            return self._provider
        config = self._resolve_config()
        if config is None:
            return None
        try:
            from app.agent import providers  # 属性查找：便于测试 monkeypatch

            provider = providers.get_provider(config)
        except Exception as e:  # noqa: BLE001 —— Provider 建不出来就退化词法召回
            self._log().warning(f"[记忆] 创建 embedding Provider 失败: {e}")
            self._set_reason(f"创建 Provider 失败: {e}")
            return None
        if not callable(getattr(provider, "embed", None)):
            self._set_reason("Provider 不支持 embedding")
            return None
        self._provider = provider
        self._reason = "可用"
        if not self.dim:
            self.dim = self._dim_from_config(config)
        return provider

    # ── 对外接口 ────────────────────────────────────────────
    async def available(self) -> bool:
        """当前是否真的能调 embedding（解析成功且 provider 有鉴权）。"""
        return self._ensure_provider() is not None

    def describe(self) -> dict:
        """诊断视图：``{"enabled", "model", "dim", "reason"}``（不含密钥）。"""
        enabled = self._ensure_provider() is not None
        return {
            "enabled": enabled,
            "model": self.model,
            "dim": int(self.dim or 0),
            "reason": self._reason,
        }

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化；任何失败返回 ``[]``（调用方据此退化为词法召回）。

        保持入参顺序与条数；重复文本只请求一次（LRU 缓存命中）。
        """
        items = [str(text if text is not None else "") for text in (texts or [])]
        if not items:
            return []
        pending: list[str] = []
        for text in items:
            if text in self._cache:
                self._cache.move_to_end(text)
            elif text not in pending:
                pending.append(text)

        if pending:
            provider = self._ensure_provider()
            if provider is None:
                return []
            for start in range(0, len(pending), _BATCH_SIZE):
                batch = pending[start:start + _BATCH_SIZE]
                vectors = await self._request(provider, batch)
                if vectors is None:
                    return []
                for text, vector in zip(batch, vectors, strict=False):
                    self._remember(text, vector)

        result: list[list[float]] = []
        for text in items:
            vector = self._cache.get(text)
            if not vector:
                self._set_reason("缓存缺少向量（请求结果不完整）")
                return []
            self._cache.move_to_end(text)
            result.append(list(vector))
        return result

    async def embed_one(self, text: str) -> list[float] | None:
        """单条向量化；不可用 / 失败返回 None。"""
        vectors = await self.embed([text])
        return vectors[0] if vectors else None

    # ── 内部：请求与缓存 ────────────────────────────────────
    async def _request(self, provider: Any, batch: list[str]) -> list[list[float]] | None:
        """发一批请求；失败或数量不匹配返回 None（不写坏向量）。"""
        try:
            vectors = await provider.embed(list(batch))
        except Exception as e:  # noqa: BLE001 —— 失败当轮退化词法，不回写
            self._log().debug(f"[记忆] embedding 请求失败（本轮退化词法召回）: {e}")
            self._set_reason(f"请求失败: {e}")
            return None
        if not isinstance(vectors, list) or len(vectors) != len(batch):
            got = len(vectors) if isinstance(vectors, list) else "非法返回值"
            self._log().debug(f"[记忆] embedding 返回数量不匹配（{got} != {len(batch)}）")
            self._set_reason("embedding 返回数量不匹配")
            return None
        result: list[list[float]] = []
        try:
            for vector in vectors:
                floats = [float(value) for value in (vector or [])]
                if not floats:
                    self._set_reason("embedding 返回空向量")
                    return None
                result.append(floats)
        except (TypeError, ValueError) as e:
            self._log().debug(f"[记忆] embedding 返回值不是数值数组（忽略）: {e}")
            self._set_reason("embedding 返回值非法")
            return None
        if result and not self.dim:
            self.dim = len(result[0])
        return result

    def _remember(self, text: str, vector: list[float]) -> None:
        """写入缓存并按 LRU 淘汰（最多 ``_CACHE_MAX`` 条）。"""
        self._cache[text] = list(vector)
        self._cache.move_to_end(text)
        while len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)
