from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any


LLM_BASE_URL = "http://10.0.0.21:8000/v1"
LLM_MODEL = "qwen3.6-35b-a3b-fp8"
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "apify_api_gVcg3a3TfXLLX1NSDSrBxGL67AAccx3qK1Su")
APIFY_OZON_ACTOR = "APa5EQZZaXHWBmogv"
# Approximate RUB→USD rate; update as needed
RUB_TO_USD = 1 / 88.0

# Module-level cache: product_id -> raw review list, populated during scrape_ozon_products
_reviews_cache: dict[str, list[dict]] = {}


def _rub_to_usd(rub: int | float | None) -> str | None:
    if rub is None:
        return None
    return f"${round(rub * RUB_TO_USD, 2):.2f}"


def _estimate_total_sales(
    review_count: int | None,
    price_usd: float | None,
    breadcrumbs: list,
) -> tuple[str, str]:
    """Estimate total sales from review count using category-based conversion rate.

    手机/smartphone: 1–5% → midpoint 3%
    笔记本/laptop:   0.5–3% → midpoint 1.75%
    """
    if not review_count:
        return "", ""
    category_text = " ".join(b.get("name", "") for b in (breadcrumbs or [])).lower()
    if any(kw in category_text for kw in ["ноутбук", "laptop", "notebook", "компьютер"]):
        rate = 0.0175
    else:
        rate = 0.03
    total_sales = int(review_count / rate)
    total_revenue = round(total_sales * price_usd, 2) if price_usd else ""
    return str(total_sales), str(total_revenue) if total_revenue != "" else ""


def _llm_summarize(positive: list[dict], negative: list[dict]) -> dict:
    from openai import OpenAI

    client = OpenAI(base_url=LLM_BASE_URL, api_key="EMPTY")

    def fmt(reviews: list[dict]) -> str:
        if not reviews:
            return "（无）"
        lines = []
        for i, r in enumerate(reviews, 1):
            lines.append(f"{i}. [{r.get('rating','')}] {r.get('title','')}\n   {r.get('text','')}")
        return "\n".join(lines)

    sys_prompt = (
        "你是一个电商商品评论分析助手。"
        "你将收到若干条来自 OZON 的俄文好评和差评(中评也归类到差评)，"
        "请用简体中文总结产品的优点和缺点，着重关注缺点。"
        "输出严格的 JSON，字段为 pros(string数组)、cons(string数组)、overall(一段中文总评)。"
        "每条 pros/cons 请精炼成 10-25 字短句，去重，按重要性排序。"
        "只输出 JSON，不要任何额外说明。"
    )
    user_prompt = (
        f"【好评 {len(positive)} 条】\n{fmt(positive)}\n\n"
        f"【差评 {len(negative)} 条】\n{fmt(negative)}\n\n"
        "请输出 JSON。"
    )
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"pros": [], "cons": [], "overall": raw}
    return {
        "pros": parsed.get("pros", []) or [],
        "cons": parsed.get("cons", []) or [],
        "overall": parsed.get("overall", "") or "",
    }


def _map_apify_item(item: dict, keyword: str) -> dict:
    sku = str(item.get("sku") or item.get("productId") or "")
    url = item.get("url") or f"https://www.ozon.ru/product/{sku}/"
    title = (item.get("title") or "").strip()

    price_rub = item.get("cardPriceDecimal") or item.get("priceDecimal")
    price_usd_float = round(price_rub * RUB_TO_USD, 2) if price_rub else None
    price_display = f"${price_usd_float:.2f}" if price_usd_float is not None else None

    rating = item.get("rating")
    review_count = item.get("reviewCount")
    breadcrumbs = item.get("breadcrumbs") or []

    # Cache raw reviews for scrape_ozon_reviews to consume later
    _reviews_cache[sku] = item.get("reviews") or []

    # Bullets from shortCharacteristics
    bullets: list[str] = [
        f"{c['name']}: {c['value']}"
        for c in (item.get("shortCharacteristics") or [])
        if c.get("name") and c.get("value")
    ]

    total_sales, total_revenue = _estimate_total_sales(review_count, price_usd_float, breadcrumbs)

    data: dict[str, Any] = {
        "product_id": sku,
        "keyword": keyword,
        "url": url.split("?")[0],
        "crawl_time": datetime.now().isoformat(),
        "title": title or None,
        "price": price_display,
        "rating": str(rating) if rating is not None else None,
        "review_count": review_count,
        "sold_count": None,
        "总销量估算": total_sales,
        "总销售额估算": total_revenue,
        "monthly_sales_estimate": "",
        "monthly_revenue_estimate": "",
        "monthly_sales_range": "",
        "bsr_rank": "",
        "bsr_category": "",
        "bsr_display": "",
        "bullets": bullets,
        "is_valid": len(title) > 10 and price_display is not None,
        "invalid_reason": None if (len(title) > 10 and price_display) else "缺少关键信息",
    }
    status = "✓ 有效" if data["is_valid"] else f"✗ 无效({data['invalid_reason']})"
    print(f"  {status} - {title[:60] or '无标题'}")
    return data


def _run_apify(keyword: str, max_results: int) -> list[dict]:
    from apify_client import ApifyClient

    client = ApifyClient(APIFY_API_TOKEN)
    run = client.actor(APIFY_OZON_ACTOR).call(run_input={
        "queries": [keyword],
        "maxResults": max_results,
        "skipDetails": False,
        "includeSellerDetails": False,
        "sorting": "score",
        "language": "ru",
        "currency": "RUB",
    })
    return list(client.dataset(run["defaultDatasetId"]).iterate_items())


async def scrape_ozon_products(
    keyword: str,
    max_pages: int = 2,
    max_valid: int = 5,
    headless: bool = False,
) -> list[dict]:
    """Apify OZON actor -> 返回有效产品列表（价格已换算为美元）。"""
    print(f"[ozon] Apify 搜索: {keyword!r}, 目标有效 {max_valid}")
    raw_items = await asyncio.to_thread(_run_apify, keyword, max_valid * 3)
    print(f"[ozon] Apify 返回 {len(raw_items)} 条原始数据")

    valid: list[dict] = []
    for item in raw_items:
        if len(valid) >= max_valid:
            break
        mapped = _map_apify_item(item, keyword)
        if mapped.get("is_valid"):
            valid.append(mapped)
            print(f"[ozon] 已拿到 {len(valid)}/{max_valid} 个有效产品")

    return valid


async def scrape_ozon_reviews(product_id: str, product_url: str, max_reviews: int = 60) -> dict:
    """从 Apify 缓存的评论中提取好评/差评并用 LLM 总结。"""
    raw = _reviews_cache.get(product_id, [])
    if not raw:
        return {"pros": [], "cons": [], "overall": ""}

    reviews: list[dict] = []
    for r in raw[:max_reviews]:
        rating = r.get("rating")
        # Combine comment + positive/negative fields into one text
        parts = [r.get("comment") or "", r.get("positive") or "", r.get("negative") or ""]
        text = " ".join(p for p in parts if p).strip()
        if text:
            reviews.append({"rating": f"{rating}/5" if rating else "", "title": "", "text": text[:500]})

    positive = [r for r in reviews if r["rating"].startswith(("4", "5"))]
    negative = [r for r in reviews if r["rating"].startswith(("1", "2", "3"))]
    print(f"[ozon] summarizing {len(positive)} pos / {len(negative)} neg reviews for {product_id}")
    return await asyncio.to_thread(_llm_summarize, positive, negative)
