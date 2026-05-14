from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime

import httpx

EUR_TO_USD = 1.08

LLM_BASE_URL = "http://10.0.0.21:8000/v1"
LLM_MODEL = "qwen3.6-35b-a3b-fp8"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

_product_cache: dict[str, dict] = {}


def _estimate_total_sales(review_count: int, price_usd: float | None, title: str) -> tuple[str, str]:
    if not review_count:
        return "", ""
    t = title.lower()
    # Tablet: fixed multiplier
    if any(k in t for k in ["tablet", "ipad"]):
        total_sales = review_count * 40
    # Keyboard / mouse
    elif any(k in t for k in ["tastatur", "maus", "keyboard", "mouse", "mäuse", "trackpad"]):
        total_sales = int(review_count / 0.055)   # midpoint 3%~8%
    # Headphones / earphones
    elif any(k in t for k in ["kopfhörer", "headphone", "earphone", "ohrhörer", "headset", "earbuds", "in-ear"]):
        total_sales = int(review_count / 0.035)   # midpoint 2%~5%
    # Laptop / notebook
    elif any(k in t for k in ["laptop", "notebook", "chromebook"]):
        total_sales = int(review_count / 0.0125)  # midpoint 0.5%~2%
    # Smartphone / phone
    elif any(k in t for k in ["smartphone", "handy", "mobiltelefon", "phone"]):
        total_sales = int(review_count / 0.02)    # midpoint 1%~3%
    else:
        total_sales = int(review_count / 0.02)    # default: phone rate

    total_revenue = round(total_sales * price_usd, 2) if price_usd else ""
    return str(total_sales), str(total_revenue) if total_revenue != "" else ""


def _extract_variation_id(url_path: str) -> str:
    m = re.search(r"variationId=([A-Za-z0-9]+)", url_path)
    if m:
        return m.group(1)
    m = re.search(r"-(\d{7,})/?(?:\?|$)", url_path)
    if m:
        return m.group(1)
    return ""


def _extract_review_id(product_url: str) -> str:
    """Extract OTTO variation/review ID from product URL (e.g. S0EER0F2)."""
    path = product_url.split("?")[0].rstrip("/")
    segments = [s for s in path.split("/") if s]
    if not segments:
        return ""
    last = segments[-1]
    # Case 1: last segment is purely alphanumeric — /S0EER0F2/
    if re.match(r"^[A-Za-z0-9]+$", last) and len(last) >= 4:
        return last
    # Case 2: ID embedded at end — /product-name-S0EER0F2/
    m = re.search(r"-([A-Z0-9]{6,})$", last)
    if m:
        return m.group(1)
    return ""


def _parse_search_page(html: str, keyword: str) -> list[dict]:
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    products: list[dict] = []
    for block in blocks:
        try:
            d = json.loads(block)
        except Exception:
            continue
        if d.get("@type") != "Product":
            continue

        url_path = d.get("url", "")
        variation_id = _extract_variation_id(url_path)
        if not variation_id:
            continue

        offers = d.get("offers", [])
        if isinstance(offers, dict):
            offers = [offers]
        price_eur: float | None = None
        for offer in offers:
            try:
                price_eur = float(offer.get("price", 0) or 0)
                if price_eur > 0:
                    break
            except (ValueError, TypeError):
                pass

        price_usd = round(price_eur * EUR_TO_USD, 2) if price_eur else None

        rating_data = d.get("aggregateRating") or {}
        try:
            rating = float(str(rating_data.get("ratingValue") or ""))
        except (ValueError, TypeError):
            rating = None
        try:
            review_count = int(str(rating_data.get("reviewCount") or 0).replace(",", ""))
        except (ValueError, TypeError):
            review_count = 0

        full_url = "https://www.otto.de" + url_path.split("?")[0]

        title = (d.get("name") or "").strip()
        total_sales, total_revenue = _estimate_total_sales(review_count, price_usd, title)

        products.append({
            "variation_id": variation_id,
            "asin": variation_id,
            "keyword": keyword,
            "url": full_url,
            "title": title,
            "price_eur": price_eur,
            "price": f"${price_usd:.2f}" if price_usd else "",
            "rating": str(rating) if rating is not None else "",
            "review_count": review_count,
            "brand": ((d.get("brand") or {}).get("name") or ""),
            "description": "",
            "bullets": [],
            "总销量估算": total_sales,
            "总销售额估算": total_revenue,
            "monthly_sales_estimate": "",
            "monthly_revenue_estimate": "",
            "monthly_sales_range": "",
            "bsr_rank": "",
            "bsr_category": "",
            "bsr_display": "",
            "is_valid": bool(title) and price_usd is not None,
            "crawl_time": datetime.now().isoformat(),
        })
    return products


def _parse_reviews_html(html: str) -> list[dict]:
    """Extract review rating + text from OTTO Kundenbewertungen page."""
    reviews: list[dict] = []
    # Split on each review block boundary
    blocks = re.split(r'(?=data-review-id=")', html)
    for block in blocks[1:]:
        rating_m = re.search(r'data-rating="(\d)"', block)
        if not rating_m:
            continue
        rating = int(rating_m.group(1))
        # Strip tags and collapse whitespace to get plain text
        text = re.sub(r"<[^>]+>", " ", block)
        text = re.sub(r"\s+", " ", text).strip()[:600]
        if len(text) > 20:
            reviews.append({"rating": rating, "text": text})
    return reviews


async def _fetch_detail(url: str) -> dict:
    try:
        async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=20) as client:
            r = await client.get(url)
        html = r.text
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
        for s in scripts:
            if '"@type": "Product"' in s or '"@type":"Product"' in s:
                try:
                    d = json.loads(s)
                    if d.get("@type") == "Product":
                        desc = d.get("description") or ""
                        bullets = [
                            sent.strip()
                            for sent in re.split(r"[.!?]\s+", desc)
                            if len(sent.strip()) > 20
                        ][:5]
                        return {"description": desc, "bullets": bullets}
                except Exception:
                    pass
    except Exception as e:
        print(f"[otto detail] error: {e}")
    return {"description": "", "bullets": []}


async def scrape_otto_products(
    keyword: str,
    max_pages: int = 2,
    max_valid: int = 5,
    headless: bool = False,
) -> list[dict]:
    valid: list[dict] = []
    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=20) as client:
        for page in range(1, max_pages + 1):
            if len(valid) >= max_valid:
                break
            url = f"https://www.otto.de/suche/{keyword}/"
            if page > 1:
                url += f"?page={page}"
            print(f"[otto search] page {page}: {url}")
            try:
                r = await client.get(url)
                products = _parse_search_page(r.text, keyword)
                print(f"[otto search] page {page}: {len(products)} products")
            except Exception as e:
                print(f"[otto search] page {page} error: {e}")
                continue

            for p in products:
                if len(valid) >= max_valid:
                    break
                if not p["is_valid"]:
                    continue
                detail = await _fetch_detail(p["url"])
                p.update(detail)
                valid.append(p)
                _product_cache[p["variation_id"]] = p
                print(f"[otto] {p['variation_id']} ✓ {p['title'][:50]}")

    print(f"[otto] 共 {len(valid)} 个有效产品")
    return valid


def _llm_analyze_product(product: dict) -> dict:
    from openai import OpenAI

    client = OpenAI(base_url=LLM_BASE_URL, api_key="EMPTY")
    sys_prompt = (
        "你是一个电商竞品分析助手。"
        "根据商品的标题、价格、评分、评论数和描述，用简体中文生成竞品定位分析。"
        "输出严格的 JSON，字段为 pros(string数组)、cons(string数组)、overall(一段中文分析，100-150字)。"
        "只输出 JSON，不要任何额外说明。"
    )
    user_prompt = (
        f"商品标题：{product.get('title', '')}\n"
        f"价格：{product.get('price', '')}\n"
        f"评分：{product.get('rating', '')}\n"
        f"评论数：{product.get('review_count', '')}\n"
        f"描述：{(product.get('description') or '')[:500]}\n"
        "请输出 JSON。"
    )
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"overall": raw}
    return {
        "pros": parsed.get("pros", []) or [],
        "cons": parsed.get("cons", []) or [],
        "overall": parsed.get("overall", "") or "",
    }


def _llm_summarize(positive: list[str], negative: list[str], product: dict) -> dict:
    from openai import OpenAI

    client = OpenAI(base_url=LLM_BASE_URL, api_key="EMPTY")
    pos_text = "\n".join(f"- {t}" for t in positive[:20])
    neg_text = "\n".join(f"- {t}" for t in negative[:10])
    sys_prompt = (
        "你是一个电商竞品分析助手。"
        "根据德语买家评论（正面和负面），用简体中文总结优缺点和综合分析。"
        "输出严格的 JSON，字段为 pros(string数组，每条15-30字)、cons(string数组，每条15-30字)、"
        "overall(一段中文分析，100-150字)。只输出 JSON，不要任何额外说明。"
    )
    user_prompt = (
        f"商品：{product.get('title', '')}\n"
        f"正面评论（{len(positive)}条）：\n{pos_text or '无'}\n\n"
        f"负面评论（{len(negative)}条）：\n{neg_text or '无'}\n\n"
        "请输出 JSON。"
    )
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"overall": raw}
    return {
        "pros": parsed.get("pros", []) or [],
        "cons": parsed.get("cons", []) or [],
        "overall": parsed.get("overall", "") or "",
    }


async def scrape_otto_reviews(variation_id: str, product_url: str, max_reviews: int = 60) -> dict:
    product = _product_cache.get(variation_id, {})

    review_id = _extract_review_id(product_url)
    if not review_id:
        print(f"[otto reviews] no review_id from {product_url}, falling back to LLM")
        if not product:
            return {"pros": [], "cons": [], "overall": ""}
        return await asyncio.to_thread(_llm_analyze_product, product)

    all_reviews: list[dict] = []
    pages_needed = max(1, (max_reviews + 29) // 30)

    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=20) as client:
        for page in range(1, pages_needed + 1):
            if len(all_reviews) >= max_reviews:
                break
            url = f"https://www.otto.de/kundenbewertungen/{review_id}/"
            if page > 1:
                url += f"?page={page}"
            print(f"[otto reviews] {review_id} page {page}: {url}")
            try:
                r = await client.get(url)
                page_reviews = _parse_reviews_html(r.text)
                print(f"[otto reviews] page {page}: {len(page_reviews)} reviews")
                if not page_reviews:
                    break
                all_reviews.extend(page_reviews)
            except Exception as e:
                print(f"[otto reviews] page {page} error: {e}")
                break

    if not all_reviews:
        print(f"[otto reviews] no reviews scraped, falling back to LLM analysis")
        if not product:
            return {"pros": [], "cons": [], "overall": ""}
        return await asyncio.to_thread(_llm_analyze_product, product)

    positive = [r["text"] for r in all_reviews if r["rating"] >= 4]
    negative = [r["text"] for r in all_reviews if r["rating"] <= 3]
    print(f"[otto reviews] {len(positive)} positive, {len(negative)} negative")
    return await asyncio.to_thread(_llm_summarize, positive, negative, product or {})

