import importlib
import sys

import pytest

import mp_agent.application.primary_agent as primary_agent
from mp_agent.application.primary_agent import (
    DASHSCOPE_BASE_URL,
    DASHSCOPE_MODEL,
    build_primary_agent_client,
    decide_next_step,
    summarize_workflow_result,
)
from mp_agent.application.session_store import ChatMessage, SessionSlots


def test_build_primary_agent_client_requires_dashscope_api_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    try:
        build_primary_agent_client()
    except RuntimeError as exc:
        assert "DASHSCOPE_API_KEY" in str(exc)
    else:
        raise AssertionError("RuntimeError was not raised")


def test_build_primary_agent_client_raises_runtime_error_before_openai_import_failure(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "openai", None)

    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        build_primary_agent_client()


def test_primary_agent_constants_match_dashscope_configuration():
    assert DASHSCOPE_BASE_URL == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert DASHSCOPE_MODEL == "glm-4.6"


def test_primary_agent_can_be_reloaded_without_openai_sdk_loaded(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)

    module = importlib.reload(importlib.import_module("mp_agent.application.primary_agent"))

    assert module.DASHSCOPE_BASE_URL == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert module.DASHSCOPE_MODEL == "glm-4.6"


def test_decide_next_step_returns_follow_up_question_when_slots_missing():
    def fake_llm(messages, tools):
        assert tools[0]["function"]["name"] == "run_amazon_competitor_analysis"
        assert any(message["role"] == "user" for message in messages)
        return {
            "type": "assistant",
            "message": "你想分析哪个平台？目前我支持 Amazon。",
            "slot_updates": {"brand": "Blackview"},
        }

    decision = decide_next_step(
        messages=[ChatMessage(role="user", content="帮我看一下 Blackview 的竞品")],
        slots=SessionSlots(),
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": "run_amazon_competitor_analysis"},
            }
        ],
        llm_call=fake_llm,
    )

    assert decision == {
        "type": "assistant",
        "message": "你想分析哪个平台？目前我支持 Amazon。",
        "slot_updates": {"brand": "Blackview"},
    }


def test_decide_next_step_runs_immediately_when_user_messages_already_contain_platform_brand_and_count():
    def fake_llm(_messages, _tools):
        return {
            "type": "tool_call",
            "tool_name": "run_amazon_competitor_analysis",
            "arguments": {"brand": " Blackview ", "count": "5"},
            "assistant_message": "好的，我开始分析 Amazon 上的 Blackview 竞品。",
            "slot_updates": {"platform": "amazon", "brand": " Blackview ", "count": "5"},
        }

    decision = decide_next_step(
        messages=[ChatMessage(role="user", content="看 Amazon 的 Blackview，5 个")],
        slots=SessionSlots(),
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": "run_amazon_competitor_analysis"},
            }
        ],
        llm_call=fake_llm,
    )

    assert decision["type"] == "tool_call"
    assert decision["tool_name"] == "run_amazon_competitor_analysis"
    assert decision["arguments"] == {"brand": "Blackview", "count": 5}
    assert decision["slot_updates"] == {
        "platform": "amazon",
        "brand": "Blackview",
        "count": 5,
    }


def test_decide_next_step_runs_after_user_supplies_missing_count_in_later_turn():
    def fake_llm(_messages, _tools):
        return {
            "type": "tool_call",
            "tool_name": "run_amazon_competitor_analysis",
            "arguments": {},
            "assistant_message": "",
            "slot_updates": {"platform": "amazon", "brand": "Blackview", "count": "5"},
        }

    decision = decide_next_step(
        messages=[
            ChatMessage(role="user", content="看 Amazon 的 Blackview"),
            ChatMessage(role="assistant", content="请告诉我要分析多少个竞品。"),
            ChatMessage(role="user", content="5个"),
        ],
        slots=SessionSlots(),
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": "run_amazon_competitor_analysis"},
            }
        ],
        llm_call=fake_llm,
    )

    assert decision["type"] == "tool_call"
    assert decision["tool_name"] == "run_amazon_competitor_analysis"
    assert decision["arguments"] == {"brand": "Blackview", "count": 5}
    assert decision["slot_updates"] == {
        "platform": "amazon",
        "brand": "Blackview",
        "count": 5,
    }


def test_decide_next_step_blocks_tool_call_when_llm_has_not_provided_required_slots():
    def fake_llm(_messages, _tools):
        return {
            "type": "tool_call",
            "tool_name": "run_amazon_competitor_analysis",
            "arguments": {},
            "assistant_message": "我来帮你分析。",
            "slot_updates": {"platform": "amazon"},
        }

    decision = decide_next_step(
        messages=[ChatMessage(role="user", content="看 Amazon 的竞品")],
        slots=SessionSlots(),
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": "run_amazon_competitor_analysis"},
            }
        ],
        llm_call=fake_llm,
    )

    assert decision == {
        "type": "assistant",
        "message": "请提供有效的品牌和数量后再试。",
        "slot_updates": {"platform": "amazon"},
    }


def test_decide_next_step_falls_back_when_tool_call_arguments_are_unusable():
    def fake_llm(_messages, _tools):
        return {
            "type": "tool_call",
            "tool_name": "run_amazon_competitor_analysis",
            "arguments": {"brand": " ", "count": "many"},
            "slot_updates": {"platform": "amazon"},
        }

    decision = decide_next_step(
        messages=[ChatMessage(role="user", content="看 Amazon 的竞品")],
        slots=SessionSlots(),
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": "run_amazon_competitor_analysis"},
            }
        ],
        llm_call=fake_llm,
    )

    assert decision == {
        "type": "assistant",
        "message": "请提供有效的品牌和数量后再试。",
        "slot_updates": {"platform": "amazon"},
    }


def test_decide_next_step_preserves_assistant_slot_updates_from_llm():
    def fake_llm(_messages, _tools):
        return {
            "type": "assistant",
            "message": "我先确认一下品牌和数量。",
            "slot_updates": {"brand": "Blackview"},
        }

    decision = decide_next_step(
        messages=[ChatMessage(role="user", content="看 Amazon 的 Blackview，5 个")],
        slots=SessionSlots(),
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": "run_amazon_competitor_analysis"},
            }
        ],
        llm_call=fake_llm,
    )

    assert decision == {
        "type": "assistant",
        "message": "我先确认一下品牌和数量。",
        "slot_updates": {"brand": "Blackview"},
    }


@pytest.mark.parametrize(
    ("user_content", "expected_brand"),
    [
        ("看 Amazon 的 iPhone 16，5 个", "iPhone 16"),
        ("看 Amazon 的 Blackview BV9300，5 个", "Blackview BV9300"),
    ],
)
def test_decide_next_step_normalizes_incoming_slot_updates_for_quantity_examples(
    user_content, expected_brand
):
    def fake_llm(_messages, _tools):
        return {
            "type": "assistant",
            "message": "我先确认一下品牌和数量。",
            "slot_updates": {"platform": "amazon", "brand": f" {expected_brand} ", "count": "5"},
        }

    decision = decide_next_step(
        messages=[ChatMessage(role="user", content=user_content)],
        slots=SessionSlots(),
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": "run_amazon_competitor_analysis"},
            }
        ],
        llm_call=fake_llm,
    )

    assert decision["slot_updates"]["brand"] == expected_brand
    assert decision["slot_updates"]["count"] == 5
    assert decision["slot_updates"]["platform"] == "amazon"


def test_decide_next_step_rejects_unsupported_tool_call():
    def fake_llm(_messages, _tools):
        return {
            "type": "tool_call",
            "tool_name": "run_temu_competitor_analysis",
            "arguments": {"brand": "Blackview", "count": 5},
            "slot_updates": {
                "platform": "temu",
                "brand": "Blackview",
                "count": 5,
            },
        }

    decision = decide_next_step(
        messages=[ChatMessage(role="user", content="看 Temu 的 Blackview，5 个")],
        slots=SessionSlots(),
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": "run_amazon_competitor_analysis"},
            }
        ],
        llm_call=fake_llm,
    )

    assert decision == {
        "type": "assistant",
        "message": "目前只支持 Amazon、eBay、Temu、OZON 竞品分析，请改用其中一个平台。",
        "slot_updates": {
            "platform": "temu",
            "brand": "Blackview",
            "count": 5,
        },
    }


def test_summarize_workflow_result_returns_final_assistant_copy():
    def fake_llm(messages, tools):
        assert tools == []
        assert any(message["role"] == "user" for message in messages)
        return {
            "type": "assistant",
            "message": "已完成 5 个 Amazon 竞品分析，CSV 已生成并可下载。",
        }

    summary = summarize_workflow_result(
        tool_name="run_amazon_competitor_analysis",
        tool_result={
            "platform": "amazon",
            "brand": "Blackview",
            "count": 5,
            "summary": "已完成 5 个竞品分析",
            "filename": "amazon_blackview_5.csv",
            "download_url": "/api/download/amazon_blackview_5.csv",
        },
        llm_call=fake_llm,
    )

    assert summary == "已完成 5 个 Amazon 竞品分析，CSV 已生成并可下载。"


def test_default_llm_call_omits_empty_tools_for_dashscope_summary(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)

            class FakeMessage:
                content = "已完成 5 个竞品分析。"
                tool_calls = None

            class FakeChoice:
                message = FakeMessage()

            class FakeResponse:
                choices = [FakeChoice()]

            return FakeResponse()

    class FakeClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(primary_agent, "build_primary_agent_client", lambda: FakeClient())

    summary = summarize_workflow_result(
        tool_name="run_amazon_competitor_analysis",
        tool_result={
            "summary": "已完成 5 个竞品分析",
        },
    )

    assert summary == "已完成 5 个竞品分析。"
    assert captured["model"] == DASHSCOPE_MODEL
    assert "tools" not in captured
    assert "tool_choice" not in captured


def test_default_llm_call_parses_assistant_json_slot_updates(monkeypatch):
    class FakeCompletions:
        def create(self, **_kwargs):
            class FakeMessage:
                content = '{"message":"请告诉我要分析多少个竞品。","slot_updates":{"platform":"amazon","brand":"Blackview"}}'
                tool_calls = None

            class FakeChoice:
                message = FakeMessage()

            class FakeResponse:
                choices = [FakeChoice()]

            return FakeResponse()

    class FakeClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(primary_agent, "build_primary_agent_client", lambda: FakeClient())

    decision = primary_agent._default_llm_call(
        messages=[{"role": "user", "content": "看 Amazon 的 Blackview 竞品"}],
        tools=[{"type": "function", "function": {"name": "run_amazon_competitor_analysis"}}],
    )

    assert decision == {
        "type": "assistant",
        "message": "请告诉我要分析多少个竞品。",
        "slot_updates": {"platform": "amazon", "brand": "Blackview"},
    }


def test_default_llm_call_builds_tool_call_from_model_extracted_slots(monkeypatch):
    class FakeCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)

            class FakeMessage:
                content = '{"message":"","slot_updates":{"platform":"amazon","brand":"Blackview","count":5}}'
                tool_calls = None

            class FakeChoice:
                message = FakeMessage()

            class FakeResponse:
                choices = [FakeChoice()]

            return FakeResponse()

    fake_completions = FakeCompletions()

    class FakeClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": fake_completions})()

    monkeypatch.setattr(primary_agent, "build_primary_agent_client", lambda: FakeClient())

    decision = primary_agent._default_llm_call(
        messages=primary_agent._history_to_messages(
            [ChatMessage(role="user", content="看 Amazon 的 Blackview，5 个")],
            SessionSlots(),
        ),
        tools=[{"type": "function", "function": {"name": "run_amazon_competitor_analysis"}}],
    )

    assert len(fake_completions.calls) == 1
    assert "tools" not in fake_completions.calls[0]
    assert decision == {
        "type": "tool_call",
        "tool_name": "run_amazon_competitor_analysis",
        "arguments": {"brand": "Blackview", "count": 5},
        "assistant_message": "",
        "slot_updates": {"platform": "amazon", "brand": "Blackview", "count": 5},
    }


def test_default_llm_call_normalizes_amazon_aliases_before_tool_dispatch(monkeypatch):
    class FakeCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)

            class FakeMessage:
                content = '{"message":"","slot_updates":{"platform":"Amazon","brand":"Blackview","count":5}}'
                tool_calls = None

            class FakeChoice:
                message = FakeMessage()

            class FakeResponse:
                choices = [FakeChoice()]

            return FakeResponse()

    fake_completions = FakeCompletions()

    class FakeClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": fake_completions})()

    monkeypatch.setattr(primary_agent, "build_primary_agent_client", lambda: FakeClient())

    decision = primary_agent._default_llm_call(
        messages=primary_agent._history_to_messages(
            [ChatMessage(role="user", content="看 Amazon 的 Blackview，5 个")],
            SessionSlots(),
        ),
        tools=[{"type": "function", "function": {"name": "run_amazon_competitor_analysis"}}],
    )

    assert decision == {
        "type": "tool_call",
        "tool_name": "run_amazon_competitor_analysis",
        "arguments": {"brand": "Blackview", "count": 5},
        "assistant_message": "",
        "slot_updates": {"platform": "amazon", "brand": "Blackview", "count": 5},
    }


def test_default_llm_call_uses_missing_slot_prompt_when_model_message_is_empty(monkeypatch):
    class FakeCompletions:
        def create(self, **_kwargs):
            class FakeMessage:
                content = '{"message":"","slot_updates":{"platform":"amazon","brand":"Blackview"}}'
                tool_calls = None

            class FakeChoice:
                message = FakeMessage()

            class FakeResponse:
                choices = [FakeChoice()]

            return FakeResponse()

    class FakeClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(primary_agent, "build_primary_agent_client", lambda: FakeClient())

    decision = primary_agent._default_llm_call(
        messages=primary_agent._history_to_messages(
            [ChatMessage(role="user", content="看 Amazon 的 Blackview 竞品")],
            SessionSlots(),
        ),
        tools=[{"type": "function", "function": {"name": "run_amazon_competitor_analysis"}}],
    )

    assert decision == {
        "type": "assistant",
        "message": "请提供有效的数量后再试。",
        "slot_updates": {"platform": "amazon", "brand": "Blackview"},
    }
