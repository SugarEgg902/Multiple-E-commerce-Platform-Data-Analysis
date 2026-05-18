# mp_agent/dao/repository.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from mp_agent.dao.db import get_async_session
from mp_agent.dao.models import (
    AnalysisResult, CrawlTask, PlatformProduct,
    PlatformProductDetail, PlatformProductSnapshot,
)


async def product_exists(platform: str, platform_product_id: str) -> bool:
    """Return True if this platform+id combo is already in the DB."""
    async with get_async_session() as session:
        result = await session.execute(
            select(PlatformProduct.id).where(
                PlatformProduct.platform == platform,
                PlatformProduct.platform_product_id == platform_product_id,
            )
        )
        return result.scalar_one_or_none() is not None


async def upsert_product(data: dict) -> int:
    """Insert or update platform_product. Returns the row id."""
    async with get_async_session() as session:
        stmt = (
            mysql_insert(PlatformProduct)
            .values(**data)
            .on_duplicate_key_update(
                title=data.get("title"),
                price_usd=data.get("price_usd"),
                price_original=data.get("price_original"),
                rating=data.get("rating"),
                review_count=data.get("review_count"),
                url=data.get("url"),
                crawl_time=data.get("crawl_time"),
                updated_at=datetime.utcnow(),
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        if result.lastrowid:
            return result.lastrowid
        row = await session.execute(
            select(PlatformProduct.id).where(
                PlatformProduct.platform == data["platform"],
                PlatformProduct.platform_product_id == data["platform_product_id"],
            )
        )
        return row.scalar_one()


async def save_detail(product_id: int, extra: dict) -> None:
    """Upsert platform-specific fields into platform_product_detail."""
    async with get_async_session() as session:
        stmt = (
            mysql_insert(PlatformProductDetail)
            .values(product_id=product_id, extra=extra)
            .on_duplicate_key_update(extra=extra)
        )
        await session.execute(stmt)


async def save_snapshot(
    product_id: int,
    platform: str,
    platform_product_id: str,
    snapshot_data: dict,
    crawl_task_id: int | None = None,
) -> None:
    """Write a point-in-time snapshot for a product listing."""
    async with get_async_session() as session:
        snap = PlatformProductSnapshot(
            product_id=product_id,
            platform=platform,
            platform_product_id=platform_product_id,
            snapshotted_at=datetime.utcnow(),
            crawl_task_id=crawl_task_id,
            **snapshot_data,
        )
        session.add(snap)


async def create_crawl_task(platform: str, keyword: str, target_count: int) -> int:
    """Create a new crawl_task record and return its id."""
    async with get_async_session() as session:
        task = CrawlTask(
            platform=platform,
            keyword=keyword,
            target_count=target_count,
            status="running",
            started_at=datetime.utcnow(),
        )
        session.add(task)
        await session.flush()
        return task.id


async def update_crawl_task(
    task_id: int,
    status: str,
    products_found: int = 0,
    error_message: str | None = None,
) -> None:
    """Update status and completion fields on a crawl_task."""
    async with get_async_session() as session:
        result = await session.execute(
            select(CrawlTask).where(CrawlTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            return
        task.status = status
        task.products_found = products_found
        task.finished_at = datetime.utcnow()
        if error_message:
            task.error_message = error_message


async def save_analysis_result(product_id: int, crawl_task_id: int | None, row: dict) -> None:
    """Persist an LLM analysis result for a product."""
    async with get_async_session() as session:
        pros_val = row.get("优点评炼", "")
        cons_val = row.get("缺点评炼", "")
        ar = AnalysisResult(
            product_id=product_id,
            crawl_task_id=crawl_task_id,
            core_selling_points=row.get("核心卖点"),
            pros=pros_val if isinstance(pros_val, list) else [pros_val] if pros_val else [],
            cons=cons_val if isinstance(cons_val, list) else [cons_val] if cons_val else [],
            overall=row.get("综合分析"),
            positioning=row.get("竞品定位"),
            category=row.get("总类目"),
        )
        session.add(ar)


async def get_latest_crawl_time(platform: str, keyword: str) -> datetime | None:
    """Return the most recent crawl_time for any product with this platform+keyword."""
    async with get_async_session() as session:
        result = await session.execute(
            select(func.max(PlatformProduct.crawl_time)).where(
                PlatformProduct.platform == platform,
                PlatformProduct.keyword == keyword,
            )
        )
        return result.scalar_one_or_none()


async def has_running_crawl_task(platform: str, keyword: str) -> bool:
    """Return True if there is already a running crawl_task for this platform+keyword."""
    async with get_async_session() as session:
        result = await session.execute(
            select(CrawlTask.id).where(
                CrawlTask.platform == platform,
                CrawlTask.keyword == keyword,
                CrawlTask.status == "running",
            )
        )
        return result.scalar_one_or_none() is not None
