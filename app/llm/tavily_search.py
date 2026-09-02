"""Tavily 联网搜索工具（系统级，不进入 NapCat 前端清单）。

- 通过 Tavily Search API 提供 LLM 联网搜索能力；
- 只生成一个 Tavily ToolSpec，由 ``chat._collect_llm_ext`` 按配置追加；
- API Key 存放在 Agent 配置中，使用 password 类型脱敏，不写入 system prompt。
"""

from __future__ import annotations

from typing import Any

import aiohttp

from app.llm.tool import ToolSpec

TAVILY_ENDPOINT = "https://api.tavily.com/search"
DEFAULT_TIMEOUT = 20
DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_CONTENT_CHARS = 2000
DEFAULT_SEARCH_DEPTH = "basic"


def _parse_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(n, maximum))


async def _tavily_search(
    api_key: str,
    query: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
    search_depth: str = DEFAULT_SEARCH_DEPTH,
    include_answer: bool = True,
) -> dict | None:
    """调用 Tavily Search API，返回原始 JSON；失败返回 None。"""
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": include_answer,
        "include_raw_content": False,
        "include_images": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            TAVILY_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                text = (await resp.text() or "")[:300]
                raise RuntimeError(f"Tavily HTTP {resp.status}: {text}")
            return await resp.json()


def _format_result(data: dict, max_chars: int = DEFAULT_MAX_CONTENT_CHARS) -> str:
    """把 Tavily 响应整理成 LLM 易读的文本。"""
    answer = str(data.get("answer") or "").strip()
    results = data.get("results") or []
    lines: list[str] = []

    if answer:
        lines.append(f"答案：{answer}")

    if not results:
        lines.append("未找到相关搜索结果。")
        text = "\n".join(lines)
        if len(text) > max_chars:
            return text[:max_chars] + "\n…(结果过长已截断)"
        return text

    lines.append("搜索结果：")
    for index, item in enumerate(results, 1):
        title = str(item.get("title") or "无标题")
        url = str(item.get("url") or "")
        content = str(item.get("content") or "").strip()
        score = item.get("score")
        lines.append(f"{index}. {title}")
        if url:
            lines.append(f"   URL: {url}")
        if score is not None:
            try:
                lines.append(f"   相关度: {float(score):.3f}")
            except (TypeError, ValueError):
                pass
        if content:
            lines.append(f"   摘要: {content[:300]}")

    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[:max_chars] + "\n…(结果过长已截断)"
    return text


def build_tavily_tool(runtime: Any) -> ToolSpec:
    """构造系统级 tavily_search 工具。"""

    def _cfg(key: str, default: Any = None) -> Any:
        try:
            return runtime.config.get(key, default)
        except Exception:
            return default

    async def _handler(ctx, args: dict) -> str:
        api_key = str(_cfg("tavily_api_key", "") or "").strip()
        if not api_key:
            return "error: 未配置 Tavily API Key"

        query = str(args.get("query") or "").strip()
        if not query:
            return "error: query 不能为空"

        max_results = _parse_int(
            args.get("max_results") or _cfg("tavily_max_results", 5),
            default=DEFAULT_MAX_RESULTS,
            minimum=1,
            maximum=20,
        )
        search_depth = str(args.get("search_depth") or _cfg("tavily_search_depth", DEFAULT_SEARCH_DEPTH) or "").lower()
        if search_depth not in ("basic", "advanced"):
            search_depth = DEFAULT_SEARCH_DEPTH
        max_chars = _parse_int(
            _cfg("tavily_max_content_chars", DEFAULT_MAX_CONTENT_CHARS),
            default=DEFAULT_MAX_CONTENT_CHARS,
            minimum=500,
            maximum=20000,
        )

        try:
            data = await _tavily_search(
                api_key,
                query,
                max_results=max_results,
                search_depth=search_depth,
                include_answer=True,
            )
        except Exception as e:  # noqa: BLE001 - 外部 API 异常以可读文本返回
            return f"error: Tavily 搜索失败: {e}"

        if data is None:
            return "error: Tavily 搜索失败（无响应）"
        return _format_result(data, max_chars=max_chars)

    return ToolSpec(
        name="tavily_search",
        description=(
            "联网搜索工具，用于回答当前时间点/实时信息、模型不确定的新事件、"
            "需要外部资料支撑的问题。当用户询问实时新闻、最新动态、网络资料、"
            "技术文档、事实核查等明显需要联网的内容时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要搜索的问题或关键词，可以包含时间、领域等上下文。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "返回条数 1~20，默认 5。",
                },
                "search_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "description": "basic 速度快成本低；advanced 结果更准且支持更多内容，默认 basic。",
                },
            },
            "required": ["query"],
        },
        handler=_handler,
        permission="member",
        scopes=("*",),
        module=None,
    )
