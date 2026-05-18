from __future__ import annotations

import json
import os
import re
from config.config import DASHSCOPE_API_KEY,DASHSCOPE_BASE_URL

DASHSCOPE_BASE_URL = DASHSCOPE_BASE_URL
DASHSCOPE_MODEL = "glm-4.6"


def build_primary_agent_client():
    api_key = DASHSCOPE_API_KEY
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
    if key == "platform":
        text = _clean_text(value)
        if text is None:
            return None
        compact = re.sub(r"\s+", "", text).lower()
        platform_aliases = {
            "amazon": "amazon",
            "amazon.com": "amazon",
            "亚马逊": "amazon",
            "ebay": "ebay",
            "ebay.com": "ebay",
            "易贝": "ebay",
            "temu": "temu",
            "ozon": "ozon",
            "ozon.ru": "ozon",
            "奥赞": "ozon",
            "otto": "otto",
            "otto.de": "otto",
            "奥托": "otto",
            "allegro": "allegro",
            "allegro.pl": "allegro",
            "波兰": "allegro",
            "tiktokshop": "tiktokshop",
            "tiktok": "tiktokshop",
            "tiktok shop": "tiktokshop",
            "tiktokshop.com": "tiktokshop",
            "抖音小店": "tiktokshop",
            "cdiscount": "cdiscount",
            "cdiscount.com": "cdiscount",
            "法国": "cdiscount",
            "aliexpress": "aliexpress",
            "aliexpress.com": "aliexpress",
            "速卖通": "aliexpress",
            "全球速卖通": "aliexpress",
        }
        return platform_aliases.get(compact, compact)
    if key == "brand":
        return _clean_text(value)
    if key == "count":
        return _normalize_count(value)
    return value


def _normalize_slot_updates(slot_updates) -> dict:
    if not isinstance(slot_updates, dict):
        return {}

    normalized: dict[str, object] = {}
    for key in ("platform", "brand", "count"):
        normalized_value = _normalize_slot_value(key, slot_updates.get(key))
        if normalized_value not in (None, ""):
            normalized[key] = normalized_value
    return normalized


def _strip_json_fence(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text

    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_assistant_content(content: str) -> dict:
    text = _clean_text(content)
    if text is None:
        return {
            "type": "assistant",
            "message": "请补充平台、品牌和数量。",
            "slot_updates": {},
        }

    stripped = _strip_json_fence(text)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return {
            "type": "assistant",
            "message": text,
            "slot_updates": {},
        }

    if not isinstance(payload, dict):
        return {
            "type": "assistant",
            "message": text,
            "slot_updates": {},
        }

    return {
        "type": "assistant",
        "message": _clean_text(payload.get("message")),
        "slot_updates": _normalize_slot_updates(payload.get("slot_updates")),
    }


def _merge_slot_updates(messages, slots, decision_updates) -> dict:
    del messages
    merged = {k: v for k, v in _slot_state_from_slots(slots).items() if v is not None}
    merged.update(_normalize_slot_updates(decision_updates))
    return merged


def _history_to_messages(messages, slots) -> list[dict]:
    slot_state = _slot_state_from_slots(slots)
    system_prompt = (
        "你是电商竞品分析主代理。"
        "你只能决定是继续追问，还是调用高层平台工作流工具。"
        "你必须只根据用户明确输入来识别 platform、brand(搜索关键词)、count，禁止猜测或脑补。"
        "如果信息不完整，不要调用工具，直接输出 JSON："
        '{"message":"给用户看的追问","slot_updates":{"platform":"已确认的平台或省略","brand":"已确认的搜索词或省略","count":已确认数量或省略}}。'
        "如果信息完整且平台受支持，调用对应工具。"
        f"当前已知槽位: {json.dumps(slot_state, ensure_ascii=False)}"
    )
    history = [{"role": "system", "content": system_prompt}]
    history.extend({"role": message.role, "content": message.content} for message in messages)
    return history


def _extract_slot_state_from_system_prompt(messages: list[dict]) -> dict:
    if not messages:
        return {}

    system_content = messages[0].get("content", "")
    if "当前已知槽位:" not in system_content:
        return {}

    try:
        raw_slot_state = system_content.split("当前已知槽位:", 1)[1].strip()
        slot_state = json.loads(raw_slot_state)
    except (IndexError, json.JSONDecodeError):
        return {}

    return _normalize_slot_updates(slot_state)


def _build_slot_extraction_messages(messages: list[dict]) -> list[dict]:
    slot_state = _extract_slot_state_from_system_prompt(messages)
    system_prompt = (
        "你是电商竞品分析主代理。"
        "你的当前任务只有一个：从用户明确说出的内容里识别 platform、brand(搜索关键词)、count。"
        "不要猜测，不要脑补，不要因为常识补全缺失字段。"
        "如果某个字段用户没有明确说，就不要填。"
        "只返回 JSON："
        '{"message":"如果信息不完整时给用户的追问；如果信息完整可留空","slot_updates":{"platform":"明确提到的平台或省略","brand":"明确提到的搜索关键词或省略","count":明确提到的数量或省略}}。'
        f"当前已知槽位: {json.dumps(slot_state, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system_prompt}, *messages[1:]]


def _is_complete_ebay_slot_state(slot_state: dict) -> bool:
    return (
        _clean_text(slot_state.get("platform")) == "ebay"
        and _clean_text(slot_state.get("brand")) is not None
        and _normalize_count(slot_state.get("count")) is not None
    )


def _is_complete_temu_slot_state(slot_state: dict) -> bool:
    return (
        _clean_text(slot_state.get("platform")) == "temu"
        and _clean_text(slot_state.get("brand")) is not None
        and _normalize_count(slot_state.get("count")) is not None
    )


def _is_complete_ozon_slot_state(slot_state: dict) -> bool:
    return (
        _clean_text(slot_state.get("platform")) == "ozon"
        and _clean_text(slot_state.get("brand")) is not None
        and _normalize_count(slot_state.get("count")) is not None
    )


def _is_complete_otto_slot_state(slot_state: dict) -> bool:
    return (
        _clean_text(slot_state.get("platform")) == "otto"
        and _clean_text(slot_state.get("brand")) is not None
        and _normalize_count(slot_state.get("count")) is not None
    )


def _is_complete_allegro_slot_state(slot_state: dict) -> bool:
    return (
        _clean_text(slot_state.get("platform")) == "allegro"
        and _clean_text(slot_state.get("brand")) is not None
        and _normalize_count(slot_state.get("count")) is not None
    )


def _is_complete_tiktokshop_slot_state(slot_state: dict) -> bool:
    return (
        _clean_text(slot_state.get("platform")) == "tiktokshop"
        and _clean_text(slot_state.get("brand")) is not None
        and _normalize_count(slot_state.get("count")) is not None
    )


def _is_complete_cdiscount_slot_state(slot_state: dict) -> bool:
    return (
        _clean_text(slot_state.get("platform")) == "cdiscount"
        and _clean_text(slot_state.get("brand")) is not None
        and _normalize_count(slot_state.get("count")) is not None
    )


def _is_complete_aliexpress_slot_state(slot_state: dict) -> bool:
    return (
        _clean_text(slot_state.get("platform")) == "aliexpress"
        and _clean_text(slot_state.get("brand")) is not None
        and _normalize_count(slot_state.get("count")) is not None
    )


def _is_complete_amazon_slot_state(slot_state: dict) -> bool:
    return (
        _clean_text(slot_state.get("platform")) == "amazon"
        and _clean_text(slot_state.get("brand")) is not None
        and _normalize_count(slot_state.get("count")) is not None
    )


def _default_llm_call(messages: list[dict], tools: list[dict]) -> dict:
    client = build_primary_agent_client()

    if not tools:
        request_kwargs = {
            "model": DASHSCOPE_MODEL,
            "messages": messages,
            "temperature": 0.2,
        }
        response = client.chat.completions.create(
            **request_kwargs,
        )
        message = response.choices[0].message
        return _parse_assistant_content(message.content or "")

    parse_response = client.chat.completions.create(
        model=DASHSCOPE_MODEL,
        messages=_build_slot_extraction_messages(messages),
        temperature=0,
    )
    parsed_decision = _parse_assistant_content(parse_response.choices[0].message.content or "")
    merged_slot_updates = _extract_slot_state_from_system_prompt(messages)
    merged_slot_updates.update(_normalize_slot_updates(parsed_decision.get("slot_updates")))

    if _is_complete_amazon_slot_state(merged_slot_updates):
        return {
            "type": "tool_call",
            "tool_name": "run_amazon_competitor_analysis",
            "arguments": {
                "brand": _clean_text(merged_slot_updates.get("brand")),
                "count": _normalize_count(merged_slot_updates.get("count")),
            },
            "assistant_message": "",
            "slot_updates": merged_slot_updates,
        }

    if _is_complete_ebay_slot_state(merged_slot_updates):
        return {
            "type": "tool_call",
            "tool_name": "run_ebay_competitor_analysis",
            "arguments": {
                "brand": _clean_text(merged_slot_updates.get("brand")),
                "count": _normalize_count(merged_slot_updates.get("count")),
            },
            "assistant_message": "",
            "slot_updates": merged_slot_updates,
        }

    if _is_complete_temu_slot_state(merged_slot_updates):
        return {
            "type": "tool_call",
            "tool_name": "run_temu_competitor_analysis",
            "arguments": {
                "brand": _clean_text(merged_slot_updates.get("brand")),
                "count": _normalize_count(merged_slot_updates.get("count")),
            },
            "assistant_message": "",
            "slot_updates": merged_slot_updates,
        }

    if _is_complete_ozon_slot_state(merged_slot_updates):
        return {
            "type": "tool_call",
            "tool_name": "run_ozon_competitor_analysis",
            "arguments": {
                "brand": _clean_text(merged_slot_updates.get("brand")),
                "count": _normalize_count(merged_slot_updates.get("count")),
            },
            "assistant_message": "",
            "slot_updates": merged_slot_updates,
        }

    if _is_complete_otto_slot_state(merged_slot_updates):
        return {
            "type": "tool_call",
            "tool_name": "run_otto_competitor_analysis",
            "arguments": {
                "brand": _clean_text(merged_slot_updates.get("brand")),
                "count": _normalize_count(merged_slot_updates.get("count")),
            },
            "assistant_message": "",
            "slot_updates": merged_slot_updates,
        }

    if _is_complete_allegro_slot_state(merged_slot_updates):
        return {
            "type": "tool_call",
            "tool_name": "run_allegro_competitor_analysis",
            "arguments": {
                "brand": _clean_text(merged_slot_updates.get("brand")),
                "count": _normalize_count(merged_slot_updates.get("count")),
            },
            "assistant_message": "",
            "slot_updates": merged_slot_updates,
        }

    if _is_complete_tiktokshop_slot_state(merged_slot_updates):
        return {
            "type": "tool_call",
            "tool_name": "run_tiktokshop_competitor_analysis",
            "arguments": {
                "brand": _clean_text(merged_slot_updates.get("brand")),
                "count": _normalize_count(merged_slot_updates.get("count")),
            },
            "assistant_message": "",
            "slot_updates": merged_slot_updates,
        }

    if _is_complete_cdiscount_slot_state(merged_slot_updates):
        return {
            "type": "tool_call",
            "tool_name": "run_cdiscount_competitor_analysis",
            "arguments": {
                "brand": _clean_text(merged_slot_updates.get("brand")),
                "count": _normalize_count(merged_slot_updates.get("count")),
            },
            "assistant_message": "",
            "slot_updates": merged_slot_updates,
        }

    if _is_complete_aliexpress_slot_state(merged_slot_updates):
        return {
            "type": "tool_call",
            "tool_name": "run_aliexpress_competitor_analysis",
            "arguments": {
                "brand": _clean_text(merged_slot_updates.get("brand")),
                "count": _normalize_count(merged_slot_updates.get("count")),
            },
            "assistant_message": "",
            "slot_updates": merged_slot_updates,
        }

    return {
        "type": "assistant",
        "message": parsed_decision.get("message") or _build_missing_slot_message(merged_slot_updates),
        "slot_updates": merged_slot_updates,
    }


def _build_missing_slot_message(slot_state: dict) -> str:
    platform = _clean_text(slot_state.get("platform"))
    brand = _clean_text(slot_state.get("brand"))
    count = _normalize_count(slot_state.get("count"))

    supported = {"amazon", "ebay", "temu", "ozon", "otto", "allegro", "tiktokshop", "cdiscount", "aliexpress"}
    if platform not in (None, *supported):
        return "目前只支持 Amazon、eBay、Temu、OZON、OTTO、Allegro、TikTok Shop、Cdiscount、AliExpress 竞品分析，请改用其中一个平台。"
    if platform is None:
        return "你想分析哪个平台？目前我支持 Amazon、eBay、Temu、OZON、OTTO、Allegro、TikTok Shop、Cdiscount 和 AliExpress。"
    if brand is None and count is None:
        return "请提供有效的品牌和数量后再试。"
    if brand is None:
        return "请提供有效的品牌后再试。"
    if count is None:
        return "请提供有效的数量后再试。"
    return "请提供平台、品牌和数量后再试。"


def _normalize_amazon_tool_call(decision: dict, messages, slots) -> dict:
    normalized_slot_updates = _merge_slot_updates(messages, slots, decision.get("slot_updates", {}))
    platform = _clean_text(normalized_slot_updates.get("platform"))
    raw_arguments = decision.get("arguments") or {}
    if not isinstance(raw_arguments, dict):
        raw_arguments = {}
    brand = _clean_text(raw_arguments.get("brand")) or _clean_text(normalized_slot_updates.get("brand"))
    count = _normalize_count(raw_arguments.get("count"))
    if count is None:
        count = _normalize_count(normalized_slot_updates.get("count"))

    if platform != "amazon" or brand is None or count is None:
        partial_slot_updates = dict(normalized_slot_updates)
        if brand is not None:
            partial_slot_updates["brand"] = brand
        if count is not None:
            partial_slot_updates["count"] = count
        return {
            "type": "assistant",
            "message": _build_missing_slot_message(partial_slot_updates),
            "slot_updates": partial_slot_updates,
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
                "message": "目前只支持 Amazon、eBay、Temu、OZON、OTTO、Allegro、TikTok Shop、Cdiscount 竞品分析，请改用其中一个平台。",
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
