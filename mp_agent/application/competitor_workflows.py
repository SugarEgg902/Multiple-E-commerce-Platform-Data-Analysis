from __future__ import annotations

import re as _re
from datetime import datetime as _dt
from pathlib import Path

from mp_agent.domain.analysis import build_analysis_row
from mp_agent.dao.repository import (
    upsert_product, save_detail, save_snapshot, save_analysis_result, product_exists,
)
from mp_agent.dao.matching import schedule_matching
from mp_agent.infrastructure.amazon import scrape_amazon_products, summarize_reviews
from mp_agent.infrastructure.artifacts import CSV_COLUMNS, EBAY_CSV_COLUMNS, TEMU_CSV_COLUMNS, OZON_CSV_COLUMNS, OTTO_CSV_COLUMNS, ALLEGRO_CSV_COLUMNS, TIKTOKSHOP_CSV_COLUMNS, CDISCOUNT_CSV_COLUMNS, write_analysis_csv, write_ebay_analysis_csv, write_temu_analysis_csv, write_ozon_analysis_csv, write_otto_analysis_csv, write_allegro_analysis_csv, write_tiktokshop_analysis_csv, write_cdiscount_analysis_csv
from mp_agent.infrastructure.ebay import scrape_ebay_products, scrape_ebay_reviews
from mp_agent.infrastructure.temu import scrape_temu_products, scrape_temu_reviews
from mp_agent.infrastructure.ozon import scrape_ozon_products, scrape_ozon_reviews
from mp_agent.infrastructure.otto import scrape_otto_products, scrape_otto_reviews
from mp_agent.infrastructure.allegro import scrape_allegro_products, scrape_allegro_reviews
from mp_agent.infrastructure.tiktokshop import scrape_tiktokshop_products, scrape_tiktokshop_reviews
from mp_agent.infrastructure.cdiscount import scrape_cdiscount_products, scrape_cdiscount_reviews


AMAZON_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_amazon_competitor_analysis",
        "description": "在 Amazon 上抓取指定品牌商品、总结评论、生成竞品分析并导出 CSV。",
        "parameters": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["brand", "count"],
        },
    },
}


def _default_download_url(path: Path) -> str:
    return f"/api/download/{path.name}"


def _parse_price_usd(price_str: str) -> float | None:
    """Extract a float from a price string like '$29.99' or '29.99 USD'."""
    m = _re.search(r"[\d]+\.?\d*", str(price_str).replace(",", ""))
    return float(m.group()) if m else None


async def run_amazon_competitor_analysis(
    brand: str,
    count: int,
    emit,
    *,
    scrape_products=scrape_amazon_products,
    summarize_reviews_fn=summarize_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    await emit(
        {
            "type": "tool_status",
            "tool": "run_amazon_competitor_analysis",
            "message": "正在抓取 Amazon 商品...",
        }
    )

    products = await scrape_products(brand, max_valid=count * 2)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    seen_new = []
    for _p in products:
        _pid = str(_p.get("asin", ""))
        if _pid and not await product_exists("amazon", _pid):
            seen_new.append(_p)
        if len(seen_new) >= count:
            break
    new_products = seen_new
    if not new_products:
        raise RuntimeError("所有搜索结果均已分析过，未找到新商品")

    rows: list[dict] = []
    for product in new_products:
        asin = product.get("asin", "")
        await emit(
            {
                "type": "tool_status",
                "tool": "run_amazon_competitor_analysis",
                "message": f"正在分析 {asin} ...",
            }
        )
        try:
            review_summary = await summarize_reviews_fn(asin)
        except Exception:
            await emit(
                {
                    "type": "tool_status",
                    "tool": "run_amazon_competitor_analysis",
                    "level": "warning",
                    "message": f"{asin} 的评论总结失败，已使用空摘要继续。",
                }
            )
            review_summary = {"pros": [], "cons": [], "overall": ""}

        row = build_row_fn(brand=brand, product=product, review_summary=review_summary)
        try:
            _product_db_id = await upsert_product({
                "platform": "amazon",
                "platform_product_id": str(asin),
                "keyword": brand,
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "currency": "USD",
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "bsr_rank": product.get("bsr_rank"),
                "bsr_category": product.get("bsr_category"),
                "bsr_display": product.get("bsr_display"),
                "monthly_sales_range": product.get("monthly_sales_range"),
                "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
                "bullets": product.get("bullets"),
            })
            await save_snapshot(_product_db_id, "amazon", str(asin), {
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

    _preview_cols = ["ASIN", "价格", "评分", "月销量估算值", "月销售额估算", "综合分析"]
    csv_path = write_csv_fn(rows, brand=brand, count=count)
    return {
        "platform": "amazon",
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": _preview_cols,
        "preview_rows": [[row.get(col, "") for col in _preview_cols] for row in rows],
        "filename": csv_path.name,
        "download_url": download_url_builder(csv_path),
        "summary": f"已完成 {len(rows)} 个竞品分析",
    }


EBAY_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_ebay_competitor_analysis",
        "description": "在 eBay 上抓取指定品牌商品、总结评论、生成竞品分析并导出 CSV。",
        "parameters": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["brand", "count"],
        },
    },
}


async def run_ebay_competitor_analysis(
    brand: str,
    count: int,
    emit,
    *,
    scrape_products=scrape_ebay_products,
    scrape_reviews_fn=scrape_ebay_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_ebay_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    await emit(
        {
            "type": "tool_status",
            "tool": "run_ebay_competitor_analysis",
            "message": "正在抓取 eBay 商品...",
        }
    )

    products = await scrape_products(brand, max_valid=count * 2)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    seen_new = []
    for _p in products:
        _pid = str(_p.get("item_id", ""))
        if _pid and not await product_exists("ebay", _pid):
            seen_new.append(_p)
        if len(seen_new) >= count:
            break
    new_products = seen_new
    if not new_products:
        raise RuntimeError("所有搜索结果均已分析过，未找到新商品")

    rows: list[dict] = []
    for product in new_products:
        item_id = product.get("item_id", "")
        await emit(
            {
                "type": "tool_status",
                "tool": "run_ebay_competitor_analysis",
                "message": f"正在分析 {item_id} ...",
            }
        )
        try:
            review_summary = await scrape_reviews_fn(item_id)
        except Exception:
            await emit(
                {
                    "type": "tool_status",
                    "tool": "run_ebay_competitor_analysis",
                    "level": "warning",
                    "message": f"{item_id} 的评论总结失败，已使用空摘要继续。",
                }
            )
            review_summary = {"pros": [], "cons": [], "overall": ""}

        ebay_product = {
            **product,
            "asin": item_id,
            "url": product.get("url", f"https://www.ebay.com/itm/{item_id}"),
        }
        row = build_row_fn(brand=brand, product=ebay_product, review_summary=review_summary)
        try:
            _product_db_id = await upsert_product({
                "platform": "ebay",
                "platform_product_id": str(item_id),
                "keyword": brand,
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "currency": "USD",
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "sold_count": product.get("sold_count"),
                "condition": product.get("condition"),
                "seller_feedback": product.get("seller_feedback"),
                "bullets": product.get("bullets"),
            })
            await save_snapshot(_product_db_id, "ebay", str(item_id), {
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

    _preview_cols = ["商品id", "价格", "评分", "月销量估算值", "月销售额估算", "综合分析"]
    csv_path = write_csv_fn(rows, brand=brand, count=count)
    return {
        "platform": "ebay",
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": _preview_cols,
        "preview_rows": [[row.get(col, "") for col in _preview_cols] for row in rows],
        "filename": csv_path.name,
        "download_url": download_url_builder(csv_path),
        "summary": f"已完成 {len(rows)} 个 eBay 竞品分析",
    }


TEMU_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_temu_competitor_analysis",
        "description": "在 Temu 上抓取指定品牌商品、总结评论、生成竞品分析并导出 CSV。",
        "parameters": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["brand", "count"],
        },
    },
}


async def run_temu_competitor_analysis(
    brand: str,
    count: int,
    emit,
    *,
    scrape_products=scrape_temu_products,
    scrape_reviews_fn=scrape_temu_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_temu_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    await emit(
        {
            "type": "tool_status",
            "tool": "run_temu_competitor_analysis",
            "message": "正在抓取 Temu 商品...",
        }
    )

    products = await scrape_products(brand, max_valid=count * 2)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    seen_new = []
    for _p in products:
        _pid = str(_p.get("goods_id", ""))
        if _pid and not await product_exists("temu", _pid):
            seen_new.append(_p)
        if len(seen_new) >= count:
            break
    new_products = seen_new
    if not new_products:
        raise RuntimeError("所有搜索结果均已分析过，未找到新商品")

    rows: list[dict] = []
    for product in new_products:
        goods_id = product.get("goods_id", "")
        product_url = product.get("url", "")
        await emit(
            {
                "type": "tool_status",
                "tool": "run_temu_competitor_analysis",
                "message": f"正在分析 {goods_id} ...",
            }
        )
        try:
            review_summary = await scrape_reviews_fn(goods_id, product_url)
        except Exception:
            await emit(
                {
                    "type": "tool_status",
                    "tool": "run_temu_competitor_analysis",
                    "level": "warning",
                    "message": f"{goods_id} 的评论总结失败，已使用空摘要继续。",
                }
            )
            review_summary = {"pros": [], "cons": [], "overall": ""}

        temu_product = {**product, "asin": goods_id, "url": product_url}
        row = build_row_fn(brand=brand, product=temu_product, review_summary=review_summary)
        try:
            _product_db_id = await upsert_product({
                "platform": "temu",
                "platform_product_id": str(goods_id),
                "keyword": brand,
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "currency": "USD",
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "goods_id": product.get("goods_id"),
                "sold_count": product.get("sold_count"),
                "bullets": product.get("bullets"),
            })
            await save_snapshot(_product_db_id, "temu", str(goods_id), {
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

    _preview_cols = ["商品id", "价格", "评分", "月销量估算值", "月销售额估算", "综合分析"]
    csv_path = write_csv_fn(rows, brand=brand, count=count)
    return {
        "platform": "temu",
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": _preview_cols,
        "preview_rows": [[row.get(col, "") for col in _preview_cols] for row in rows],
        "filename": csv_path.name,
        "download_url": download_url_builder(csv_path),
        "summary": f"已完成 {len(rows)} 个 Temu 竞品分析",
    }


OZON_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_ozon_competitor_analysis",
        "description": "在 OZON 上抓取指定品牌商品、总结评论、生成竞品分析并导出 CSV。价格换算为美元。",
        "parameters": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["brand", "count"],
        },
    },
}


async def run_ozon_competitor_analysis(
    brand: str,
    count: int,
    emit,
    *,
    scrape_products=scrape_ozon_products,
    scrape_reviews_fn=scrape_ozon_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_ozon_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    await emit(
        {
            "type": "tool_status",
            "tool": "run_ozon_competitor_analysis",
            "message": "正在抓取 OZON 商品...",
        }
    )

    products = await scrape_products(brand, max_valid=count * 2)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    seen_new = []
    for _p in products:
        _pid = str(_p.get("product_id", ""))
        if _pid and not await product_exists("ozon", _pid):
            seen_new.append(_p)
        if len(seen_new) >= count:
            break
    new_products = seen_new
    if not new_products:
        raise RuntimeError("所有搜索结果均已分析过，未找到新商品")

    rows: list[dict] = []
    for product in new_products:
        product_id = product.get("product_id", "")
        product_url = product.get("url", f"https://www.ozon.ru/product/{product_id}/")
        await emit(
            {
                "type": "tool_status",
                "tool": "run_ozon_competitor_analysis",
                "message": f"正在分析 {product_id} ...",
            }
        )
        try:
            review_summary = await scrape_reviews_fn(product_id, product_url)
        except Exception:
            await emit(
                {
                    "type": "tool_status",
                    "tool": "run_ozon_competitor_analysis",
                    "level": "warning",
                    "message": f"{product_id} 的评论总结失败，已使用空摘要继续。",
                }
            )
            review_summary = {"pros": [], "cons": [], "overall": ""}

        ozon_product = {**product, "asin": product_id, "url": product_url}
        row = build_row_fn(brand=brand, product=ozon_product, review_summary=review_summary)
        try:
            _product_db_id = await upsert_product({
                "platform": "ozon",
                "platform_product_id": str(product_id),
                "keyword": brand,
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "currency": "USD",
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "sku": product.get("sku"),
                "total_sales_estimate": product.get("total_sales_estimate"),
                "total_revenue_estimate": product.get("total_revenue_estimate"),
                "breadcrumbs": product.get("breadcrumbs"),
                "short_characteristics": product.get("short_characteristics"),
            })
            await save_snapshot(_product_db_id, "ozon", str(product_id), {
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

    _preview_cols = ["商品id", "价格", "评分", "总销量估算", "总销售额估算", "综合分析"]
    csv_path = write_csv_fn(rows, brand=brand, count=count)
    return {
        "platform": "ozon",
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": _preview_cols,
        "preview_rows": [[row.get(col, "") for col in _preview_cols] for row in rows],
        "filename": csv_path.name,
        "download_url": download_url_builder(csv_path),
        "summary": f"已完成 {len(rows)} 个 OZON 竞品分析",
    }


OTTO_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_otto_competitor_analysis",
        "description": "在 OTTO.de 上抓取指定品牌商品、生成竞品分析并导出 CSV。价格换算为美元。",
        "parameters": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["brand", "count"],
        },
    },
}


async def run_otto_competitor_analysis(
    brand: str,
    count: int,
    emit,
    *,
    scrape_products=scrape_otto_products,
    scrape_reviews_fn=scrape_otto_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_otto_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    await emit({"type": "tool_status", "tool": "run_otto_competitor_analysis", "message": "正在抓取 OTTO 商品..."})

    products = await scrape_products(brand, max_valid=count * 2)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    seen_new = []
    for _p in products:
        _pid = str(_p.get("variation_id", ""))
        if _pid and not await product_exists("otto", _pid):
            seen_new.append(_p)
        if len(seen_new) >= count:
            break
    new_products = seen_new
    if not new_products:
        raise RuntimeError("所有搜索结果均已分析过，未找到新商品")

    rows: list[dict] = []
    for product in new_products:
        variation_id = product.get("variation_id", "")
        product_url = product.get("url", f"https://www.otto.de/suche/{brand}/")
        await emit({"type": "tool_status", "tool": "run_otto_competitor_analysis",
                    "message": f"正在分析 {variation_id} ..."})
        try:
            review_summary = await scrape_reviews_fn(variation_id, product_url)
        except Exception:
            review_summary = {"pros": [], "cons": [], "overall": ""}

        otto_product = {**product, "asin": variation_id, "url": product_url}
        row = build_row_fn(brand=brand, product=otto_product, review_summary=review_summary)
        try:
            _product_db_id = await upsert_product({
                "platform": "otto",
                "platform_product_id": str(variation_id),
                "keyword": brand,
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "currency": "USD",
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "variation_id": product.get("variation_id"),
                "total_sales_estimate": product.get("total_sales_estimate"),
                "total_revenue_estimate": product.get("total_revenue_estimate"),
                "description": product.get("description"),
                "bullets": product.get("bullets"),
            })
            await save_snapshot(_product_db_id, "otto", str(variation_id), {
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

    _preview_cols = ["ASIN", "价格", "评分", "总销量估算", "总销售额估算", "综合分析"]
    csv_path = write_csv_fn(rows, brand=brand, count=count)
    return {
        "platform": "otto",
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": _preview_cols,
        "preview_rows": [[row.get(col, "") for col in _preview_cols] for row in rows],
        "filename": csv_path.name,
        "download_url": download_url_builder(csv_path),
        "summary": f"已完成 {len(rows)} 个 OTTO 竞品分析",
    }



ALLEGRO_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_allegro_competitor_analysis",
        "description": "在 Allegro.pl 上抓取指定品牌商品、生成竞品分析并导出 CSV。价格换算为美元。",
        "parameters": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["brand", "count"],
        },
    },
}


async def run_allegro_competitor_analysis(
    brand: str,
    count: int,
    emit,
    *,
    scrape_products=scrape_allegro_products,
    scrape_reviews_fn=scrape_allegro_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_allegro_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    await emit({"type": "tool_status", "tool": "run_allegro_competitor_analysis", "message": "正在抓取 Allegro 商品..."})

    products = await scrape_products(brand, max_valid=count * 2)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    seen_new = []
    for _p in products:
        _pid = str(_p.get("product_id", ""))
        if _pid and not await product_exists("allegro", _pid):
            seen_new.append(_p)
        if len(seen_new) >= count:
            break
    new_products = seen_new
    if not new_products:
        raise RuntimeError("所有搜索结果均已分析过，未找到新商品")

    rows: list[dict] = []
    for product in new_products:
        product_id = product.get("product_id", "")
        product_url = product.get("url", "")
        await emit({"type": "tool_status", "tool": "run_allegro_competitor_analysis",
                    "message": f"正在分析 {product_id} ..."})
        try:
            review_summary = await scrape_reviews_fn(product_id, product_url)
        except Exception:
            review_summary = {"pros": [], "cons": [], "overall": ""}

        allegro_product = {**product, "asin": product_id, "url": product_url}
        row = build_row_fn(brand=brand, product=allegro_product, review_summary=review_summary)
        try:
            _product_db_id = await upsert_product({
                "platform": "allegro",
                "platform_product_id": str(product_id),
                "keyword": brand,
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "currency": "USD",
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "condition": product.get("condition"),
                "seller": product.get("seller"),
                "seller_rating": product.get("seller_rating"),
                "category": product.get("category"),
                "parameters": product.get("parameters"),
            })
            await save_snapshot(_product_db_id, "allegro", str(product_id), {
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

    _preview_cols = ["商品id", "价格", "评分", "月销量估算值", "月销售额估算", "综合分析"]
    csv_path = write_csv_fn(rows, brand=brand, count=count)
    return {
        "platform": "allegro",
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": _preview_cols,
        "preview_rows": [[row.get(col, "") for col in _preview_cols] for row in rows],
        "filename": csv_path.name,
        "download_url": download_url_builder(csv_path),
        "summary": f"已完成 {len(rows)} 个 Allegro 竞品分析",
    }


TIKTOKSHOP_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_tiktokshop_competitor_analysis",
        "description": "在 TikTok Shop 上抓取指定品牌商品、获取评论、生成竞品分析并导出 CSV。",
        "parameters": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
                "region": {"type": "string", "default": "us"},
            },
            "required": ["brand", "count"],
        },
    },
}


async def run_tiktokshop_competitor_analysis(
    brand: str,
    count: int,
    emit,
    region: str = "us",
    *,
    scrape_products=scrape_tiktokshop_products,
    scrape_reviews_fn=scrape_tiktokshop_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_tiktokshop_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    await emit({"type": "tool_status", "tool": "run_tiktokshop_competitor_analysis", "message": "正在抓取 TikTok Shop 商品..."})

    products = await scrape_products(brand, max_valid=count * 2, region=region)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    seen_new = []
    for _p in products:
        _pid = str(_p.get("product_id", ""))
        if _pid and not await product_exists("tiktokshop", _pid):
            seen_new.append(_p)
        if len(seen_new) >= count:
            break
    new_products = seen_new
    if not new_products:
        raise RuntimeError("所有搜索结果均已分析过，未找到新商品")

    rows: list[dict] = []
    for product in new_products:
        product_id = product.get("product_id", "")
        product_url = product.get("url", "")
        await emit({"type": "tool_status", "tool": "run_tiktokshop_competitor_analysis",
                    "message": f"正在分析 {product_id} ..."})
        try:
            review_summary = await scrape_reviews_fn(product_id, product_url)
        except Exception:
            review_summary = {"pros": [], "cons": [], "overall": ""}

        tiktok_product = {**product, "asin": product_id, "url": product_url}
        row = build_row_fn(brand=brand, product=tiktok_product, review_summary=review_summary)
        row["卖家"] = product.get("seller", "")
        row["评论数"] = product.get("review_count", "")
        try:
            _product_db_id = await upsert_product({
                "platform": "tiktokshop",
                "platform_product_id": str(product_id),
                "keyword": brand,
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "currency": "USD",
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "seller": product.get("seller"),
                "sold_count": product.get("sold_count"),
            })
            await save_snapshot(_product_db_id, "tiktokshop", str(product_id), {
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

    _preview_cols = ["商品id", "价格", "评分", "评论数", "卖家", "月销量估算值", "综合分析"]
    csv_path = write_csv_fn(rows, brand=brand, count=count)
    return {
        "platform": "tiktokshop",
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": _preview_cols,
        "preview_rows": [[row.get(col, "") for col in _preview_cols] for row in rows],
        "filename": csv_path.name,
        "download_url": download_url_builder(csv_path),
        "summary": f"已完成 {len(rows)} 个 TikTok Shop 竞品分析",
    }


CDISCOUNT_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_cdiscount_competitor_analysis",
        "description": "在 Cdiscount.com 上抓取指定品牌商品、生成竞品分析并导出 CSV。价格为欧元。",
        "parameters": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["brand", "count"],
        },
    },
}


async def run_cdiscount_competitor_analysis(
    brand: str,
    count: int,
    emit,
    *,
    scrape_products=scrape_cdiscount_products,
    scrape_reviews_fn=scrape_cdiscount_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_cdiscount_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    await emit({"type": "tool_status", "tool": "run_cdiscount_competitor_analysis", "message": "正在抓取 Cdiscount 商品..."})

    products = await scrape_products(brand, max_valid=count * 2)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    seen_new = []
    for _p in products:
        _pid = str(_p.get("product_id", ""))
        if _pid and not await product_exists("cdiscount", _pid):
            seen_new.append(_p)
        if len(seen_new) >= count:
            break
    new_products = seen_new
    if not new_products:
        raise RuntimeError("所有搜索结果均已分析过，未找到新商品")

    rows: list[dict] = []
    for product in new_products:
        product_id = product.get("product_id", "")
        product_url = product.get("url", "")
        await emit({"type": "tool_status", "tool": "run_cdiscount_competitor_analysis",
                    "message": f"正在分析 {product_id} ..."})
        try:
            review_summary = await scrape_reviews_fn(product_id, product_url)
        except Exception:
            review_summary = {"pros": [], "cons": [], "overall": ""}

        cd_product = {**product, "asin": product_id, "url": product_url}
        row = build_row_fn(brand=brand, product=cd_product, review_summary=review_summary)
        row["原价"] = product.get("striked_price", "")
        row["卖家"] = product.get("seller", "")
        try:
            _product_db_id = await upsert_product({
                "platform": "cdiscount",
                "platform_product_id": str(product_id),
                "keyword": brand,
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "currency": "USD",
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "original_price": product.get("original_price"),
                "seller": product.get("seller"),
                "category": product.get("category"),
                "bullet_points": product.get("bullet_points"),
            })
            await save_snapshot(_product_db_id, "cdiscount", str(product_id), {
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating"),
                "review_count": product.get("review_count"),
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

    _preview_cols = ["商品id", "价格", "原价", "卖家", "总类目", "综合分析"]
    csv_path = write_csv_fn(rows, brand=brand, count=count)
    return {
        "platform": "cdiscount",
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": _preview_cols,
        "preview_rows": [[row.get(col, "") for col in _preview_cols] for row in rows],
        "filename": csv_path.name,
        "download_url": download_url_builder(csv_path),
        "summary": f"已完成 {len(rows)} 个 Cdiscount 竞品分析",
    }
