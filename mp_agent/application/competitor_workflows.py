from __future__ import annotations

import asyncio as _asyncio
import re as _re
from datetime import datetime as _dt, timedelta as _timedelta
from pathlib import Path

from config.config import CACHE_TTL_DAYS
from mp_agent.domain.analysis import build_analysis_row
from mp_agent.dao.repository import (
    upsert_product, save_detail, save_snapshot, save_analysis_result, product_exists,
    get_latest_crawl_time, has_running_crawl_task,
)
from mp_agent.dao.matching import schedule_matching
from mp_agent.infrastructure.amazon import scrape_amazon_products, summarize_reviews
from mp_agent.infrastructure.artifacts import CSV_COLUMNS, EBAY_CSV_COLUMNS, TEMU_CSV_COLUMNS, OZON_CSV_COLUMNS, OTTO_CSV_COLUMNS, ALLEGRO_CSV_COLUMNS, TIKTOKSHOP_CSV_COLUMNS, CDISCOUNT_CSV_COLUMNS, ALIEXPRESS_CSV_COLUMNS, write_analysis_csv, write_ebay_analysis_csv, write_temu_analysis_csv, write_ozon_analysis_csv, write_otto_analysis_csv, write_allegro_analysis_csv, write_tiktokshop_analysis_csv, write_cdiscount_analysis_csv, write_aliexpress_analysis_csv
from mp_agent.infrastructure.ebay import scrape_ebay_products, scrape_ebay_reviews
from mp_agent.infrastructure.temu import scrape_temu_products, scrape_temu_reviews, _llm_analyze_product as _temu_llm_analyze
from mp_agent.infrastructure.ozon import scrape_ozon_products, scrape_ozon_reviews
from mp_agent.infrastructure.otto import scrape_otto_products, scrape_otto_reviews, _llm_analyze_product as _otto_llm_analyze
from mp_agent.infrastructure.allegro import scrape_allegro_products, scrape_allegro_reviews, _llm_analyze_product as _allegro_llm_analyze
from mp_agent.infrastructure.tiktokshop import scrape_tiktokshop_products, scrape_tiktokshop_reviews, _llm_analyze_product as _tiktokshop_llm_analyze
from mp_agent.infrastructure.cdiscount import scrape_cdiscount_products, scrape_cdiscount_reviews, _llm_analyze_product as _cdiscount_llm_analyze
from mp_agent.infrastructure.aliexpress import scrape_aliexpress_products, scrape_aliexpress_reviews, _llm_analyze_product as _aliexpress_llm_analyze


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


async def _noop_emit(_: dict) -> None:
    pass


# Tracks (platform, keyword) pairs currently being re-crawled in the background.
_running_background_crawls: set[tuple[str, str]] = set()


async def _try_serve_from_cache(
    platform: str,
    brand: str,
    count: int,
    emit,
    preview_cols: list[str],
    download_url_builder,
    background_fn,
) -> dict | None:
    """
    Stale-while-revalidate cache check.

    Returns a result dict on cache hit (Cases A and B), None on cache miss (Case C).
    For Case B (stale), also fires a background re-crawl task.
    """
    try:
        latest = await get_latest_crawl_time(platform, brand)
    except Exception:
        return None  # DB unavailable — fall through to live crawl

    if latest is None:
        return None  # Case C: no data in DB

    try:
        from mp_agent.infrastructure.artifacts import _query_analysis_rows, export_platform_csv_from_db
        rows = await _query_analysis_rows(platform, brand, count)
    except Exception:
        return None

    if not rows or len(rows) < count:
        return None  # 数据不足 count 条 — 触发重新爬取

    try:
        csv_path = await export_platform_csv_from_db(platform, brand, count)
    except Exception:
        return None

    age = _dt.utcnow() - latest
    ttl = _timedelta(days=CACHE_TTL_DAYS)
    date_str = latest.strftime("%Y-%m-%d")

    result: dict = {
        "platform": platform,
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": preview_cols,
        "preview_rows": [[row.get(col, "") for col in preview_cols] for row in rows],
        "filename": csv_path.name,
        "download_url": download_url_builder(csv_path),
        "from_cache": True,
    }

    if age > ttl:
        # Case B: stale — return cached data and trigger background re-crawl
        key = (platform, brand)
        already_running = key in _running_background_crawls
        if not already_running:
            try:
                already_running = await has_running_crawl_task(platform, brand)
            except Exception:
                pass

        if not already_running:
            _asyncio.get_running_loop().create_task(_background_crawl(platform, brand, background_fn))
            await emit({
                "type": "tool_status",
                "tool": f"run_{platform}_competitor_analysis",
                "message": f"缓存已过期（{int(age.total_seconds() / 86400)} 天），已在后台触发重新爬取。",
            })
        result["summary"] = f"已从缓存返回 {len(rows)} 个竞品分析（数据时间：{date_str}，后台更新中）"
    else:
        # Case A: fresh — return cached data directly
        await emit({
            "type": "tool_status",
            "tool": f"run_{platform}_competitor_analysis",
            "message": f"命中缓存，直接返回（数据时间：{date_str}）",
        })
        result["summary"] = f"已从缓存返回 {len(rows)} 个竞品分析（数据时间：{date_str}）"

    return result


async def _background_crawl(platform: str, brand: str, workflow_coro_fn) -> None:
    """Fire-and-forget wrapper that tracks running background crawls."""
    key = (platform, brand)
    _running_background_crawls.add(key)
    try:
        await workflow_coro_fn()
    except Exception:
        pass
    finally:
        _running_background_crawls.discard(key)


async def run_amazon_competitor_analysis(
    brand: str,
    count: int,
    emit,
    *,
    _skip_cache: bool = False,
    scrape_products=scrape_amazon_products,
    summarize_reviews_fn=summarize_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    _preview_cols = ["ASIN", "价格", "评分", "月销量估算值", "月销售额估算", "综合分析"]
    if not _skip_cache:
        _cached = await _try_serve_from_cache(
            "amazon", brand, count, emit, _preview_cols, download_url_builder,
            lambda: run_amazon_competitor_analysis(brand, count, _noop_emit, _skip_cache=True),
        )
        if _cached is not None:
            return _cached

    await emit(
        {
            "type": "tool_status",
            "tool": "run_amazon_competitor_analysis",
            "message": "正在抓取 Amazon 商品...",
        }
    )

    products = await scrape_products(brand, max_valid=count)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    if _skip_cache:
        new_products = products[:count]
    else:
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "extra": {
                    "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                    "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
                    "bsr_rank": product.get("bsr_rank"),
                },
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

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
    _skip_cache: bool = False,
    scrape_products=scrape_ebay_products,
    scrape_reviews_fn=scrape_ebay_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_ebay_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    _preview_cols = ["商品id", "价格", "评分", "月销量估算值", "月销售额估算", "综合分析"]
    if not _skip_cache:
        _cached = await _try_serve_from_cache(
            "ebay", brand, count, emit, _preview_cols, download_url_builder,
            lambda: run_ebay_competitor_analysis(brand, count, _noop_emit, _skip_cache=True),
        )
        if _cached is not None:
            return _cached

    await emit(
        {
            "type": "tool_status",
            "tool": "run_ebay_competitor_analysis",
            "message": "正在抓取 eBay 商品...",
        }
    )

    products = await scrape_products(brand, max_valid=count)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    if _skip_cache:
        new_products = products[:count]
    else:
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "sold_count": product.get("sold_count"),
                "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
                "condition": product.get("condition"),
                "seller_feedback": product.get("seller_feedback"),
                "bullets": product.get("bullets"),
            })
            await save_snapshot(_product_db_id, "ebay", str(item_id), {
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "extra": {
                    "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                    "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
                },
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

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
    _skip_cache: bool = False,
    scrape_products=scrape_temu_products,
    scrape_reviews_fn=scrape_temu_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_temu_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    _preview_cols = ["商品id", "价格", "评分", "月销量估算值", "月销售额估算", "综合分析"]
    if not _skip_cache:
        _cached = await _try_serve_from_cache(
            "temu", brand, count, emit, _preview_cols, download_url_builder,
            lambda: run_temu_competitor_analysis(brand, count, _noop_emit, _skip_cache=True),
        )
        if _cached is not None:
            return _cached

    await emit(
        {
            "type": "tool_status",
            "tool": "run_temu_competitor_analysis",
            "message": "正在抓取 Temu 商品...",
        }
    )

    products = await scrape_products(brand, max_valid=count)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    if _skip_cache:
        new_products = products[:count]
    else:
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
        if not review_summary.get("overall"):
            try:
                review_summary = await _asyncio.to_thread(_temu_llm_analyze, temu_product)
            except Exception:
                pass
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "goods_id": product.get("goods_id"),
                "sold_count": product.get("sold_count"),
                "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
                "bullets": product.get("bullets"),
            })
            await save_snapshot(_product_db_id, "temu", str(goods_id), {
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "extra": {
                    "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                    "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
                },
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

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
    _skip_cache: bool = False,
    scrape_products=scrape_ozon_products,
    scrape_reviews_fn=scrape_ozon_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_ozon_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    _preview_cols = ["商品id", "价格", "评分", "总销量估算", "总销售额估算", "综合分析"]
    if not _skip_cache:
        _cached = await _try_serve_from_cache(
            "ozon", brand, count, emit, _preview_cols, download_url_builder,
            lambda: run_ozon_competitor_analysis(brand, count, _noop_emit, _skip_cache=True),
        )
        if _cached is not None:
            return _cached

    await emit(
        {
            "type": "tool_status",
            "tool": "run_ozon_competitor_analysis",
            "message": "正在抓取 OZON 商品...",
        }
    )

    products = await scrape_products(brand, max_valid=count)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    if _skip_cache:
        new_products = products[:count]
    else:
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "extra": {
                    "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                    "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
                },
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

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
    _skip_cache: bool = False,
    scrape_products=scrape_otto_products,
    scrape_reviews_fn=scrape_otto_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_otto_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    _preview_cols = ["ASIN", "价格", "评分", "总销量估算", "总销售额估算", "综合分析"]
    if not _skip_cache:
        _cached = await _try_serve_from_cache(
            "otto", brand, count, emit, _preview_cols, download_url_builder,
            lambda: run_otto_competitor_analysis(brand, count, _noop_emit, _skip_cache=True),
        )
        if _cached is not None:
            return _cached

    await emit({"type": "tool_status", "tool": "run_otto_competitor_analysis", "message": "正在抓取 OTTO 商品..."})

    products = await scrape_products(brand, max_valid=count)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    if _skip_cache:
        new_products = products[:count]
    else:
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
        if not review_summary.get("overall"):
            try:
                review_summary = await _asyncio.to_thread(_otto_llm_analyze, otto_product)
            except Exception:
                pass
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "extra": {
                    "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                    "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
                },
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

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
    _skip_cache: bool = False,
    scrape_products=scrape_allegro_products,
    scrape_reviews_fn=scrape_allegro_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_allegro_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    _preview_cols = ["商品id", "价格", "评分", "月销量估算值", "月销售额估算", "综合分析"]
    if not _skip_cache:
        _cached = await _try_serve_from_cache(
            "allegro", brand, count, emit, _preview_cols, download_url_builder,
            lambda: run_allegro_competitor_analysis(brand, count, _noop_emit, _skip_cache=True),
        )
        if _cached is not None:
            return _cached

    await emit({"type": "tool_status", "tool": "run_allegro_competitor_analysis", "message": "正在抓取 Allegro 商品..."})

    products = await scrape_products(brand, max_valid=count)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    if _skip_cache:
        new_products = products[:count]
    else:
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
        if not review_summary.get("overall"):
            try:
                review_summary = await _asyncio.to_thread(_allegro_llm_analyze, allegro_product)
            except Exception:
                pass
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "extra": {
                    "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                    "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
                },
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

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
    _skip_cache: bool = False,
    scrape_products=scrape_tiktokshop_products,
    scrape_reviews_fn=scrape_tiktokshop_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_tiktokshop_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    _preview_cols = ["商品id", "价格", "评分", "评论数", "卖家", "月销量估算值", "综合分析"]
    if not _skip_cache:
        _cached = await _try_serve_from_cache(
            "tiktokshop", brand, count, emit, _preview_cols, download_url_builder,
            lambda: run_tiktokshop_competitor_analysis(brand, count, _noop_emit, region=region, _skip_cache=True),
        )
        if _cached is not None:
            return _cached

    await emit({"type": "tool_status", "tool": "run_tiktokshop_competitor_analysis", "message": "正在抓取 TikTok Shop 商品..."})

    products = await scrape_products(brand, max_valid=count, region=region)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    if _skip_cache:
        new_products = products[:count]
    else:
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
        if not review_summary.get("overall"):
            try:
                review_summary = await _asyncio.to_thread(_tiktokshop_llm_analyze, tiktok_product)
            except Exception:
                pass
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "seller": product.get("seller"),
                "sold_count": product.get("sold_count"),
                "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
            })
            await save_snapshot(_product_db_id, "tiktokshop", str(product_id), {
                "title": product.get("title"),
                "price_usd": _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "extra": {
                    "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                    "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
                },
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

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
    _skip_cache: bool = False,
    scrape_products=scrape_cdiscount_products,
    scrape_reviews_fn=scrape_cdiscount_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_cdiscount_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    _preview_cols = ["商品id", "价格", "原价", "卖家", "总类目", "综合分析"]
    if not _skip_cache:
        _cached = await _try_serve_from_cache(
            "cdiscount", brand, count, emit, _preview_cols, download_url_builder,
            lambda: run_cdiscount_competitor_analysis(brand, count, _noop_emit, _skip_cache=True),
        )
        if _cached is not None:
            return _cached

    await emit({"type": "tool_status", "tool": "run_cdiscount_competitor_analysis", "message": "正在抓取 Cdiscount 商品..."})

    products = await scrape_products(brand, max_valid=count)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    if _skip_cache:
        new_products = products[:count]
    else:
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

        # Cdiscount has no review API — use the product-level LLM analysis as review_summary
        # so that 优点评炼/缺点评炼/综合分析 are populated in the output row.
        if not review_summary.get("overall"):
            try:
                review_summary = await _asyncio.to_thread(_cdiscount_llm_analyze, cd_product)
            except Exception:
                pass

        row = build_row_fn(brand=brand, product=cd_product, review_summary=review_summary)
        row["原价"] = product.get("striked_price", "")
        row["卖家"] = product.get("seller", "")
        try:
            _product_db_id = await upsert_product({
                "platform": "cdiscount",
                "platform_product_id": str(product_id),
                "keyword": brand,
                "title": product.get("title"),
                "price_usd": product.get("price_usd") or _parse_price_usd(product.get("price", "")),
                "price_original": str(product.get("price", "")),
                "currency": "EUR",
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
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
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "extra": {
                    "monthly_sales_estimate": product.get("monthly_sales_estimate"),
                    "monthly_revenue_estimate": product.get("monthly_revenue_estimate"),
                },
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

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


ALIEXPRESS_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_aliexpress_competitor_analysis",
        "description": "在 AliExpress 上抓取指定品牌商品、生成竞品分析并导出 CSV。支持指定国家/地区，默认美国。",
        "parameters": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
                "country": {"type": "string", "default": "US"},
            },
            "required": ["brand", "count"],
        },
    },
}


async def run_aliexpress_competitor_analysis(
    brand: str,
    count: int,
    emit,
    country: str = "US",
    *,
    _skip_cache: bool = False,
    scrape_products=scrape_aliexpress_products,
    scrape_reviews_fn=scrape_aliexpress_reviews,
    build_row_fn=build_analysis_row,
    write_csv_fn=write_aliexpress_analysis_csv,
    download_url_builder=_default_download_url,
) -> dict:
    _preview_cols = ["商品id", "价格", "评分", "总销量", "总销售额", "综合分析"]
    if not _skip_cache:
        _cached = await _try_serve_from_cache(
            "aliexpress", brand, count, emit, _preview_cols, download_url_builder,
            lambda: run_aliexpress_competitor_analysis(brand, count, _noop_emit, country=country, _skip_cache=True),
        )
        if _cached is not None:
            return _cached

    await emit({"type": "tool_status", "tool": "run_aliexpress_competitor_analysis", "message": "正在抓取 AliExpress 商品..."})

    products = await scrape_products(brand, max_valid=count, country=country)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    if _skip_cache:
        new_products = products[:count]
    else:
        seen_new = []
        for _p in products:
            _pid = str(_p.get("product_id", ""))
            if _pid and not await product_exists("aliexpress", _pid):
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
        await emit({"type": "tool_status", "tool": "run_aliexpress_competitor_analysis",
                    "message": f"正在分析 {product_id} ..."})
        try:
            review_summary = await scrape_reviews_fn(product_id, product_url)
        except Exception:
            review_summary = {"pros": [], "cons": [], "overall": ""}

        ae_product = {**product, "asin": product_id, "url": product_url}
        if not review_summary.get("overall"):
            try:
                review_summary = await _asyncio.to_thread(_aliexpress_llm_analyze, ae_product)
            except Exception:
                pass

        row = build_row_fn(brand=brand, product=ae_product, review_summary=review_summary)
        row["总销量"] = product.get("总销量估算", "")
        row["总销售额"] = product.get("总销售额估算", "")
        row["折扣率"] = f"{product.get('discount_percentage', '')}%" if product.get("discount_percentage") else ""
        row["卖点"] = "；".join(product.get("selling_points") or [])
        row["原价"] = product.get("original_price", "")
        try:
            _product_db_id = await upsert_product({
                "platform": "aliexpress",
                "platform_product_id": str(product_id),
                "keyword": brand,
                "title": product.get("title"),
                "price_usd": product.get("price_usd"),
                "price_original": str(product.get("price", "")),
                "currency": product.get("currency", "USD"),
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "url": product.get("url"),
                "crawl_time": _dt.utcnow(),
            })
            await save_detail(_product_db_id, {
                "orders_count": product.get("orders_count"),
                "total_sales_estimate": product.get("总销量估算", ""),
                "total_revenue_estimate": product.get("总销售额估算", ""),
                "discount_percentage": product.get("discount_percentage"),
                "selling_points": product.get("selling_points"),
                "is_sponsored": product.get("is_sponsored"),
            })
            await save_snapshot(_product_db_id, "aliexpress", str(product_id), {
                "title": product.get("title"),
                "price_usd": product.get("price_usd"),
                "price_original": str(product.get("price", "")),
                "rating": product.get("rating") or None,
                "review_count": product.get("review_count") or None,
                "extra": {
                    "orders_count": product.get("orders_count"),
                    "total_sales_estimate": product.get("总销量估算", ""),
                    "total_revenue_estimate": product.get("总销售额估算", ""),
                    "discount_percentage": product.get("discount_percentage"),
                },
            })
            await save_analysis_result(_product_db_id, None, row)
            schedule_matching(_product_db_id, product.get("title", ""))
        except Exception:
            pass  # DB unavailable — persist to CSV only
        rows.append(row)

    csv_path = write_csv_fn(rows, brand=brand, count=count)
    return {
        "platform": "aliexpress",
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": _preview_cols,
        "preview_rows": [[row.get(col, "") for col in _preview_cols] for row in rows],
        "filename": csv_path.name,
        "download_url": download_url_builder(csv_path),
        "summary": f"已完成 {len(rows)} 个 AliExpress 竞品分析",
    }
