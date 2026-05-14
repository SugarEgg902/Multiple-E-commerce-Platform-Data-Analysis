from __future__ import annotations

"""
Amazon 抓取 + 评论总结 工具集
=================================

两个工具函数（async），可直接被任何支持 OpenAI/Anthropic 风格 function-calling
的 agent 通过 TOOLS_SCHEMA + TOOL_FUNCTIONS 调用：

1. scrape_amazon_products(keyword, max_pages, max_valid)
     关键词 -> 搜索 -> 详情 -> 返回有效产品列表

2. summarize_reviews(asin, max_reviews)
     ASIN -> 写入 asin_list.xlsx -> 轮询 all_reviews.xlsx -> 返回评论 + 本地 vLLM 中文优缺点总结

LLM 端点固定指向用户的 vLLM 服务（OpenAI 兼容）。
"""

import asyncio
import json
import os
import random
import re
import time
import zipfile
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

try:
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - exercised by environments without Playwright
    async_playwright = None

try:
    from playwright_stealth import Stealth
except ImportError:  # pragma: no cover - exercised by environments without playwright-stealth
    Stealth = None


# ============================================================================
# 配置
# ============================================================================

LLM_BASE_URL = "http://10.0.0.21:8000/v1"
LLM_MODEL = "qwen3.6-35b-a3b-fp8"
ASIN_LIST_XLSX_PATH = "/Users/wei/Desktop/商品1/asin_list.xlsx"
ALL_REVIEWS_XLSX_PATH = "/Users/wei/Desktop/商品1/result/all_reviews.xlsx"
XLSX_POLL_INTERVAL_SEC = 3.0
XLSX_POLL_TIMEOUT_SEC = 300.0

MIN_DELAY = 8.0
MAX_DELAY = 18.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
XLSX_NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
XLSX_REL_NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
REVIEW_HEADER_ALIASES = {
    "asin": {"asin"},
    "rating": {"rating", "review_rating", "star_rating", "stars"},
    "review_id": {"review_id", "id"},
    "review_header": {"review_header", "review_title", "title", "headline", "header"},
    "review_text": {"review_text", "review_body", "text", "body", "content"},
    "helpful_count": {"helpful_count", "helpful", "helpful_votes", "helpful_vote_count"},
    "author_name": {"author_name", "reviewer", "reviewer_name", "author"},
    "review_posted_date": {"review_posted_date", "posted_date", "date"},
    "review_country": {"review_country", "country"},
    "badge": {"badge"},
    "url": {"url"},
}
_EXCEL_REVIEW_LOCK = None
_EXCEL_REVIEW_LOCK_LOOP = None
_UNKNOWN_BSR_ESTIMATE = {
    "monthly_sales_range": "",
    "monthly_sales_estimate": "",
}
_BSR_RULES = {
    "Cell Phones & Accessories": [
        (500, "800-5000+", 2900),
        (5000, "200-800", 500),
        (50000, "30-200", 115),
        (300000, "5-50", 28),
        (float("inf"), "<10", 5),
    ],
    "Computers & Accessories": [
        (1000, "500-3000+", 1750),
        (10000, "100-500", 300),
        (100000, "20-150", 85),
        (float("inf"), "<20", 10),
    ],
    "Electronics": [
        (500, "1000-6000+", 3500),
        (5000, "200-1000", 600),
        (50000, "30-250", 140),
        (300000, "5-60", 33),
        (float("inf"), "<10", 5),
    ],
}
_BSR_CATEGORY_ALIASES = {
    "cell phones & accessories": "Cell Phones & Accessories",
    "computers & accessories": "Computers & Accessories",
    "electronics": "Electronics",
}


def _require_playwright_scraping() -> None:
    if async_playwright is None or Stealth is None:
        raise RuntimeError(
            "Amazon scraping requires 'playwright' and 'playwright-stealth' to be installed."
        )


# ============================================================================
# 共享辅助
# ============================================================================


def _get_excel_review_lock() -> asyncio.Lock:
    global _EXCEL_REVIEW_LOCK, _EXCEL_REVIEW_LOCK_LOOP

    loop = asyncio.get_running_loop()
    if _EXCEL_REVIEW_LOCK is None or _EXCEL_REVIEW_LOCK_LOOP is not loop:
        _EXCEL_REVIEW_LOCK = asyncio.Lock()
        _EXCEL_REVIEW_LOCK_LOOP = loop
    return _EXCEL_REVIEW_LOCK


async def _human_delay(min_sec: float | None = None, max_sec: float | None = None) -> None:
    await asyncio.sleep(random.uniform(min_sec or MIN_DELAY, max_sec or MAX_DELAY))


async def _block_resources(route) -> None:
    if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
        await route.abort()
    else:
        await route.continue_()


async def _new_page(playwright, headless: bool):
    browser = await playwright.chromium.launch(headless=headless)
    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=USER_AGENT,
        locale="en-US",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    page = await context.new_page()
    await page.route("**/*", _block_resources)
    return browser, context, page


def _parse_review_star(text: str) -> float | None:
    try:
        token = (text or "").strip().split(" ")[0]
        return float(token)
    except Exception:
        return None


def _parse_best_sellers_rank(text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return {"bsr_rank": "", "bsr_category": "", "bsr_display": ""}

    if "Best Sellers Rank" in normalized:
        normalized = normalized.split("Best Sellers Rank", 1)[1].strip()
    normalized = normalized.lstrip(": ").strip()

    matches = list(
        re.finditer(r"#\s*([\d,]+)\s+in\s+(.+?)(?=\s+\(|\s+#|$)", normalized, flags=re.IGNORECASE)
    )
    if not matches:
        return {"bsr_rank": "", "bsr_category": "", "bsr_display": ""}

    candidates: list[tuple[str, int, str]] = []
    for match in matches:
        rank_token = match.group(1)
        category = match.group(2).strip(" :-")
        try:
            rank = int(rank_token.replace(",", ""))
        except ValueError:
            continue
        candidates.append((rank_token, rank, category))

    if not candidates:
        return {"bsr_rank": "", "bsr_category": "", "bsr_display": ""}

    selected = None
    for candidate in candidates:
        if _canonical_bsr_category(candidate[2]):
            selected = candidate
            break
    if selected is None:
        selected = candidates[0]

    rank_token, rank, category = selected

    return {
        "bsr_rank": rank,
        "bsr_category": category,
        "bsr_display": f"#{rank_token} in {category}",
    }


def _canonical_bsr_category(category: str) -> str | None:
    normalized = re.sub(r"\s+", " ", str(category or "")).strip().lower()
    if not normalized:
        return None

    for alias, canonical in _BSR_CATEGORY_ALIASES.items():
        if alias in normalized:
            return canonical
    return None


def _estimate_monthly_sales(category: str, rank: int | str | None) -> dict[str, Any]:
    canonical = _canonical_bsr_category(category)
    if canonical is None:
        return dict(_UNKNOWN_BSR_ESTIMATE)

    try:
        numeric_rank = int(rank)
    except (TypeError, ValueError):
        return dict(_UNKNOWN_BSR_ESTIMATE)

    for max_rank, sales_range, estimate in _BSR_RULES[canonical]:
        if numeric_rank <= max_rank:
            return {
                "monthly_sales_range": sales_range,
                "monthly_sales_estimate": estimate,
            }

    return dict(_UNKNOWN_BSR_ESTIMATE)


def _parse_price_amount(price_text: str | None) -> float | None:
    match = re.search(r"(\d[\d,]*\.?\d*)", str(price_text or ""))
    if not match:
        return None

    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _estimate_monthly_revenue(price_text: str | None, monthly_sales_estimate: Any) -> float | str:
    try:
        sales_value = float(monthly_sales_estimate)
    except (TypeError, ValueError):
        return ""

    price_value = _parse_price_amount(price_text)
    if price_value is None:
        return ""

    return round(price_value * sales_value, 2)


async def _extract_best_sellers_rank(page) -> dict[str, Any]:
    item_selectors = [
        "#detailBullets_feature_div span.a-list-item",
        "#detailBulletsWrapper_feature_div span.a-list-item",
        "#glance_icons_div span.a-list-item",
        "#productDetails_expanderTables_depthRightSections span.a-list-item",
        "#productDetails_expanderTables_depthLeftSections span.a-list-item",
    ]
    for selector in item_selectors:
        try:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            for text in await locator.all_inner_texts():
                parsed = _parse_best_sellers_rank(text)
                if parsed["bsr_rank"]:
                    return parsed
        except Exception:
            continue

    selectors = [
        "#productDetails_detailBullets_sections1",
        "#detailBullets_feature_div",
        "#detailBulletsWrapper_feature_div",
        "#prodDetails",
    ]
    for selector in selectors:
        try:
            section = page.locator(selector).first
            if await section.count() == 0:
                continue
            text = await section.inner_text(timeout=4000)
            parsed = _parse_best_sellers_rank(text)
            if parsed["bsr_rank"]:
                return parsed
        except Exception:
            continue

    try:
        body_text = await page.locator("body").inner_text(timeout=5000)
        parsed = _parse_best_sellers_rank(body_text)
        if parsed["bsr_rank"]:
            return parsed
    except Exception:
        pass

    return {"bsr_rank": "", "bsr_category": "", "bsr_display": ""}


def _build_review_url(asin: str) -> str:
    return (
        f"https://www.amazon.com/product-reviews/{asin}/"
        "ref=cm_cr_dp_d_show_all_btm?ie=UTF8&reviewerType=all_reviews"
    )


def _xlsx_normalize_target(target: str) -> str:
    target = target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return f"xl/{target}"


def _xlsx_column_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref or "")
    if not match:
        return 0

    index = 0
    for ch in match.group(1):
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def _xlsx_cell_value(cell, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value_node = cell.find("a:v", XLSX_NS)
    inline_node = cell.find("a:is", XLSX_NS)

    if inline_node is not None:
        return "".join(t.text or "" for t in inline_node.iterfind(".//a:t", XLSX_NS))

    raw = value_node.text if value_node is not None and value_node.text is not None else ""
    if cell_type == "s" and raw:
        try:
            return shared_strings[int(raw)]
        except Exception:
            return raw
    if cell_type == "b":
        return "true" if raw == "1" else "false"
    return raw


def _read_xlsx_rows(path: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for string_item in shared_root.findall("a:si", XLSX_NS):
                shared_strings.append(
                    "".join(text.text or "" for text in string_item.iterfind(".//a:t", XLSX_NS))
                )

        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = workbook_root.find("a:sheets", XLSX_REL_NS)
        if sheets is None or len(sheets) == 0:
            return []

        first_sheet = sheets[0]
        rel_id = first_sheet.attrib.get(f"{{{XLSX_REL_NS['r']}}}id")
        if not rel_id:
            return []

        rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        sheet_path = ""
        for rel in rel_root:
            if rel.attrib.get("Id") == rel_id:
                sheet_path = _xlsx_normalize_target(rel.attrib.get("Target", ""))
                break
        if not sheet_path:
            return []

        sheet_root = ET.fromstring(archive.read(sheet_path))
        sheet_data = sheet_root.find("a:sheetData", XLSX_NS)
        if sheet_data is None:
            return []

        rows: list[list[str]] = []
        for row in sheet_data.findall("a:row", XLSX_NS):
            values_by_index: dict[int, str] = {}
            max_index = -1
            for cell in row.findall("a:c", XLSX_NS):
                col_index = _xlsx_column_index(cell.attrib.get("r", ""))
                values_by_index[col_index] = _xlsx_cell_value(cell, shared_strings)
                max_index = max(max_index, col_index)

            if max_index < 0:
                continue

            values = ["" for _ in range(max_index + 1)]
            for col_index, value in values_by_index.items():
                values[col_index] = value
            while values and values[-1] == "":
                values.pop()
            if values:
                rows.append(values)
        return rows


def _write_single_cell_xlsx(path: str, value: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)

    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Sheet1" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""
    workbook_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""
    root_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>
"""
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>
"""
    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">
      <c r="A1" t="inlineStr"><is><t>{escape(value)}</t></is></c>
    </row>
  </sheetData>
</worksheet>
"""

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", root_rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        archive.writestr("xl/styles.xml", styles_xml)


def _normalize_review_header(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


def _looks_like_header_row(row: list[str]) -> bool:
    normalized = {_normalize_review_header(cell) for cell in row if str(cell).strip()}
    if not normalized:
        return False
    known = {alias for aliases in REVIEW_HEADER_ALIASES.values() for alias in aliases}
    return bool(normalized & known)


def _rows_to_review_dicts(rows: list[list[str]]) -> list[dict]:
    if not rows:
        return []

    header_row = rows[0]
    data_rows = rows
    headers: list[str] = [f"col_{i + 1}" for i in range(len(header_row))]

    if _looks_like_header_row(header_row):
        data_rows = rows[1:]
        seen_headers: dict[str, int] = {}
        headers = []
        for idx, cell in enumerate(header_row):
            normalized = _normalize_review_header(cell) or f"col_{idx + 1}"
            canonical = normalized
            for key, aliases in REVIEW_HEADER_ALIASES.items():
                if normalized in aliases:
                    canonical = key
                    break
            count = seen_headers.get(canonical, 0)
            seen_headers[canonical] = count + 1
            headers.append(canonical if count == 0 else f"{canonical}_{count + 1}")
    elif len(header_row) >= 5:
        headers = [
            "author_name",
            "review_header",
            "review_posted_date",
            "review_text",
            "rating",
        ] + [f"col_{i + 1}" for i in range(5, len(header_row))]

    reviews: list[dict] = []
    for row in data_rows:
        if not any(str(cell).strip() for cell in row):
            continue
        review: dict[str, Any] = {}
        for idx, header in enumerate(headers):
            review[header] = row[idx] if idx < len(row) else ""
        if "helpful_count" in review:
            try:
                review["helpful_count"] = int(float(str(review["helpful_count"]).replace(",", "")))
            except Exception:
                pass
        reviews.append(review)
    return reviews


def _rows_have_data(rows: list[list[str]]) -> bool:
    return any(any(str(cell).strip() for cell in row) for row in rows)


async def _wait_for_reviews_xlsx(
    path: str,
    poll_interval_sec: float = XLSX_POLL_INTERVAL_SEC,
    timeout_sec: float = XLSX_POLL_TIMEOUT_SEC,
) -> list[dict]:
    deadline = time.monotonic() + timeout_sec
    last_error: Exception | None = None

    while True:
        try:
            if os.path.exists(path):
                rows = await asyncio.to_thread(_read_xlsx_rows, path)
                if _rows_have_data(rows):
                    return _rows_to_review_dicts(rows)
        except Exception as exc:
            last_error = exc

        if time.monotonic() >= deadline:
            if last_error is not None:
                raise TimeoutError(f"等待评论结果 Excel 超时，最后错误: {last_error}") from last_error
            raise TimeoutError(f"等待评论结果 Excel 超时: {path}")

        await asyncio.sleep(poll_interval_sec)


# ============================================================================
# Tool A: 搜索 + 详情
# ============================================================================

async def _scrape_search_page(page, keyword: str, page_num: int) -> list[dict]:
    url = f"https://www.amazon.com/s?k={keyword.replace(' ', '+')}&page={page_num}"
    print(f"[search] {keyword} page={page_num}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await _human_delay(3, 6)
        await page.evaluate("window.scrollBy(0, 1200)")
        await _human_delay(2, 4)
        await page.evaluate("window.scrollBy(0, 800)")
        await _human_delay(2, 4)

        products = await page.locator('div[data-component-type="s-search-result"]').all()
        out: list[dict] = []
        for product in products:
            try:
                asin = await product.get_attribute("data-asin")
                if not asin or len(asin) < 8:
                    continue
                out.append({
                    "asin": asin.strip(),
                    "keyword": keyword,
                    "page": page_num,
                    "timestamp": datetime.now().isoformat(),
                })
            except Exception:
                continue
        print(f"[search] got {len(out)} ASIN")
        return out
    except Exception as e:
        print(f"[search] failed: {e}")
        return []


async def _scrape_product_detail(page, asin: str, original: dict) -> dict:
    url = f"https://www.amazon.com/dp/{asin}"
    print(f"[detail] {asin}")
    data: dict[str, Any] = {
        **original,
        "url": url,
        "crawl_time": datetime.now().isoformat(),
        "title": None,
        "price": None,
        "rating": None,
        "review_count": None,
        "bsr_rank": "",
        "bsr_category": "",
        "bsr_display": "",
        "monthly_sales_range": "",
        "monthly_sales_estimate": "",
        "monthly_revenue_estimate": "",
        "bullets": [],
        "is_valid": False,
        "invalid_reason": None,
    }

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await _human_delay(4, 8)
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, 1000)")
            await _human_delay(2, 4)

        # title
        for sel in ["#productTitle", "h1#title", "h1.a-size-large",
                    "span#a-size-large", "h1[data-csa-c-type='product']", "h1"]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    text = await el.inner_text(timeout=8000)
                    if text and len(text.strip()) > 5:
                        data["title"] = text.strip()
                        break
            except Exception:
                continue
        if not data["title"]:
            try:
                data["title"] = (await page.title()).split("|")[0].strip()
            except Exception:
                pass

        # price
        for sel in [".a-price .a-offscreen", "span.a-price-whole",
                    "#priceblock_ourprice", "#priceblock_dealprice",
                    "#corePriceDisplay_desktop_feature_div .a-offscreen",
                    "span.aok-offscreen", "[data-a-size='xl'] .a-offscreen"]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    p = await el.inner_text(timeout=5000)
                    if p and any(c.isdigit() for c in p):
                        data["price"] = p.strip()
                        break
            except Exception:
                continue
        if not data["price"]:
            try:
                for p in await page.locator("span.a-price").all_inner_texts():
                    if any(c.isdigit() for c in p):
                        data["price"] = p.strip()
                        break
            except Exception:
                pass

        # rating
        for sel in ["span.a-icon-alt", "#acrPopover span.a-icon-alt", "i.a-icon-star span"]:
            try:
                r = await page.locator(sel).first.inner_text(timeout=5000)
                if r and "out of" in r.lower():
                    data["rating"] = r.strip()
                    break
            except Exception:
                continue

        # review_count
        try:
            data["review_count"] = (
                await page.locator("#acrCustomerReviewText").first.inner_text(timeout=5000)
            ).strip()
        except Exception:
            try:
                data["review_count"] = (
                    await page.locator("a[href*='customer-reviews'] span")
                    .first.inner_text(timeout=4000)
                ).strip()
            except Exception:
                pass

        # bullets
        try:
            for b in await page.locator("#feature-bullets li span.a-list-item").all():
                t = await b.inner_text(timeout=3000)
                if t.strip():
                    data["bullets"].append(t.strip())
        except Exception:
            pass

        data.update(await _extract_best_sellers_rank(page))
        data.update(_estimate_monthly_sales(data.get("bsr_category", ""), data.get("bsr_rank")))
        data["monthly_revenue_estimate"] = _estimate_monthly_revenue(
            data.get("price"),
            data.get("monthly_sales_estimate"),
        )

        # validity
        title = data.get("title") or ""
        lower = title.lower()
        if any(err in lower for err in ["page not found", "sorry, we couldn't", "not available"]):
            data["invalid_reason"] = "Page Not Found"
        elif len(title) > 15 and (len(data["bullets"]) >= 2 or data.get("price") or data.get("rating")):
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


async def scrape_amazon_products(
    keyword: str,
    max_pages: int = 2,
    max_valid: int = 5,
    headless: bool = False,
) -> list[dict]:
    """搜索关键词 -> 收集 ASIN -> 抓详情 -> 返回有效产品列表。

    Returns:
        list of dict（与原 valid_products.json 结构一致）。
    """
    _require_playwright_scraping()

    all_asins: list[dict] = []
    seen: set[str] = set()
    valid: list[dict] = []

    async with Stealth().use_async(async_playwright()) as pw:
        browser, _ctx, page = await _new_page(pw, headless)
        try:
            # 1) 搜索
            for pn in range(1, max_pages + 1):
                items = await _scrape_search_page(page, keyword, pn)
                for it in items:
                    if it["asin"] not in seen:
                        seen.add(it["asin"])
                        all_asins.append(it)
                await _human_delay()

            print(f"[search] 共 {len(all_asins)} 个唯一 ASIN, 目标有效 {max_valid}")

            # 2) 详情
            for original in all_asins:
                if len(valid) >= max_valid:
                    break
                detail = await _scrape_product_detail(page, original["asin"], original)
                if detail.get("is_valid"):
                    valid.append(detail)
                    print(f"[detail] 已拿到 {len(valid)}/{max_valid} 个有效产品")
                await _human_delay()
        finally:
            await browser.close()

    return valid


# ============================================================================
# Tool B: Bright Data 评论 API + LLM 总结
# ============================================================================

def _normalize_review_for_summary(review: dict) -> dict:
    rating = review.get("rating", "")
    if isinstance(rating, (int, float)):
        rating_text = f"{rating}/5"
    else:
        rating_text = str(rating or "").strip()

    return {
        "rating": rating_text,
        "title": (review.get("review_header") or review.get("title") or "").strip(),
        "text": (review.get("review_text") or review.get("text") or "").strip(),
        "helpful_count": review.get("helpful_count", 0) or 0,
    }


def _dedupe_reviews(reviews: list[dict]) -> list[dict]:
    uniq: list[dict] = []
    seen: set[tuple[str, str]] = set()
    review_ids: set[str] = set()

    for review in reviews:
        review_id = str(review.get("review_id") or "").strip()
        if review_id:
            if review_id in review_ids:
                continue
            review_ids.add(review_id)

        key = (
            (review.get("review_header") or review.get("title") or "")[:120],
            (review.get("review_text") or review.get("text") or "")[:240],
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append(review)
    return uniq


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
        "你将收到若干条来自 Amazon 的英文好评和差评(中评也归类到差评)，"
        "请用简体中文总结产品的优点和缺点，着重关注缺点。"
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


async def summarize_reviews(
    asin: str,
    max_reviews: int = 100,
) -> dict:
    """覆盖写入 asin_list.xlsx，轮询 all_reviews.xlsx，并用本地 LLM 提炼好评/差评。"""
    del max_reviews  # Excel 结果文件由外部流程生成，这里保留参数仅兼容旧调用。

    async with _get_excel_review_lock():
        review_url = _build_review_url(asin)
        try:
            await asyncio.to_thread(_write_single_cell_xlsx, ASIN_LIST_XLSX_PATH, review_url)
            reviews = await _wait_for_reviews_xlsx(
                ALL_REVIEWS_XLSX_PATH,
                poll_interval_sec=XLSX_POLL_INTERVAL_SEC,
                timeout_sec=XLSX_POLL_TIMEOUT_SEC,
            )
        finally:
            await asyncio.to_thread(_write_single_cell_xlsx, ASIN_LIST_XLSX_PATH, "")
            await asyncio.to_thread(_write_single_cell_xlsx, ALL_REVIEWS_XLSX_PATH, "")

    reviews = _dedupe_reviews(reviews)

    positive_reviews: list[dict] = []
    negative_reviews: list[dict] = []
    neutral_reviews: list[dict] = []

    for review in reviews:
        rating = review.get("rating")
        try:
            star = float(rating)
        except (TypeError, ValueError):
            normalized = _normalize_review_for_summary(review)
            star = _parse_review_star(normalized["rating"])

        if star is None:
            neutral_reviews.append(review)
        elif star >= 4:
            positive_reviews.append(review)
        elif star <= 3:
            negative_reviews.append(review)
        else:
            neutral_reviews.append(review)

    positive_summary_reviews = [_normalize_review_for_summary(r) for r in positive_reviews]
    negative_summary_reviews = [_normalize_review_for_summary(r) for r in negative_reviews]

    if positive_summary_reviews or negative_summary_reviews:
        print(f"[llm] summarizing {len(positive_summary_reviews)} pos / {len(negative_summary_reviews)} neg ...")
        summary = _llm_summarize(positive_summary_reviews, negative_summary_reviews)
    else:
        summary = {"pros": [], "cons": [], "overall": ""}

    return {
        "asin": asin,
        "review_count": len(reviews),
        "positive_count": len(positive_reviews),
        "negative_count": len(negative_reviews),
        "neutral_count": len(neutral_reviews),
        "pros": summary["pros"],
        "cons": summary["cons"],
        "overall": summary["overall"],
        "reviews": reviews,
        "raw_positive": positive_reviews,
        "raw_negative": negative_reviews,
        "raw_neutral": neutral_reviews,
    }


# ============================================================================
# JSON Schema 导出（OpenAI / Anthropic function-calling 通用）
# ============================================================================

TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "scrape_amazon_products",
            "description": (
                "在 Amazon.com 上搜索给定关键词，抓取商品搜索结果并访问详情页，"
                "返回若干条有效商品（含 title, price, rating, review_count, bullets, asin, "
                "Best Sellers Rank、类目、月销量估算、月销售额估算等）。"
                "评论字段不在此工具中产出。需要评论摘要请再调用 summarize_reviews。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Amazon 搜索关键词，例如 'doogee'、'rugged phone'。",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "搜索结果最多翻几页（每页 ~48 条），默认 2。",
                        "default": 2,
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "max_valid": {
                        "type": "integer",
                        "description": "拿到多少个有效商品后停止，默认 5。",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "headless": {
                        "type": "boolean",
                        "description": "是否无头模式，默认 false（可见浏览器）。",
                        "default": False,
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_reviews",
            "description": (
                "针对单个 Amazon ASIN，覆盖写入本地 asin_list.xlsx，轮询本地 all_reviews.xlsx，"
                "读取评论后再用本地 LLM 输出简体中文的优点/缺点列表和总评。"
                "返回字段包含：asin, review_count, positive_count, negative_count, neutral_count, "
                "pros, cons, overall, reviews, raw_positive, raw_negative, raw_neutral。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asin": {
                        "type": "string",
                        "description": "Amazon 商品 ASIN，例如 'B08T6HXTVQ'。",
                    },
                    "max_reviews": {
                        "type": "integer",
                        "description": "兼容保留参数；当前 Excel 流程下不使用。",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 1000,
                    },
                },
                "required": ["asin"],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "scrape_amazon_products": scrape_amazon_products,
    "summarize_reviews": summarize_reviews,
}


# ============================================================================
# CLI 自测
# ============================================================================

async def _demo():
    products = await scrape_amazon_products("doogee", max_pages=2, max_valid=5)
    print(f"\n=== 拿到 {len(products)} 个有效商品 ===")
    for p in products:
        print(f"  - {p['asin']} | {(p.get('title') or '')[:60]}")

    if not products:
        print("没有有效商品，跳过评论总结")
        return
    for p in products:
        asin = p["asin"]
        print(f"\n=== 对 {asin} 总结评论 ===")
        result = await summarize_reviews(asin, max_reviews=100)
        print(f"价钱：{p['price']}")
        print(f"标题：{p['title']}")
        print(f"平均星数：{p['rating']}")
        print(f"五点：{p['bullets']}")
        print(
            f"评论 {result['review_count']} 条 "
            f"(好评 {result['positive_count']} / 差评 {result['negative_count']} / 中评 {result['neutral_count']})"
        )
        print("\n【优点】")
        for x in result["pros"]:
            print(f"  + {x}")
        print("\n【缺点】")
        for x in result["cons"]:
            print(f"  - {x}")
        print(f"\n【总评】\n{result['overall']}")

  


if __name__ == "__main__":
    asyncio.run(_demo())
