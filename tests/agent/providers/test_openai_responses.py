"""OpenAI Responses API 适配器：端点 / 请求转换 / 响应解析 / 流式事件。"""

from __future__ import annotations

from app.agent.providers import get_provider_class, normalize_provider_type
from app.agent.providers.base import StreamEvent
from app.agent.providers.openai_responses import (
    OpenAIResponsesProvider,
    parse_responses_result,
    stream_events_from_responses,
    to_responses_input,
    to_responses_tools,
)

MESSAGES = [
    {"role": "system", "content": "你是助手"},
    {"role": "user", "content": "你好"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q": "x"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "结果 42"},
]


def test_registered_with_aliases():
    assert get_provider_class("openai-responses") is OpenAIResponsesProvider
    assert normalize_provider_type("responses") == "openai-responses"
    assert normalize_provider_type("openai_responses") == "openai-responses"


def test_endpoint_normalization():
    p1 = OpenAIResponsesProvider({"api_key": "k", "api_base": "https://api.openai.com/v1"})
    assert p1._endpoint() == "https://api.openai.com/v1/responses"

    p2 = OpenAIResponsesProvider({"api_key": "k", "api_base": "https://api.openai.com/v1/responses"})
    assert p2._endpoint() == "https://api.openai.com/v1/responses"

    p3 = OpenAIResponsesProvider({"api_key": "k", "api_base": "https://api.openai.com"})
    assert p3._endpoint() == "https://api.openai.com/v1/responses"

    # 版本化基地址（Z.AI）保持原样，不再补 /v1
    p4 = OpenAIResponsesProvider({"api_key": "k", "api_base": "https://api.z.ai/api/coding/paas/v4"})
    assert p4._endpoint() == "https://api.z.ai/api/coding/paas/v4/responses"


def test_message_conversion_to_input_items():
    items, instructions = to_responses_input(MESSAGES)

    assert instructions == "你是助手"
    assert items[0] == {"role": "user", "content": "你好"}
    assert items[1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "lookup",
        "arguments": '{"q": "x"}',
    }
    assert items[2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "结果 42",
    }


def test_user_image_block_becomes_input_image():
    items, _ = to_responses_input([
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    ])
    blocks = items[0]["content"]
    assert blocks[0] == {"type": "input_text", "text": "看图"}
    assert blocks[1] == {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}


def test_tools_conversion_is_flat():
    converted = to_responses_tools([
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "查资料",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }
    ])
    assert converted == [
        {
            "type": "function",
            "name": "lookup",
            "description": "查资料",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
    ]


def test_payload_shape_keeps_store_false_and_output_floor():
    provider = OpenAIResponsesProvider({
        "api_key": "k",
        "api_base": "https://api.openai.com/v1",
        "extra_body": {"reasoning": {"effort": "low"}},
    })
    payload = provider._build_payload(
        MESSAGES,
        model="gpt-5.2",
        temperature=0.3,
        max_tokens=8,
        tools=[{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
        stream=False,
    )

    assert payload["model"] == "gpt-5.2"
    assert payload["store"] is False
    assert payload["stream"] is False
    assert payload["temperature"] == 0.3
    assert payload["max_output_tokens"] == 16  # 低于 16 会被上游 400
    assert payload["instructions"] == "你是助手"
    assert payload["input"][0] == {"role": "user", "content": "你好"}
    assert payload["tools"][0]["name"] == "lookup"
    assert payload["reasoning"] == {"effort": "low"}


def test_non_stream_result_parsing():
    result = {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "想一下"}]},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "你好呀"}],
            },
            {"type": "function_call", "call_id": "call_9", "name": "lookup", "arguments": '{"q": "y"}'},
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 3,
            "total_tokens": 13,
            "input_tokens_details": {"cached_tokens": 4},
        },
    }
    response = parse_responses_result(result)

    assert response.text == "你好呀"
    assert response.reasoning == "想一下"
    assert response.tool_calls == [
        {
            "id": "call_9",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"q": "y"}'},
        }
    ]
    assert response.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "total_tokens": 13,
        "prompt_cache_hit_tokens": 4,
    }


async def test_chat_posts_responses_payload(monkeypatch):
    provider = OpenAIResponsesProvider({"api_key": "k", "api_base": "https://api.openai.com/v1"})
    captured: dict = {}

    async def fake_request(payload: dict, timeout: int) -> dict:
        captured["payload"] = payload
        return {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(provider, "_request", fake_request)
    response = await provider.chat([{"role": "user", "content": "hi"}], model="gpt-5.2", max_tokens=32)

    assert response.text == "ok"
    assert captured["payload"]["input"] == [{"role": "user", "content": "hi"}]
    assert captured["payload"]["stream"] is False


def test_stream_text_and_tool_call_events():
    state: dict = {}
    chunks = [
        {"type": "response.output_text.delta", "item_id": "msg_1", "delta": "你"},
        {"type": "response.output_text.delta", "item_id": "msg_1", "delta": "好"},
        {
            "type": "response.output_item.added",
            "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup", "arguments": ""},
        },
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"q": '},
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '"x"}'},
        {
            "type": "response.completed",
            "response": {
                "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
                "status": "completed",
            },
        },
    ]
    events: list[StreamEvent] = []
    for chunk in chunks:
        events.extend(stream_events_from_responses(chunk, state))

    assert "".join(event.text for event in events if event.type == "text") == "你好"

    fragments = [event.tool_call for event in events if event.type == "tool_call"]
    assert fragments[0]["index"] == 0
    assert fragments[0]["id"] == "call_1"
    assert fragments[0]["function"]["name"] == "lookup"
    assert "".join(fragment["function"]["arguments"] for fragment in fragments[1:]) == '{"q": "x"}'

    assert any(event.type == "usage" and event.usage["prompt_tokens"] == 5 for event in events)
    assert any(event.type == "done" and event.finish_reason == "stop" for event in events)


def test_stream_falls_back_to_done_payloads():
    """有的中转不逐字流式：正文与工具参数只在 done 里出现。"""
    state: dict = {}
    events: list[StreamEvent] = []
    events.extend(stream_events_from_responses({
        "type": "response.output_item.done",
        "item": {"type": "message", "id": "msg_1", "content": [{"type": "output_text", "text": "完整正文"}]},
    }, state))
    events.extend(stream_events_from_responses({
        "type": "response.output_item.done",
        "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup", "arguments": '{"q": "z"}'},
    }, state))

    assert "".join(event.text for event in events if event.type == "text") == "完整正文"
    fragments = [event.tool_call for event in events if event.type == "tool_call"]
    assert fragments[0]["function"]["name"] == "lookup"
    assert fragments[-1]["function"]["arguments"] == '{"q": "z"}'


def test_stream_error_and_incomplete_events():
    events = stream_events_from_responses({"type": "error", "message": "上游炸了"}, {})
    assert events and events[0].type == "error" and "上游炸了" in events[0].text

    state: dict = {}
    events = stream_events_from_responses({
        "type": "response.incomplete",
        "response": {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
    }, state)
    assert any(event.type == "done" and event.finish_reason == "length" for event in events)


async def test_chat_stream_passes_events_through(monkeypatch):
    provider = OpenAIResponsesProvider({"api_key": "k", "api_base": "https://api.openai.com/v1"})
    captured: dict = {}

    async def fake_stream(api_key: str, payload: dict, timeout: int):
        captured["payload"] = payload
        yield StreamEvent(type="text", text="hi")
        yield StreamEvent(type="done", finish_reason="stop")

    monkeypatch.setattr(provider, "_stream_responses", fake_stream)
    events = [event async for event in provider.chat_stream([{"role": "user", "content": "hi"}])]

    assert [event.type for event in events] == ["text", "done"]
    assert captured["payload"]["stream"] is True
