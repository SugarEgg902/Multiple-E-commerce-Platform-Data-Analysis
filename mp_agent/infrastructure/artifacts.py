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

MERCADOLIBRE_CSV_COLUMNS = [
    "搜索词",
    "商品id",
    "url",
    "商品标题",
    "品牌",
    "子类目",
    "价格(MXN)",
    "评分",
    "评论数",
    "30天销量",
    "月销售额(MXN)",
    "总销量",
    "销量增长率",
    "转化率",
    "BSR排名",
    "库存数量",
    "库存类型",
    "店铺类型",
    "店铺名称",
    "上架日期",
    "核心卖点",
    "优点评炼",
    "缺点评炼",
    "综合分析",
    "竞品定位",
    "总类目",
]

KAUFLAND_CSV_COLUMNS = [
    "搜索词",
    "商品id",
    "url",
    "商品标题",
    "价格",
    "评分",
    "评论数",
    "卖家",
    "卖家数量",
    "库存状态",
    "配送费用",
    "规格参数",
    "总类目",
    "核心卖点",
    "优点评炼",
    "缺点评炼",
    "综合分析",
    "竞品定位",
]

WORTEN_CSV_COLUMNS = [
    "搜索词",
    "商品id",
    "url",
    "商品标题",
    "品牌",
    "价格(EUR)",
    "价格(USD)",
    "评分",
    "评论数",
    "库存状态",
    "核心卖点",
    "优点评炼",
    "缺点评炼",
    "综合分析",
    "竞品定位",
]

EPRICE_CSV_COLUMNS = [
    "搜索词",
    "商品id",
    "url",
    "商品标题",
    "品牌",
    "价格(EUR)",
    "价格(USD)",
    "原价(EUR)",
    "折扣率",
    "评分",
    "评论数",
    "卖家",
    "库存状态",
    "规格参数",
    "核心卖点",
    "优点评炼",
    "缺点评炼",
    "综合分析",
    "竞品定位",
]

MULTI_PLATFORM_CSV_COLUMNS = [
    "platform",
    "搜索词",
    "商品id",
    "ASIN",
    "url",
    "商品标题",
    "品牌",
    "价格",
    "价格(MXN)",
    "原价",
    "折扣率",
    "评分",
    "评论数",
    "卖家",
    "总类目",
    "子类目",
    "Best Sellers Rank",
    "BSR排名",
    "月销量区间",
    "月销量估算值",
    "月销售额估算",
    "30天销量",
    "月销售额(MXN)",
    "总销量估算",
    "总销售额估算",
    "总销量",
    "总销售额",
    "卖点",
    "销量增长率",
    "转化率",
    "库存数量",
    "库存类型",
    "店铺类型",
    "店铺名称",
    "上架日期",
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


def write_mercadolibre_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"mercadolibre_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"mercadolibre_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MERCADOLIBRE_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in MERCADOLIBRE_CSV_COLUMNS})

    return path


def write_kaufland_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"kaufland_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"kaufland_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=KAUFLAND_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in KAUFLAND_CSV_COLUMNS})

    return path


def write_worten_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"worten_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"worten_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=WORTEN_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in WORTEN_CSV_COLUMNS})

    return path


def write_eprice_analysis_csv(rows: list[dict], brand: str, count: int, output_dir=None) -> Path:
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_brand = _sanitize_brand(brand)
    path = output_dir / f"eprice_{safe_brand}_{count}_{timestamp}.csv"

    suffix = 1
    while path.exists():
        path = output_dir / f"eprice_{safe_brand}_{count}_{timestamp}_{suffix}.csv"
        suffix += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EPRICE_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in EPRICE_CSV_COLUMNS})

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
    from sqlalchemy import select, desc, func
    from mp_agent.dao.db import get_async_session
    from mp_agent.dao.models import PlatformProduct, PlatformProductDetail, AnalysisResult

    async with get_async_session() as session:
        result = await session.execute(
            select(PlatformProduct, PlatformProductDetail, AnalysisResult)
            .join(PlatformProductDetail, PlatformProductDetail.product_id == PlatformProduct.id, isouter=True)
            .join(AnalysisResult, AnalysisResult.product_id == PlatformProduct.id, isouter=True)
            .where(PlatformProduct.platform == platform, func.lower(PlatformProduct.keyword) == keyword.lower())
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
            if platform == "mercadolibre":
                row.update({
                    "30天销量": extra.get("sales_30_days", ""),
                    "月销售额(MXN)": extra.get("revenue", ""),
                    "总销量": extra.get("total_sales", ""),
                    "销量增长率": extra.get("sales_growth_rate", ""),
                    "转化率": extra.get("conversion_rate", ""),
                    "BSR排名": extra.get("bsr", ""),
                    "库存数量": extra.get("stock_quantity", ""),
                    "库存类型": extra.get("stock_type", ""),
                    "店铺类型": extra.get("store_type", ""),
                    "店铺名称": extra.get("store_name", ""),
                    "上架日期": extra.get("launch_date", ""),
                    "品牌": extra.get("brand", ""),
                    "子类目": extra.get("sub_category", ""),
                    "价格(MXN)": product.price_original or "",
                })
            if platform == "worten":
                price_usd = extra.get("price_usd")
                row.update({
                    "价格(EUR)": product.price_original or "",
                    "价格(USD)": f"${price_usd:.2f}" if price_usd else "",
                    "品牌": extra.get("brand", ""),
                    "库存状态": extra.get("stock_status", ""),
                })
            if platform == "eprice":
                price_usd = extra.get("price_usd")
                row.update({
                    "价格(EUR)": product.price_original or "",
                    "价格(USD)": f"${price_usd:.2f}" if price_usd else "",
                    "原价(EUR)": extra.get("original_price_eur", ""),
                    "折扣率": f"{extra.get('discount_pct')}%" if extra.get("discount_pct") else "",
                    "品牌": extra.get("brand", ""),
                    "卖家": extra.get("seller", ""),
                    "库存状态": extra.get("stock_status", ""),
                    "规格参数": extra.get("specs", ""),
                })
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
    "mercadolibre": MERCADOLIBRE_CSV_COLUMNS,
    "kaufland": KAUFLAND_CSV_COLUMNS,
    "worten": WORTEN_CSV_COLUMNS,
    "eprice": EPRICE_CSV_COLUMNS,
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


_MULTI_PLATFORM_PREVIEW_COLS = [
    "platform", "商品标题", "价格", "评分", "综合分析", "优点评炼", "缺点评炼", "竞品定位",
]


async def write_multi_platform_analysis_csv(
    platform_rows: list[tuple[str, list[dict]]],
    brand: str,
    count: int,
    output_dir=None,
) -> dict:
    """Merge rows from multiple platforms into a single CSV with a 'platform' column.
    Only writes columns that have at least one non-empty value across all rows.
    """
    output_dir = ARTIFACTS_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_kw = _sanitize_brand(brand)
    platforms_str = "_".join(p for p, _ in platform_rows)
    path = output_dir / f"multi_{platforms_str}_{safe_kw}_{count}_{timestamp}.csv"

    all_rows: list[dict] = []
    for platform, rows in platform_rows:
        for row in rows:
            merged = {"platform": platform}
            for col in MULTI_PLATFORM_CSV_COLUMNS[1:]:
                merged[col] = row.get(col, "")
            all_rows.append(merged)

    # Keep only columns that have at least one non-empty value.
    active_cols = ["platform"] + [
        col for col in MULTI_PLATFORM_CSV_COLUMNS[1:]
        if any(str(row.get(col, "")).strip() for row in all_rows)
    ]

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=active_cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    preview_cols = [c for c in _MULTI_PLATFORM_PREVIEW_COLS if c in active_cols]
    preview_rows = [
        [row.get(col, "") for col in preview_cols]
        for row in all_rows[:5]
    ]
    return {
        "summary": f"多平台分析完成，共 {len(all_rows)} 条（{', '.join(p for p, _ in platform_rows)}）",
        "preview_columns": preview_cols,
        "preview_rows": preview_rows,
        "filename": path.name,
        "download_url": f"/api/download/{path.name}",
        "count": len(all_rows),
        "rows": all_rows,
    }
