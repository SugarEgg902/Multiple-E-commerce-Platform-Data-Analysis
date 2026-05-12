from __future__ import annotations

from pathlib import Path

from mp_agent.domain.analysis import build_analysis_row
from mp_agent.infrastructure.amazon import scrape_amazon_products, summarize_reviews
from mp_agent.infrastructure.artifacts import CSV_COLUMNS, EBAY_CSV_COLUMNS, write_analysis_csv, write_ebay_analysis_csv
from mp_agent.infrastructure.ebay import scrape_ebay_products, scrape_ebay_reviews


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

    products = await scrape_products(brand, max_valid=count)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    rows: list[dict] = []
    for product in products:
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

        rows.append(
            build_row_fn(
                brand=brand,
                product=product,
                review_summary=review_summary,
            )
        )

    csv_path = write_csv_fn(rows, brand=brand, count=count)
    return {
        "platform": "amazon",
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": CSV_COLUMNS,
        "preview_rows": [[row.get(column, "") for column in CSV_COLUMNS] for row in rows],
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

    products = await scrape_products(brand, max_valid=count)
    if not products:
        raise RuntimeError("没有抓取到有效商品")

    rows: list[dict] = []
    for product in products:
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
        rows.append(
            build_row_fn(
                brand=brand,
                product=ebay_product,
                review_summary=review_summary,
            )
        )

    csv_path = write_csv_fn(rows, brand=brand, count=count)
    return {
        "platform": "ebay",
        "brand": brand,
        "count": count,
        "rows": rows,
        "preview_columns": EBAY_CSV_COLUMNS,
        "preview_rows": [[row.get(column, "") for column in EBAY_CSV_COLUMNS] for row in rows],
        "filename": csv_path.name,
        "download_url": download_url_builder(csv_path),
        "summary": f"已完成 {len(rows)} 个 eBay 竞品分析",
    }
