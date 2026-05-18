# tests/dao/test_integration_smoke.py
"""
Live DB integration smoke tests.
Requires: MP_AGENT_DB_URL env var pointing to a live MySQL instance.
Auto-skipped when MP_AGENT_DB_URL is not set.
"""
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("MP_AGENT_DB_URL"),
    reason="MP_AGENT_DB_URL not set — skipping live DB tests",
)


@pytest.mark.asyncio
async def test_upsert_and_query_product():
    from datetime import datetime
    from mp_agent.dao.repository import upsert_product, product_exists

    pid = await upsert_product({
        "platform": "amazon",
        "platform_product_id": "SMOKE_TEST_001",
        "keyword": "smoke_test",
        "title": "Smoke Test Product",
        "price_usd": 9.99,
        "crawl_time": datetime.utcnow(),
    })
    assert isinstance(pid, int)
    assert await product_exists("amazon", "SMOKE_TEST_001")


@pytest.mark.asyncio
async def test_create_and_update_crawl_task():
    from mp_agent.dao.repository import create_crawl_task, update_crawl_task

    task_id = await create_crawl_task("amazon", "smoke_test", 5)
    assert isinstance(task_id, int)
    await update_crawl_task(task_id, "done", products_found=1)


@pytest.mark.asyncio
async def test_save_detail_and_snapshot():
    from datetime import datetime
    from mp_agent.dao.repository import upsert_product, save_detail, save_snapshot

    pid = await upsert_product({
        "platform": "ebay",
        "platform_product_id": "SMOKE_TEST_002",
        "keyword": "smoke_test",
        "title": "Smoke Test eBay Product",
        "price_usd": 19.99,
        "crawl_time": datetime.utcnow(),
    })
    await save_detail(pid, {"sold_count": 100, "condition": "New"})
    await save_snapshot(pid, "ebay", "SMOKE_TEST_002", {
        "title": "Smoke Test eBay Product",
        "price_usd": 19.99,
        "rating": 4.5,
    })
    assert True  # no exception = success
