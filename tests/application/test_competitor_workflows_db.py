import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path


@pytest.mark.asyncio
async def test_amazon_workflow_calls_upsert_product():
    from mp_agent.application.competitor_workflows import run_amazon_competitor_analysis

    mock_product = {
        "asin": "B001TEST", "title": "Test Product", "price": "$29.99",
        "rating": 4.5, "review_count": 100, "url": "https://amazon.com/dp/B001TEST",
        "bsr_display": "", "monthly_sales_range": "", "monthly_sales_estimate": 0,
        "monthly_revenue_estimate": 0, "bullets": [],
    }
    mock_review = {"pros": [], "cons": [], "overall": "good"}
    mock_row = {
        "ASIN": "B001TEST", "核心卖点": "test", "优点评炼": [], "缺点评炼": [],
        "综合分析": "", "竞品定位": "", "价格": "$29.99", "评分": 4.5,
        "月销量估算值": 0, "月销售额估算": 0,
    }

    mock_scrape = AsyncMock(return_value=[mock_product])
    mock_summarize = AsyncMock(return_value=mock_review)

    with patch("mp_agent.application.competitor_workflows.product_exists", new_callable=AsyncMock, return_value=False), \
         patch("mp_agent.application.competitor_workflows.upsert_product", new_callable=AsyncMock, return_value=1) as mock_upsert, \
         patch("mp_agent.application.competitor_workflows.save_detail", new_callable=AsyncMock), \
         patch("mp_agent.application.competitor_workflows.save_snapshot", new_callable=AsyncMock), \
         patch("mp_agent.application.competitor_workflows.save_analysis_result", new_callable=AsyncMock), \
         patch("mp_agent.application.competitor_workflows.schedule_matching"):
        emit = AsyncMock()
        result = await run_amazon_competitor_analysis(
            "doogee", 1, emit,
            scrape_products=mock_scrape,
            summarize_reviews_fn=mock_summarize,
            build_row_fn=lambda **_: mock_row,
            write_csv_fn=lambda rows, brand, count: Path("/tmp/test.csv"),
        )
        mock_upsert.assert_called_once()
        assert result["platform"] == "amazon"


@pytest.mark.asyncio
async def test_amazon_workflow_db_failure_still_returns_rows():
    """When DB calls raise, the workflow should degrade gracefully and still return rows."""
    from mp_agent.application.competitor_workflows import run_amazon_competitor_analysis

    mock_product = {
        "asin": "B002TEST", "title": "Test Product 2", "price": "$19.99",
        "rating": 4.0, "review_count": 50, "url": "https://amazon.com/dp/B002TEST",
        "bsr_display": "", "monthly_sales_range": "", "monthly_sales_estimate": 0,
        "monthly_revenue_estimate": 0, "bullets": [],
    }
    mock_review = {"pros": [], "cons": [], "overall": ""}
    mock_row = {
        "ASIN": "B002TEST", "核心卖点": "test", "优点评炼": [], "缺点评炼": [],
        "综合分析": "", "竞品定位": "", "价格": "$19.99", "评分": 4.0,
        "月销量估算值": 0, "月销售额估算": 0,
    }

    mock_scrape = AsyncMock(return_value=[mock_product])
    mock_summarize = AsyncMock(return_value=mock_review)

    with patch("mp_agent.application.competitor_workflows.product_exists", new_callable=AsyncMock, return_value=False), \
         patch("mp_agent.application.competitor_workflows.upsert_product", new_callable=AsyncMock, side_effect=Exception("DB unavailable")), \
         patch("mp_agent.application.competitor_workflows.save_detail", new_callable=AsyncMock), \
         patch("mp_agent.application.competitor_workflows.save_snapshot", new_callable=AsyncMock), \
         patch("mp_agent.application.competitor_workflows.save_analysis_result", new_callable=AsyncMock), \
         patch("mp_agent.application.competitor_workflows.schedule_matching"):
        emit = AsyncMock()
        result = await run_amazon_competitor_analysis(
            "doogee", 1, emit,
            scrape_products=mock_scrape,
            summarize_reviews_fn=mock_summarize,
            build_row_fn=lambda **_: mock_row,
            write_csv_fn=lambda rows, brand, count: Path("/tmp/test.csv"),
        )
        # Despite DB failure, rows should still be collected and returned
        assert result["platform"] == "amazon"
        assert len(result["rows"]) == 1
