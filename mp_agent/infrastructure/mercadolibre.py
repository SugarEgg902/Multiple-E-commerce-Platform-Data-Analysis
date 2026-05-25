from __future__ import annotations

import asyncio
import json

import pymysql
import pymysql.cursors

_DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "rootroot",
    "database": "shadowcraw_db",
    "charset": "utf8mb4",
}



def _query_products_sync(brand: str, limit: int) -> list[dict]:
    conn = pymysql.connect(**_DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM mercadolibre WHERE brand LIKE %s LIMIT %s",
                (f"%{brand}%", limit),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _map_row(r: dict) -> dict:
    return {
        "product_id": r.get("product_id", ""),
        "title": r.get("product_name", ""),
        "brand": r.get("brand", ""),
        "sub_category": r.get("sub_category", ""),
        "price": r.get("price", ""),
        "rating": r.get("review_rating"),
        "review_count": r.get("review_count"),
        "sales_7_days": r.get("sales_7_days"),
        "sales_30_days": r.get("sales_30_days"),
        "sales_90_days": r.get("sales_90_days"),
        "total_sales": r.get("total_sales"),
        "revenue": r.get("revenue"),
        "sales_growth_rate": r.get("sales_growth_rate"),
        "bsr": r.get("bsr"),
        "stock_quantity": r.get("stock_quantity"),
        "stock_type": r.get("stock_type"),
        "store_type": r.get("store_type"),
        "store_name": r.get("store_name"),
        "launch_date": str(r.get("launch_date", ""))[:10],
        "product_status": r.get("product_status"),
        "conversion_rate": r.get("conversion_rate"),
        "url": r.get("product_url", ""),
        "is_valid": bool(r.get("product_name")),
    }


async def fetch_mercadolibre_products(brand: str, count: int) -> list[dict]:
    """Query shadowcraw_db for MercadoLibre products matching brand."""
    rows = await asyncio.to_thread(_query_products_sync, brand, count * 4)
    products = [_map_row(r) for r in rows]
    valid = [p for p in products if p["is_valid"]]
    return valid[:count]



def _llm_analyze_product(product: dict) -> dict:
    """Generate pros/cons/overall from product data (no reviews available)."""
    from openai import OpenAI
    from config.config import ANALYSIS_LLM_BASE_URL, ANALYSIS_LLM_MODEL

    client = OpenAI(base_url=ANALYSIS_LLM_BASE_URL, api_key="EMPTY")
    prompt = {
        "title": product.get("title", ""),
        "price_mxn": product.get("price", ""),
        "sales_30_days": product.get("sales_30_days", ""),
        "total_sales": product.get("total_sales", ""),
        "sales_growth_rate": product.get("sales_growth_rate", ""),
        "bsr_rank": product.get("bsr", ""),
        "store_type": product.get("store_type", ""),
        "sub_category": product.get("sub_category", ""),
        "review_rating": product.get("rating", ""),
        "review_count": product.get("review_count", ""),
        "conversion_rate": product.get("conversion_rate", ""),
    }
    resp = client.chat.completions.create(
        model=ANALYSIS_LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是电商竞品分析助手，专注于 MercadoLibre 平台。"
                    "根据商品信息（标题、价格、销量、增长率、BSR、转化率等），"
                    "从竞争优势和劣势角度分析该商品。"
                    "只输出严格 JSON，包含三个字段："
                    "pros（list[str]，优势列表，简体中文）、"
                    "cons（list[str]，劣势列表，简体中文）、"
                    "overall（string，一句话综合评价，简体中文）。"
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    result = json.loads(resp.choices[0].message.content or "{}")
    return {
        "pros": result.get("pros", []),
        "cons": result.get("cons", []),
        "overall": result.get("overall", ""),
    }
