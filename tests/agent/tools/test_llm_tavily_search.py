"""Tavily 系统级工具基础测试。"""

from types import SimpleNamespace

from app.agent.tools import web_search as tavily_mod
from app.agent.tools.web_search import build_tavily_tool


async def test_tool_requires_api_key():
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            get=lambda key, default=None: {
                "tavily_api_key": "",
                "tavily_max_results": 5,
                "tavily_search_depth": "basic",
                "tavily_max_content_chars": 2000,
            }.get(key, default)
        )
    )
    spec = build_tavily_tool(runtime)
    assert spec.name == "tavily_search"
    result = await spec.handler(None, {"query": "test"})
    assert "未配置" in result and "Tavily" in result


async def test_handler_calls_search_and_formats(monkeypatch):
    async def fake_search(api_key, query, *, max_results=5, search_depth="basic", include_answer=True):
        assert api_key == "tvly-test"
        assert query == "python 教程"
        assert max_results == 3
        assert search_depth == "basic"
        return {
            "answer": "找到答案",
            "results": [{"title": "Python", "url": "https://python.org", "content": "教程内容"}],
        }

    monkeypatch.setattr(tavily_mod, "_tavily_search", fake_search)
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            get=lambda key, default=None: {
                "tavily_api_key": "tvly-test",
                "tavily_max_results": 5,
                "tavily_search_depth": "basic",
                "tavily_max_content_chars": 2000,
            }.get(key, default)
        )
    )
    spec = build_tavily_tool(runtime)
    result = await spec.handler(None, {"query": "python 教程", "max_results": 3})
    assert "找到答案" in result
    assert "https://python.org" in result
    assert "Python" in result
