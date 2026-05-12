import asyncio

import mp_agent.application.workflow_registry as workflow_registry
from mp_agent.application.competitor_workflows import run_amazon_competitor_analysis
from mp_agent.application.workflow_registry import build_default_registry


def _sample_product():
    return {
        "asin": "B0TEST1234",
        "url": "https://www.amazon.com/dp/B0TEST1234",
        "title": "Blackview Example",
        "price": "$199.99",
        "rating": "4.4 out of 5 stars",
        "review_count": "321",
        "bullets": ["Rugged", "Long battery"],
    }


def _sample_row():
    return {
        "品牌": "Blackview",
        "ASIN": "B0TEST1234",
        "url": "https://www.amazon.com/dp/B0TEST1234",
        "商品标题": "Blackview Example",
        "价格": "$199.99",
        "评分": "4.4 out of 5 stars",
        "评论数": "321",
        "总类目": "Cell Phones & Accessories",
        "Best Sellers Rank": "#3,214 in Cell Phones & Accessories",
        "月销量区间": "200-800",
        "月销量估算值": 500,
        "月销售额估算": 99995.0,
        "核心卖点": "三防机身",
        "优点评炼": "续航长",
        "缺点评炼": "偏厚重",
        "综合分析": "适合户外",
        "竞品定位": "中低价三防竞品",
    }


def test_default_registry_exposes_amazon_workflow_schema():
    registry = build_default_registry()

    schemas = registry.get_tool_schemas()
    names = [s["function"]["name"] for s in schemas]

    assert "run_amazon_competitor_analysis" in names


def test_default_registry_dispatches_to_amazon_workflow(monkeypatch):
    events = []

    async def fake_emit(payload):
        events.append(payload)

    async def fake_handler(*, brand, count, emit):
        await emit(
            {
                "type": "tool_status",
                "tool": "run_amazon_competitor_analysis",
                "message": "dispatch-ok",
            }
        )
        return {"brand": brand, "count": count}

    monkeypatch.setattr(workflow_registry, "run_amazon_competitor_analysis", fake_handler)
    registry = workflow_registry.build_default_registry()

    result = asyncio.run(
        registry.call_tool(
            "run_amazon_competitor_analysis",
            {"brand": "Blackview", "count": 1},
            fake_emit,
        )
    )

    assert events == [
        {
            "type": "tool_status",
            "tool": "run_amazon_competitor_analysis",
            "message": "dispatch-ok",
        }
    ]
    assert result == {"brand": "Blackview", "count": 1}


def test_run_amazon_competitor_analysis_emits_status_and_returns_artifact(tmp_path):
    events = []
    product = _sample_product()
    row = _sample_row()

    async def fake_emit(payload):
        events.append(payload)

    async def fake_scrape(_brand, max_pages=2, max_valid=5, headless=False):
        return [product]

    async def fake_reviews(_asin, max_reviews=100):
        return {
            "pros": ["续航长"],
            "cons": ["偏厚重"],
            "overall": "适合户外使用",
        }

    def fake_row_builder(**_kwargs):
        return row

    result = asyncio.run(
        run_amazon_competitor_analysis(
            brand="Blackview",
            count=1,
            emit=fake_emit,
            scrape_products=fake_scrape,
            summarize_reviews_fn=fake_reviews,
            build_row_fn=fake_row_builder,
            write_csv_fn=lambda rows, brand, count: tmp_path / "out.csv",
            download_url_builder=lambda path: f"/api/download/{path.name}",
        )
    )

    assert events[0] == {
        "type": "tool_status",
        "tool": "run_amazon_competitor_analysis",
        "message": "正在抓取 Amazon 商品...",
    }
    assert result["platform"] == "amazon"
    assert result["brand"] == "Blackview"
    assert result["count"] == 1
    assert result["preview_rows"] == [[row[column] for column in result["preview_columns"]]]
    assert result["filename"] == "out.csv"
    assert result["download_url"] == "/api/download/out.csv"


def test_run_amazon_competitor_analysis_emits_warning_when_review_summary_fails(tmp_path):
    events = []
    captured = {}
    product = _sample_product()
    row = _sample_row()

    async def fake_emit(payload):
        events.append(payload)

    async def fake_scrape(_brand, max_pages=2, max_valid=5, headless=False):
        return [product]

    async def fake_reviews(_asin, max_reviews=100):
        raise RuntimeError("review summary failed")

    def fake_row_builder(**kwargs):
        captured["review_summary"] = kwargs["review_summary"]
        return row

    result = asyncio.run(
        run_amazon_competitor_analysis(
            brand="Blackview",
            count=1,
            emit=fake_emit,
            scrape_products=fake_scrape,
            summarize_reviews_fn=fake_reviews,
            build_row_fn=fake_row_builder,
            write_csv_fn=lambda rows, brand, count: tmp_path / "out.csv",
            download_url_builder=lambda path: f"/api/download/{path.name}",
        )
    )

    assert captured["review_summary"] == {"pros": [], "cons": [], "overall": ""}
    assert events[1] == {
        "type": "tool_status",
        "tool": "run_amazon_competitor_analysis",
        "message": "正在分析 B0TEST1234 ...",
    }
    assert events[2] == {
        "type": "tool_status",
        "tool": "run_amazon_competitor_analysis",
        "level": "warning",
        "message": "B0TEST1234 的评论总结失败，已使用空摘要继续。",
    }
    assert result["filename"] == "out.csv"
