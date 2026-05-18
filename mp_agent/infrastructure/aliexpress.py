from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime

from apify_client import ApifyClient
from config.config import APIFY_API_TOKEN_2, APIFY_ALIEXPRESS_ACTOR, LLM_BASE_URL, LLM_MODEL, EUR_TO_USD


_MAX_APIFY_RETRIES = 3


def _to_usd(price: float | None, currency: str) -> float | None:
    if price is None:
        return None
    if currency == "USD":
        return round(price, 2)
    if currency == "EUR":
        return round(price * EUR_TO_USD, 2)
    return round(price, 2)


def _run_apify(keyword: str, max_results: int, country: str) -> list[dict]:
    client = ApifyClient(APIFY_API_TOKEN_2)
    run_input = {
        "queries": [keyword],
        "category": "all",
        "trending": False,
        "maxResults": max_results,
        "sortBy": "default",
        "country": country.upper(),
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
            "apifyProxyCountry": country.upper(),
        },
    }
    print(f"[aliexpress apify] search {keyword!r} max={max_results} country={country}")
    run = client.actor(APIFY_ALIEXPRESS_ACTOR).call(run_input=run_input)
    return list(client.dataset(run["defaultDatasetId"]).iterate_items())


def _map_apify_item(item: dict, keyword: str) -> dict:
    product_id = str(item.get("product_id") or "")
    title = (item.get("title") or "").strip()
    currency = item.get("sale_price_currency") or "USD"
    sale_price_raw = item.get("sale_price")
    original_price_raw = item.get("original_price")
    sale_price_usd = _to_usd(sale_price_raw, currency)
    original_price_usd = _to_usd(original_price_raw, currency)
    orders_count = item.get("orders_count") or 0
    rating = item.get("rating")
    selling_points: list[str] = item.get("selling_points") or []
    discount_pct = item.get("discount_percentage")

    total_revenue = round(orders_count * sale_price_usd, 2) if (orders_count and sale_price_usd) else ""

    price_display = f"${sale_price_usd:.2f}" if sale_price_usd is not None else ""
    original_display = f"${original_price_usd:.2f}" if original_price_usd is not None else ""

    is_valid = (
        bool(product_id)
        and len(title) > 10
        and sale_price_usd is not None
        and sale_price_usd > 0
    )

    return {
        "product_id": product_id,
        "asin": product_id,
        "keyword": keyword,
        "url": item.get("url") or f"https://www.aliexpress.com/item/{product_id}.html",
        "title": title,
        "price": price_display,
        "price_usd": sale_price_usd,
        "original_price": original_display,
        "original_price_usd": original_price_usd,
        "discount_percentage": discount_pct,
        "currency": currency,
        "rating": str(rating) if rating is not None else "",
        "review_count": None,
        "orders_count": orders_count,
        "selling_points": selling_points,
        "bullets": selling_points,
        "is_sponsored": item.get("is_sponsored", False),
        "总销量估算": str(orders_count) if orders_count else "",
        "总销售额估算": str(total_revenue) if total_revenue != "" else "",
        "monthly_sales_estimate": "",
        "monthly_revenue_estimate": "",
        "monthly_sales_range": "",
        "bsr_rank": "",
        "bsr_category": "",
        "bsr_display": "",
        "is_valid": is_valid,
        "crawl_time": datetime.now().isoformat(),
    }


async def scrape_aliexpress_products(
    keyword: str,
    max_pages: int = 2,
    max_valid: int = 5,
    headless: bool = False,
    country: str = "US",
) -> list[dict]:
    request_size = max(20, max_valid * 4)
    seen_ids: set[str] = set()
    valid: list[dict] = []

    for attempt in range(_MAX_APIFY_RETRIES):
        raw_items = await asyncio.to_thread(_run_apify, keyword, request_size, country)
        print(f"[aliexpress apify] returned {len(raw_items)} raw items (attempt {attempt + 1})")
        for item in raw_items:
            if len(valid) >= max_valid:
                break
            mapped = _map_apify_item(item, keyword)
            pid = mapped["product_id"]
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            if mapped["is_valid"]:
                valid.append(mapped)
                print(f"[aliexpress] {pid} ✓ {mapped['title'][:50]}")
            else:
                print(f"[aliexpress] {item.get('product_id', '')} ✗ 无效")
        if len(valid) >= max_valid:
            break
        if attempt < _MAX_APIFY_RETRIES - 1:
            request_size = min(request_size * 2, max_valid * 20)
            print(f"[aliexpress] 有效产品不足 {max_valid}，扩大请求量至 {request_size} 重试...")

    print(f"[aliexpress] 共 {len(valid)} 个有效产品")
    return valid


def _llm_analyze_product(product: dict) -> dict:
    from openai import OpenAI

    client = OpenAI(base_url=LLM_BASE_URL, api_key="EMPTY")
    selling_pts = "；".join((product.get("selling_points") or [])[:5])

    sys_prompt = (
        "你是一个电商竞品分析助手。"
        "根据 AliExpress 商品的标题、价格、折扣、评分、销量和卖点，用简体中文生成竞品定位分析。"
        "输出严格的 JSON，字段为 overall（一段中文分析，100-150字）。"
        "只输出 JSON，不要任何额外说明。"
    )
    user_prompt = (
        f"商品标题：{product.get('title', '')}\n"
        f"售价：{product.get('price', '')}（原价 {product.get('original_price', '')}，折扣 {product.get('discount_percentage', '')}%）\n"
        f"评分：{product.get('rating', '')}\n"
        f"总销量：{product.get('总销量估算', '')}\n"
        f"总销售额：{product.get('总销售额估算', '')}\n"
        f"卖点：{selling_pts}\n"
        "请输出 JSON。"
    )
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"overall": raw}
    return {
        "pros": [],
        "cons": [],
        "overall": parsed.get("overall", "") or "",
    }


async def scrape_aliexpress_reviews(
    product_id: str,
    product_url: str,
    max_reviews: int = 60,
) -> dict:
    return {"pros": [], "cons": [], "overall": ""}
