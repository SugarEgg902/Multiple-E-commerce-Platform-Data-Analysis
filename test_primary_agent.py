import importlib
import sys

import pytest

from primary_agent import (
    DASHSCOPE_BASE_URL,
    DASHSCOPE_MODEL,
    build_primary_agent_client,
    decide_next_step,
    summarize_workflow_result,
)
from session_store import ChatMessage, SessionSlots


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

    module = importlib.reload(importlib.import_module("primary_agent"))

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


def test_decide_next_step_returns_tool_call_when_slots_are_complete():
    def fake_llm(_messages, _tools):
        return {
            "type": "tool_call",
            "tool_name": "run_amazon_competitor_analysis",
            "arguments": {"brand": " Blackview ", "count": "5"},
            "assistant_message": "好的，我开始分析 Amazon 上的 Blackview 竞品。",
            "slot_updates": {},
        }

    decision = decide_next_step(
        messages=[ChatMessage(role="user", content="看 Amazon 的 Blackview，5 个")],
        slots=SessionSlots(platform="amazon"),
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


def test_decide_next_step_preserves_and_infers_assistant_slot_updates():
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
        "slot_updates": {"brand": "Blackview", "platform": "amazon", "count": 5},
    }


@pytest.mark.parametrize(
    ("user_content", "expected_brand"),
    [
        ("看 Amazon 的 iPhone 16，5 个", "Blackview"),
        ("看 Amazon 的 Blackview BV9300，5 个", "Blackview"),
    ],
)
def test_decide_next_step_normalizes_incoming_slot_updates_for_quantity_examples(
    user_content, expected_brand
):
    def fake_llm(_messages, _tools):
        return {
            "type": "assistant",
            "message": "我先确认一下品牌和数量。",
            "slot_updates": {"brand": " Blackview ", "count": "5"},
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
        "message": "目前只支持 Amazon 竞品分析，请改用 Amazon。",
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
