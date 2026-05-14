from __future__ import annotations

import json


LLM_BASE_URL = "http://10.0.0.21:8005/v1"
LLM_MODEL = "gemma-4-31b-it-fp8"


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _normalize_text_list(value: object) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif value in (None, ""):
        raw_items = []
    else:
        raw_items = [value]

    items: list[str] = []
    for raw in raw_items:
        text = _stringify(raw)
        if text and text not in items:
            items.append(text)
    return items


def _join_summary_items(value: object) -> str:
    return "；".join(_normalize_text_list(value))


def _fallback_core_selling_points(product: dict) -> str:
    bullets = _normalize_text_list(product.get("bullets", []))
    if bullets:
        return "；".join(bullets[:3])
    return _stringify(product.get("title", ""))


def _fallback_positioning(product: dict, review_summary: dict) -> str:
    price = _stringify(product.get("price", ""))
    overall = _stringify(review_summary.get("overall", ""))
    if price and overall:
        return f"{price} 价位，{overall}"
    return overall or price


def _default_llm_call(payload: dict) -> dict:
    from openai import OpenAI

    client = OpenAI(base_url=LLM_BASE_URL, api_key="EMPTY")
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是竞品分析助手。"
                    "你会收到品牌、商品信息和评论摘要。"
                    "只输出严格 JSON，且只包含三个字段："
                    "核心卖点(string)、竞品定位(string)、总类目(string)。"
                    "核心卖点要根据标题、bullets、评论摘要提炼成简体中文短句；"
                    "竞品定位要结合价格、卖点和评论表现，输出一句中文定位总结；"
                    "总类目要根据商品标题和类目信息，给出最顶层的商品大类（如：手机、耳机、平板、智能手表等），用简体中文简短词语表示。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content or "{}")


def build_analysis_row(brand: str, product: dict, review_summary: dict, llm_call=None) -> dict:
    llm_call = llm_call or _default_llm_call
    try:
        analysis = llm_call(
            {
                "brand": brand,
                "product": product,
                "review_summary": review_summary,
            }
        )
    except Exception:
        analysis = {}

    pros = _join_summary_items(review_summary.get("pros", []))
    cons = _join_summary_items(review_summary.get("cons", []))
    overall = _stringify(review_summary.get("overall", ""))
    core_selling_points = _stringify(analysis.get("核心卖点", "")) or _fallback_core_selling_points(product)
    positioning = _stringify(analysis.get("竞品定位", "")) or _fallback_positioning(product, review_summary)
    # 总类目优先从 LLM 输出取，其次 fallback 到 product 字段（Amazon BSR 类目）
    category = _stringify(analysis.get("总类目", "")) or _stringify(product.get("bsr_category", ""))

    return {
        "搜索词": brand,
        "ASIN": product.get("asin", ""),
        "商品id": product.get("asin", ""),
        "url": product.get("url", ""),
        "商品标题": product.get("title", ""),
        "价格": product.get("price", ""),
        "评分": product.get("rating", ""),
        "评论数": product.get("review_count", ""),
        "总类目": category,
        "Best Sellers Rank": product.get("bsr_display", ""),
        "月销量区间": product.get("monthly_sales_range", ""),
        "月销量估算值": product.get("monthly_sales_estimate", ""),
        "月销售额估算": product.get("monthly_revenue_estimate", ""),
        "总销量估算": product.get("总销量估算", ""),
        "总销售额估算": product.get("总销售额估算", ""),
        "核心卖点": core_selling_points,
        "优点评炼": pros or _stringify(analysis.get("优点评炼", "")),
        "缺点评炼": cons or _stringify(analysis.get("缺点评炼", "")),
        "综合分析": overall or _stringify(analysis.get("综合分析", "")),
        "竞品定位": positioning,
    }
