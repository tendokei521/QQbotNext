"""Tavily 系统级工具基础测试。"""

from types import SimpleNamespace

import pytest

from app.llm import tavily_search as tavily_mod
from app.llm.tavily_search import _format_result, build_tavily_tool


def test_format_result_with_answer_and_results():
    data = {
        "answer": "这是答案",
        "results": [
            {"title": "标题A", "url": "https://a.example", "content": "摘要A", "score": 0.98},
            {"title": "标题B", "url": "https://b.example", "content": "摘要B", "score": 0.75},
        ],
    }
    text = _format_result(data, max_chars=2000)
    assert "答案：这是答案" in text
    assert "标题A" in text
    assert "https://a.example" in text
    assert "相关度: 0.980" in text


def test_format_result_empty():
    text = _format_result({"answer": "", "results": []}, max_chars=2000)
    assert text == "未找到相关搜索结果。"
    text = _format_result({"answer": "孤答案", "results": []}, max_chars=2000)
    assert "答案：孤答案" in text
    assert "未找到相关搜索结果" in text


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
    assert result == "error: 未配置 Tavily API Key"


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
