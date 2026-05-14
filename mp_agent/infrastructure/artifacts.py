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
