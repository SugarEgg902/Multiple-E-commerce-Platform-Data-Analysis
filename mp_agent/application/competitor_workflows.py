from __future__ import annotations

from pathlib import Path

from mp_agent.domain.analysis import build_analysis_row
from mp_agent.infrastructure.product_cache import load_platform_cache, save_cached_entry
from mp_agent.infrastructure.amazon import scrape_amazon_products, summarize_reviews
from mp_agent.infrastructure.artifacts import CSV_COLUMNS, EBAY_CSV_COLUMNS, TEMU_CSV_COLUMNS, OZON_CSV_COLUMNS, OTTO_CSV_COLUMNS, write_analysis_csv, write_ebay_analysis_csv, write_temu_analysis_csv, write_ozon_analysis_csv, write_otto_analysis_csv
from mp_agent.infrastructure.ebay import scrape_ebay_products, scrape_ebay_reviews
from mp_agent.infrastructure.temu import scrape_temu_products, scrape_temu_reviews
from mp_agent.infrastructure.ozon import scrape_ozon_products, scrape_ozon_reviews
from mp_agent.infrastructure.otto import scrape_otto_products, scrape_otto_reviews


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

    cache = load_platform_cache("amazon")
    new_products = [p for p in products if p.get("asin", "") not in cache][:count]
    skipped = len(products) - len(new_products)
    if skipped:
        await emit({"type": "tool_status", "tool": "run_amazon_competitor_analysis",
                    "message": f"已过滤 {skipped} 个重复商品，分析 {len(new_products)} 个新商品"})
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
        save_cached_entry("amazon", asin, row)
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

    cache = load_platform_cache("ebay")
    new_products = [p for p in products if p.get("item_id", "") not in cache][:count]
    skipped = len(products) - len(new_products)
    if skipped:
        await emit({"type": "tool_status", "tool": "run_ebay_competitor_analysis",
                    "message": f"已过滤 {skipped} 个重复商品，分析 {len(new_products)} 个新商品"})
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
        save_cached_entry("ebay", item_id, row)
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

    cache = load_platform_cache("temu")
    new_products = [p for p in products if p.get("goods_id", "") not in cache][:count]
    skipped = len(products) - len(new_products)
    if skipped:
        await emit({"type": "tool_status", "tool": "run_temu_competitor_analysis",
                    "message": f"已过滤 {skipped} 个重复商品，分析 {len(new_products)} 个新商品"})
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
        save_cached_entry("temu", goods_id, row)
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

    cache = load_platform_cache("ozon")
    new_products = [p for p in products if p.get("product_id", "") not in cache][:count]
    skipped = len(products) - len(new_products)
    if skipped:
        await emit({"type": "tool_status", "tool": "run_ozon_competitor_analysis",
                    "message": f"已过滤 {skipped} 个重复商品，分析 {len(new_products)} 个新商品"})
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
        save_cached_entry("ozon", product_id, row)
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

    cache = load_platform_cache("otto")
    new_products = [p for p in products if p.get("variation_id", "") not in cache][:count]
    skipped = len(products) - len(new_products)
    if skipped:
        await emit({"type": "tool_status", "tool": "run_otto_competitor_analysis",
                    "message": f"已过滤 {skipped} 个重复商品，分析 {len(new_products)} 个新商品"})
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
        save_cached_entry("otto", variation_id, row)
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

