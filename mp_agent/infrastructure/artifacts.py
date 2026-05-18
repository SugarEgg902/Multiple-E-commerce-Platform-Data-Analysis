from __future__ import annotations

import csv
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = Path(
    os.getenv(
        "MP_AGENT_ARTIFACTS_DIR",
        Path(tempfile.gettempdir()) / "m_p_agent_artifacts",
    )
).resolve()


CSV_COLUMNS = [
    "搜索词",
    "ASIN",
    "url",
    "商品标题",
    "价格",
    "评分",
    "评论数",
    "总类目",
    "Best Sellers Rank",
    "月销量区间",
    "月销量估算值",
    "月销售额估算",
    "核心卖点",
    "优点评炼",
    "缺点评炼",
    "综合分析",
    "竞品定位",
]

EBAY_CSV_COLUMNS = [
    "搜索词",
    "商品id",
    "url",
    "商品标题",
    "价格",
    "评分",
    "总类目",
    "月销量估算值",
    "月销售额估算",
    "核心卖点",
    "优点评炼",
    "缺点评炼",
    "综合分析",
    "竞品定位",
]

TEMU_CSV_COLUMNS = EBAY_CSV_COLUMNS

OZON_CSV_COLUMNS = [
    "搜索词",
    "商品id",
    "url",
    "商品标题",
    "价格",
    "评分",
    "总类目",
    "总销量估算",
    "总销售额估算",
    "核心卖点",
    "优点评炼",
    "缺点评炼",
    "综合分析",
    "竞品定位",
]

OTTO_CSV_COLUMNS = [
    "搜索词",
    "ASIN",
    "url",
    "商品标题",
    "价格",
    "评分",
    "总类目",
    "总销量估算",
    "总销售额估算",
    "核心卖点",
    "优点评炼",
    "缺点评炼",
    "综合分析",
    "竞品定位",
]

ALLEGRO_CSV_COLUMNS = EBAY_CSV_COLUMNS

TIKTOKSHOP_CSV_COLUMNS = [
    "搜索词",
    "商品id",
    "url",
    "商品标题",
    "价格",
    "评分",
    "评论数",
    "卖家",
    "月销量估算值",
    "月销售额估算",
    "核心卖点",
    "优点评炼",
    "缺点评炼",
    "综合分析",
    "竞品定位",
]

CDISCOUNT_CSV_COLUMNS = [
    "搜索词",
    "商品id",
    "url",
    "商品标题",
    "价格",
    "原价",
    "卖家",
    "总类目",
    "核心卖点",
    "优点评炼",
    "缺点评炼",
    "综合分析",
    "竞品定位",
]

ALIEXPRESS_CSV_COLUMNS = [
    "搜索词",
    "商品id",
    "url",
    "商品标题",
    "价格",
    "原价",
    "折扣率",
    "评分",
    "总销量",
    "总销售额",
    "卖点",
    "总类目",
    "核心卖点",
    "优点评炼",
    "缺点评炼",
    "综合分析",
    "竞品定位",
]


def _sanitize_brand(brand: str) -> str:
    sanitized = re.sub(r"[^a-z0-9]+", "_", brand.lower()).strip("_")
    return sanitized or "brand"


def write_ebay_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"ebay_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"ebay_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EBAY_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in EBAY_CSV_COLUMNS})

    return path


def write_temu_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"temu_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"temu_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TEMU_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in TEMU_CSV_COLUMNS})

    return path


def write_ozon_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"ozon_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"ozon_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OZON_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in OZON_CSV_COLUMNS})

    return path


def write_otto_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"otto_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"otto_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OTTO_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in OTTO_CSV_COLUMNS})

    return path


def write_allegro_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"allegro_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"allegro_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ALLEGRO_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in ALLEGRO_CSV_COLUMNS})

    return path


def write_tiktokshop_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"tiktokshop_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"tiktokshop_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TIKTOKSHOP_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in TIKTOKSHOP_CSV_COLUMNS})

    return path


def write_cdiscount_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"cdiscount_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"cdiscount_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CDISCOUNT_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in CDISCOUNT_CSV_COLUMNS})

    return path


def write_aliexpress_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"aliexpress_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"aliexpress_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ALIEXPRESS_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in ALIEXPRESS_CSV_COLUMNS})

    return path


def write_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"amazon_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"amazon_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in CSV_COLUMNS})

    return path


async def _query_analysis_rows(platform: str, keyword: str, count: int) -> list[dict]:
    """Query DB for the latest analysis rows for a given platform + keyword."""
    from sqlalchemy import select, desc
    from mp_agent.dao.db import get_async_session
    from mp_agent.dao.models import PlatformProduct, PlatformProductDetail, AnalysisResult

    async with get_async_session() as session:
        result = await session.execute(
            select(PlatformProduct, PlatformProductDetail, AnalysisResult)
            .join(PlatformProductDetail, PlatformProductDetail.product_id == PlatformProduct.id, isouter=True)
            .join(AnalysisResult, AnalysisResult.product_id == PlatformProduct.id, isouter=True)
            .where(PlatformProduct.platform == platform, PlatformProduct.keyword == keyword)
            .order_by(desc(PlatformProduct.crawl_time))
            .limit(count)
        )
        rows = []
        for product, detail, analysis in result.all():
            extra = detail.extra if detail else {}
            row = {
                "搜索词": keyword,
                "商品id": product.platform_product_id,
                "ASIN": product.platform_product_id,
                "url": product.url or "",
                "商品标题": product.title or "",
                "价格": product.price_original or "",
                "评分": float(product.rating) if product.rating else "",
                "评论数": product.review_count or "",
                "总类目": analysis.category if analysis else "",
                "Best Sellers Rank": extra.get("bsr_display", ""),
                "月销量区间": extra.get("monthly_sales_range", ""),
                "月销量估算值": extra.get("monthly_sales_estimate", ""),
                "月销售额估算": extra.get("monthly_revenue_estimate", ""),
                "总销量估算": extra.get("total_sales_estimate", ""),
                "总销售额估算": extra.get("total_revenue_estimate", ""),
                "核心卖点": analysis.core_selling_points if analysis else "",
                "优点评炼": "；".join(analysis.pros or []) if analysis else "",
                "缺点评炼": "；".join(analysis.cons or []) if analysis else "",
                "综合分析": analysis.overall if analysis else "",
                "竞品定位": analysis.positioning if analysis else "",
                "卖家": extra.get("seller", ""),
                "原价": extra.get("original_price", ""),
                # AliExpress-specific
                "总销量": extra.get("total_sales_estimate", ""),
                "总销售额": extra.get("total_revenue_estimate", ""),
                "折扣率": f"{extra.get('discount_percentage')}%" if extra.get("discount_percentage") else "",
                "卖点": "；".join(extra.get("selling_points") or []),
            }
            rows.append(row)
        return rows


_PLATFORM_CSV_COLUMNS = {
    "amazon": CSV_COLUMNS,
    "ebay": EBAY_CSV_COLUMNS,
    "temu": TEMU_CSV_COLUMNS,
    "ozon": OZON_CSV_COLUMNS,
    "otto": OTTO_CSV_COLUMNS,
    "allegro": ALLEGRO_CSV_COLUMNS,
    "tiktokshop": TIKTOKSHOP_CSV_COLUMNS,
    "cdiscount": CDISCOUNT_CSV_COLUMNS,
    "aliexpress": ALIEXPRESS_CSV_COLUMNS,
}


async def export_platform_csv_from_db(
    platform: str, keyword: str, count: int, output_dir=None
) -> Path:
    """Export a CSV from DB for the given platform + keyword."""
    rows = await _query_analysis_rows(platform, keyword, count)
    columns = _PLATFORM_CSV_COLUMNS.get(platform, CSV_COLUMNS)
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_kw = _sanitize_brand(keyword)
    path = output_dir / f"{platform}_{safe_kw}_{count}_{timestamp}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path
