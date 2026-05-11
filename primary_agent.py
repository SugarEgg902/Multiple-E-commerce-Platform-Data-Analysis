from __future__ import annotations

import json
import os
import re


DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DASHSCOPE_MODEL = "glm-4.6"


def build_primary_agent_client():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=DASHSCOPE_BASE_URL)


def _slot_state_from_slots(slots) -> dict:
    return {
        "platform": slots.platform,
        "brand": slots.brand,
        "count": slots.count,
    }


def _clean_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_count(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _normalize_slot_value(key: str, value):
    if key in {"platform", "brand"}:
        return _clean_text(value)
    if key == "count":
        return _normalize_count(value)
    return value


def _infer_slot_updates_from_messages(messages) -> dict:
    user_text = " ".join(message.content for message in messages if getattr(message, "role", "") == "user")
    if not user_text:
        return {}

    updates: dict[str, object] = {}
    lower_text = user_text.lower()
    if "amazon" in lower_text or "亚马逊" in user_text:
        updates["platform"] = "amazon"

    brand_match = re.search(r"(?:amazon|亚马逊)?[^\n]*?的\s*([A-Za-z0-9\u4e00-\u9fff_-]+)", user_text, re.I)
    if brand_match:
        brand = _clean_text(brand_match.group(1))
        if brand and brand not in {"竞品", "商品", "分析", "平台"}:
            updates["brand"] = brand

    count_match = re.search(r"(?<!\d)(\d+)\s*(?:个|件|款)", user_text)
    if count_match:
        updates["count"] = int(count_match.group(1))

    return updates


def _merge_slot_updates(messages, slots, decision_updates) -> dict:
    if not isinstance(decision_updates, dict):
        decision_updates = {}
    merged = {k: v for k, v in _slot_state_from_slots(slots).items() if v is not None}
    merged.update(_infer_slot_updates_from_messages(messages))
    for key, value in decision_updates.items():
        normalized_value = _normalize_slot_value(key, value)
        if normalized_value not in (None, ""):
            merged[key] = normalized_value
    return merged


def _history_to_messages(messages, slots) -> list[dict]:
    slot_state = _slot_state_from_slots(slots)
    system_prompt = (
        "你是电商竞品分析主代理。"
        "你只能决定是继续追问，还是调用高层平台工作流工具。"
        "如果 platform、brand、count 任一缺失，就追问缺失项，不要猜测。"
        f"当前已知槽位: {json.dumps(slot_state, ensure_ascii=False)}"
    )
    history = [{"role": "system", "content": system_prompt}]
    history.extend({"role": message.role, "content": message.content} for message in messages)
    return history


def _default_llm_call(messages: list[dict], tools: list[dict]) -> dict:
    client = build_primary_agent_client()
    response = client.chat.completions.create(
        model=DASHSCOPE_MODEL,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        temperature=0.2,
    )
    message = response.choices[0].message
    if message.tool_calls:
        tool_call = message.tool_calls[0]
        return {
            "type": "tool_call",
            "tool_name": tool_call.function.name,
            "arguments": json.loads(tool_call.function.arguments or "{}"),
            "assistant_message": message.content or "",
            "slot_updates": {},
        }
    return {
        "type": "assistant",
        "message": message.content or "请补充平台、品牌和数量。",
        "slot_updates": {},
    }


def _normalize_amazon_tool_call(decision: dict, messages, slots) -> dict:
    raw_arguments = decision.get("arguments") or {}
    if not isinstance(raw_arguments, dict):
        raw_arguments = {}
    raw_slot_updates = decision.get("slot_updates") or {}
    normalized_slot_updates = _merge_slot_updates(messages, slots, raw_slot_updates)

    brand = _clean_text(raw_arguments.get("brand"))
    if brand is None:
        brand = _clean_text(slots.brand) or _clean_text(normalized_slot_updates.get("brand"))

    count = _normalize_count(raw_arguments.get("count"))
    if count is None:
        count = _normalize_count(slots.count)
    if count is None:
        count = _normalize_count(normalized_slot_updates.get("count"))

    if brand is None or count is None:
        return {
            "type": "assistant",
            "message": "请提供有效的品牌和数量后再试。",
            "slot_updates": normalized_slot_updates,
        }

    normalized_slot_updates.update(
        {
            "platform": "amazon",
            "brand": brand,
            "count": count,
        }
    )
    return {
        "type": "tool_call",
        "tool_name": "run_amazon_competitor_analysis",
        "arguments": {
            "brand": brand,
            "count": count,
        },
        "assistant_message": decision.get("assistant_message", ""),
        "slot_updates": normalized_slot_updates,
    }


def decide_next_step(messages, slots, tool_schemas, llm_call=None) -> dict:
    llm_call = llm_call or _default_llm_call
    decision = llm_call(_history_to_messages(messages, slots), tool_schemas)
    if decision.get("type") == "assistant":
        decision = dict(decision)
        decision["slot_updates"] = _merge_slot_updates(messages, slots, decision.get("slot_updates", {}))
        return decision

    if decision.get("type") == "tool_call":
        supported_names = {schema["function"]["name"] for schema in tool_schemas}
        if decision.get("tool_name") not in supported_names:
            return {
                "type": "assistant",
                "message": "目前只支持 Amazon 竞品分析，请改用 Amazon。",
                "slot_updates": _merge_slot_updates(messages, slots, decision.get("slot_updates", {})),
            }
        if decision.get("tool_name") == "run_amazon_competitor_analysis":
            return _normalize_amazon_tool_call(decision, messages, slots)
    return decision


def summarize_workflow_result(tool_name: str, tool_result: dict, llm_call=None) -> str:
    llm_call = llm_call or _default_llm_call
    messages = [
        {
            "role": "system",
            "content": "你是电商竞品分析主代理。根据工作流结果，为用户输出一条简洁中文总结。",
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "tool_name": tool_name,
                    "tool_result": tool_result,
                },
                ensure_ascii=False,
            ),
        },
    ]
    result = llm_call(messages, [])
    return result.get("message", "") or tool_result.get("summary", "任务已完成。")
