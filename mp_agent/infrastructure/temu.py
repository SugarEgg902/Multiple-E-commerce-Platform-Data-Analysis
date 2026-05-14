from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime

APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
APIFY_TEMU_ACTOR = "LTBzVVq592mKgR6lU"

LLM_BASE_URL = "http://10.0.0.21:8000/v1"
LLM_MODEL = "qwen3.6-35b-a3b-fp8"

_product_cache: dict[str, dict] = {}


def _parse_price(val) -> float | None:
    if val is None:
        return None
    m = re.search(r"([\d,]+\.?\d*)", str(val))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _map_apify_item(item: dict, keyword: str) -> dict:
    goods_id = str(item.get("goods_id") or "")
    title = (item.get("title") or "").strip()

    # Price lives inside price_info nested object
    # price_str is already formatted (e.g. "$12.99"), price is the raw number
    price_info = item.get("price_info") or {}
    price_float = None
    price_str = ""
    for key in ["price_str", "price_text", "price", "split_price_text", "market_price_str"]:
        val = price_info.get(key)
        if val is None:
            continue
        candidate = _parse_price(val)
        if candidate is not None and candidate > 0:
            price_float = candidate
            price_str = f"${price_float:.2f}"
            break

    # Rating and review count from comment nested object
    comment = item.get("comment") or {}
    rating_raw = comment.get("goods_score") or comment.get("star") or comment.get("rating") or ""
    try:
        rating = float(str(rating_raw)) if rating_raw else None
    except (ValueError, TypeError):
        rating = None

    review_count_raw = (
        comment.get("comment_num_tips") or comment.get("comment_num") or
        comment.get("count") or 0
    )
    try:
        review_count = int(str(review_count_raw).replace(",", ""))
    except (ValueError, TypeError):
        review_count = 0

    # Sales from sales_num (string like "1234" or "0")
    sold_raw = item.get("sales_num") or item.get("sales_tip") or 0
    try:
        sold_int = int(str(sold_raw).replace(",", "").replace("+", ""))
    except (ValueError, TypeError):
        sold_int = 0

    url = item.get("link_url") or item.get("url") or ""
    if not url and goods_id:
        url = f"https://www.temu.com/goods.html?goods_id={goods_id}"

    bullets: list = []
    monthly_revenue = round(price_float * sold_int, 2) if price_float and sold_int else ""

    product = {
        "goods_id": goods_id,
        "asin": goods_id,
        "keyword": keyword,
        "url": url,
        "title": title,
        "price": price_str,
        "rating": str(rating) if rating is not None else "",
        "review_count": review_count,
        "sold_count": sold_int,
        "monthly_sales_estimate": sold_int or "",
        "monthly_revenue_estimate": monthly_revenue,
        "monthly_sales_range": "",
        "bsr_rank": "",
        "bsr_category": "",
        "bsr_display": "",
        "bullets": bullets,
        "is_valid": len(title) > 5 and price_float is not None,
        "crawl_time": datetime.now().isoformat(),
    }

    if goods_id:
        _product_cache[goods_id] = product

    return product


def _run_apify(keyword: str, max_results: int) -> list[dict]:
    from apify_client import ApifyClient

    client = ApifyClient(APIFY_API_TOKEN)
    run_input = {
        "searchQueries": [keyword],
        "currency": "USD",
        "maxResults": max_results,
    }
    print(f"[temu apify] running actor {APIFY_TEMU_ACTOR} for {keyword!r} max={max_results}")
    run = client.actor(APIFY_TEMU_ACTOR).call(run_input=run_input)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    print(f"[temu apify] got {len(items)} raw items")
    return items


async def scrape_temu_products(
    keyword: str,
    max_pages: int = 2,
    max_valid: int = 5,
    headless: bool = False,
) -> list[dict]:
    raw_items = await asyncio.to_thread(_run_apify, keyword, max(20, max_valid))

    valid: list[dict] = []
    for item in raw_items:
        if len(valid) >= max_valid:
            break
        mapped = _map_apify_item(item, keyword)
        if mapped["is_valid"]:
            valid.append(mapped)
            print(f"[temu] {mapped['goods_id']} ✓ {mapped['title'][:50]}")
        else:
            print(f"[temu] {mapped['goods_id']} ✗ 无效")

    print(f"[temu] 共 {len(valid)} 个有效产品")
    return valid


def _llm_analyze_product(product: dict) -> dict:
    from openai import OpenAI

    client = OpenAI(base_url=LLM_BASE_URL, api_key="EMPTY")
    sys_prompt = (
        "你是一个电商竞品分析助手。"
        "根据商品的标题、价格、评分和销量，用简体中文生成一段竞品定位分析。"
        "输出严格的 JSON，字段为 overall（一段中文分析，100-150字）。"
        "只输出 JSON，不要任何额外说明。"
    )
    user_prompt = (
        f"商品标题：{product.get('title', '')}\n"
        f"价格：{product.get('price', '')}\n"
        f"评分：{product.get('rating', '')}\n"
        f"销量：{product.get('sold_count', '')}\n"
        f"评论数：{product.get('review_count', '')}\n"
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


async def scrape_temu_reviews(goods_id: str, product_url: str, max_reviews: int = 60) -> dict:
    product = _product_cache.get(goods_id, {})
    if not product:
        return {"pros": [], "cons": [], "overall": ""}
    print(f"[temu llm] analyzing {goods_id} based on title/price/rating/sales...")
    return await asyncio.to_thread(_llm_analyze_product, product)

