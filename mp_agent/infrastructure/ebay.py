from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime
from typing import Any

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

try:
    from playwright_stealth import Stealth
except ImportError:
    Stealth = None


LLM_BASE_URL = "http://10.0.0.21:8000/v1"
LLM_MODEL = "qwen3.6-35b-a3b-fp8"
MIN_DELAY = 6.0
MAX_DELAY = 14.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
_EBAY_HOME = "https://www.ebay.com"


def _require_playwright_scraping() -> None:
    if async_playwright is None or Stealth is None:
        raise RuntimeError(
            "eBay scraping requires 'playwright' and 'playwright-stealth' to be installed."
        )


async def _human_delay(min_sec: float | None = None, max_sec: float | None = None) -> None:
    await asyncio.sleep(random.uniform(min_sec or MIN_DELAY, max_sec or MAX_DELAY))


async def _block_resources(route) -> None:
    # Allow all requests during warm-up; only block heavy assets on detail pages
    if route.request.resource_type in ["media", "font"]:
        await route.abort()
    else:
        await route.continue_()


async def _new_page(playwright, headless: bool):
    browser = await playwright.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        viewport={"width": random.randint(1280, 1920), "height": random.randint(800, 1080)},
        user_agent=USER_AGENT,
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        },
    )
    # Remove webdriver flag
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {} };
    """)
    page = await context.new_page()
    await page.route("**/*", _block_resources)
    return browser, context, page


async def _warm_up_session(page) -> None:
    """Visit eBay homepage first to establish a real session with cookies."""
    print("[ebay] warming up session via homepage...")
    try:
        await page.goto(_EBAY_HOME, wait_until="domcontentloaded", timeout=60000)
        await _human_delay(4, 8)
        await page.evaluate("window.scrollBy(0, 400)")
        await _human_delay(2, 4)
        await page.evaluate("window.scrollBy(0, 300)")
        await _human_delay(2, 3)
    except Exception as e:
        print(f"[ebay] warm-up warning: {e}")


def _parse_price_amount(price_text: str | None) -> float | None:
    match = re.search(r"(\d[\d,]*\.?\d*)", str(price_text or ""))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _llm_summarize(positive: list[dict], negative: list[dict]) -> dict:
    from openai import OpenAI

    client = OpenAI(base_url=LLM_BASE_URL, api_key="EMPTY")

    def fmt(reviews: list[dict]) -> str:
        if not reviews:
            return "（无）"
        lines = []
        for i, r in enumerate(reviews, 1):
            lines.append(f"{i}. [{r.get('rating','')}] {r.get('title','')}\n   {r.get('text','')}")
        return "\n".join(lines)

    sys_prompt = (
        "你是一个电商商品评论分析助手。"
        "你将收到若干条来自 eBay 的英文好评和差评(中评也归类到差评)，"
        "请用简体中文总结产品的优点和缺点。"
        "输出严格的 JSON，字段为 pros(string数组)、cons(string数组)、overall(一段中文总评)。"
        "每条 pros/cons 请精炼成 10-25 字短句，去重，按重要性排序。"
        "只输出 JSON，不要任何额外说明。"
    )
    user_prompt = (
        f"【好评 {len(positive)} 条】\n{fmt(positive)}\n\n"
        f"【差评 {len(negative)} 条】\n{fmt(negative)}\n\n"
        "请输出 JSON。"
    )
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"pros": [], "cons": [], "overall": raw}
    return {
        "pros": parsed.get("pros", []) or [],
        "cons": parsed.get("cons", []) or [],
        "overall": parsed.get("overall", "") or "",
    }


async def _scrape_search_page(page, keyword: str, page_num: int) -> list[dict]:
    url = f"https://www.ebay.com/sch/i.html?_nkw={keyword.replace(' ', '+')}&_pgn={page_num}"
    print(f"[ebay search] {keyword} page={page_num}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await _human_delay(4, 8)

        # Check for access denied
        title = await page.title()
        if "access denied" in title.lower() or "denied" in title.lower():
            print("[ebay search] access denied on search page, retrying after delay...")
            await _human_delay(8, 15)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await _human_delay(4, 8)

        await page.evaluate("window.scrollBy(0, 600)")
        await _human_delay(1, 3)
        await page.evaluate("window.scrollBy(0, 600)")
        await _human_delay(2, 4)
        # eBay now uses .srp-results a[href*='/itm/'] — .s-item__link is no longer present
        links = await page.locator(".srp-results a[href*='/itm/']").all()
        out: list[dict] = []
        seen: set[str] = set()
        for link in links:
            try:
                href = await link.get_attribute("href") or ""
                m = re.search(r"/itm/(\d{10,})", href)
                if m:
                    item_id = m.group(1)
                    if item_id not in seen:
                        seen.add(item_id)
                        out.append({
                            "item_id": item_id,
                            "keyword": keyword,
                            "page": page_num,
                            "timestamp": datetime.now().isoformat(),
                        })
            except Exception:
                continue
        print(f"[ebay search] got {len(out)} item IDs")
        return out
    except Exception as e:
        print(f"[ebay search] failed: {e}")
        return []


async def _scrape_item_detail(page, item_id: str, original: dict) -> dict:
    url = f"https://www.ebay.com/itm/{item_id}"
    print(f"[ebay detail] {item_id}")
    data: dict[str, Any] = {
        **original,
        "url": url,
        "crawl_time": datetime.now().isoformat(),
        "title": None,
        "price": None,
        "rating": None,
        "review_count": None,
        "sold_count": None,
        "condition": None,
        "seller_feedback": None,
        "monthly_sales_estimate": "",
        "monthly_revenue_estimate": "",
        "monthly_sales_range": "",
        "bsr_rank": "",
        "bsr_category": "",
        "bsr_display": "",
        "bullets": [],
        "is_valid": False,
        "invalid_reason": None,
    }
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await _human_delay(4, 8)
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 1000)")
            await _human_delay(2, 4)

        for sel in [
            "h1.x-item-title__mainTitle span",
            "h1[itemprop='name']",
            ".x-item-title h1",
            "h1",
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    text = await el.inner_text(timeout=8000)
                    if text and len(text.strip()) > 5:
                        data["title"] = text.strip()
                        break
            except Exception:
                continue

        # price
        for sel in [
            ".x-price-primary span[itemprop='price']",
            ".x-bin-price .x-price-primary",
            ".x-price-primary",
            "[data-testid='x-bin-price'] span",
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    p = await el.inner_text(timeout=5000)
                    if p and any(c.isdigit() for c in p):
                        data["price"] = p.strip()
                        break
            except Exception:
                continue

        # rating — extract from page JSON (accessibilityText like "4.8 out of 5 stars")
        # eBay embeds rating in a JSON blob; DOM star elements are unreliable/duplicated
        try:
            body_html = await page.evaluate("document.body.innerHTML")
            m = re.search(r'"accessibilityText"\s*:\s*"([\d.]+\s+out\s+of\s+5[^"]*)"', body_html)
            if m:
                # Extract just the numeric value, e.g. "4.8"
                nm = re.search(r"([\d.]+)", m.group(1))
                if nm:
                    data["rating"] = nm.group(1)
        except Exception:
            pass

        # sold_count
        sold_int = None
        for sel in [".x-quantity__availability span", "[class*='sold'] span"]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    t = await el.inner_text(timeout=5000)
                    m = re.search(r"([\d,]+)\s+sold", t, re.IGNORECASE)
                    if m:
                        sold_int = int(m.group(1).replace(",", ""))
                        data["sold_count"] = t.strip()
                        break
            except Exception:
                continue
        if sold_int is None:
            try:
                body_text = await page.locator("body").inner_text(timeout=5000)
                m = re.search(r"([\d,]+)\s+sold", body_text, re.IGNORECASE)
                if m:
                    sold_int = int(m.group(1).replace(",", ""))
                    data["sold_count"] = f"{m.group(1)} sold"
            except Exception:
                pass

        # condition
        for sel in [".x-item-condition-text span", "[itemprop='itemCondition']", ".condText"]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    c = await el.inner_text(timeout=5000)
                    if c and c.strip():
                        data["condition"] = c.strip()
                        break
            except Exception:
                continue

        # seller_feedback
        for sel in [".x-sellercard-atf__data .ux-textspans--BOLD", ".seller-persona .ux-textspans--BOLD"]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    sf = await el.inner_text(timeout=5000)
                    if sf and sf.strip():
                        data["seller_feedback"] = sf.strip()
                        break
            except Exception:
                continue

        # bullets from item specifics
        try:
            rows = await page.locator(".ux-layout-section-evo__col .ux-labels-values").all()
            for row in rows[:10]:
                t = await row.inner_text(timeout=3000)
                if t.strip():
                    data["bullets"].append(t.strip())
        except Exception:
            pass

        # monthly_sales_estimate = sold_count directly
        if sold_int is not None:
            data["monthly_sales_estimate"] = sold_int
            price_float = _parse_price_amount(data.get("price"))
            if price_float is not None:
                data["monthly_revenue_estimate"] = round(price_float * sold_int, 2)

        title = data.get("title") or ""
        if len(title) > 15 and (data.get("price") or data.get("rating")):
            data["is_valid"] = True
        else:
            data["invalid_reason"] = "缺少关键信息"

        status = "✓ 有效" if data["is_valid"] else f"✗ 无效({data['invalid_reason']})"
        print(f"  {status} - {(title[:60] if title else '无标题')}")
        return data

    except Exception as e:
        data["invalid_reason"] = f"异常: {str(e)[:100]}"
        print(f"  ✗ 异常: {e}")
        return data


async def scrape_ebay_products(
    keyword: str,
    max_pages: int = 2,
    max_valid: int = 5,
    headless: bool = False,
) -> list[dict]:
    """搜索关键词 -> 收集 item ID -> 抓详情 -> 返回有效产品列表。"""
    _require_playwright_scraping()

    all_items: list[dict] = []
    seen: set[str] = set()
    valid: list[dict] = []

    async with Stealth().use_async(async_playwright()) as pw:
        browser, _ctx, page = await _new_page(pw, headless)
        try:
            await _warm_up_session(page)
            for pn in range(1, max_pages + 1):
                items = await _scrape_search_page(page, keyword, pn)
                for it in items:
                    if it["item_id"] not in seen:
                        seen.add(it["item_id"])
                        all_items.append(it)
                await _human_delay()

            print(f"[ebay] 共 {len(all_items)} 个唯一 item ID, 目标有效 {max_valid}")

            for original in all_items:
                if len(valid) >= max_valid:
                    break
                detail = await _scrape_item_detail(page, original["item_id"], original)
                if detail.get("is_valid"):
                    valid.append(detail)
                    print(f"[ebay] 已拿到 {len(valid)}/{max_valid} 个有效产品")
                await _human_delay()
        finally:
            await browser.close()

    return valid


async def scrape_ebay_reviews(item_id: str, max_reviews: int = 60) -> dict:
    """抓取 eBay 商品 buyer feedback 并用 LLM 总结优缺点。

    eBay 的 /urw/ 评论页已不可用；feedback 直接嵌在商品详情页的
    .fdbk-container 卡片中，通过 [data-test-type] 区分 positive/negative。
    """
    _require_playwright_scraping()

    reviews: list[dict] = []

    async with Stealth().use_async(async_playwright()) as pw:
        browser, _ctx, page = await _new_page(pw, headless=True)
        try:
            await _warm_up_session(page)
            url = f"https://www.ebay.com/itm/{item_id}"
            print(f"[ebay reviews] loading {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await _human_delay(4, 7)
            # Scroll down to load feedback section
            for _ in range(4):
                await page.evaluate("window.scrollBy(0, 800)")
                await _human_delay(1, 2)

            cards = await page.locator(".fdbk-container").all()
            print(f"[ebay reviews] found {len(cards)} feedback cards")

            for card in cards[:max_reviews]:
                try:
                    review: dict[str, Any] = {}

                    # Sentiment from SVG data-test-type: "positive" / "negative" / "neutral"
                    sentiment_el = card.locator("[data-test-type]").first
                    if await sentiment_el.count() > 0:
                        sentiment = (await sentiment_el.get_attribute("data-test-type") or "").lower()
                        if sentiment == "positive":
                            review["rating"] = 5.0
                        elif sentiment == "negative":
                            review["rating"] = 1.0
                        else:
                            review["rating"] = 3.0

                    # Comment text
                    comment_el = card.locator(".fdbk-container__details__comment span").first
                    if await comment_el.count() > 0:
                        review["text"] = (await comment_el.inner_text(timeout=3000)).strip()

                    if review.get("text"):
                        reviews.append(review)
                except Exception:
                    continue
        finally:
            await browser.close()

    positive = [r for r in reviews if r.get("rating", 0) >= 4]
    negative = [r for r in reviews if r.get("rating", 5) <= 2]

    print(f"[ebay reviews] {len(positive)} positive / {len(negative)} negative")

    if positive or negative:
        print(f"[ebay llm] summarizing {len(positive)} pos / {len(negative)} neg ...")
        summary = _llm_summarize(positive, negative)
    else:
        summary = {"pros": [], "cons": [], "overall": ""}

    return {
        "item_id": item_id,
        "review_count": len(reviews),
        "positive_count": len(positive),
        "negative_count": len(negative),
        "pros": summary["pros"],
        "cons": summary["cons"],
        "overall": summary["overall"],
    }

