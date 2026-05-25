from __future__ import annotations

import json
import re
import html as _html
from datetime import datetime

from config.config import EUR_TO_USD, LLM_BASE_URL, LLM_MODEL
from mp_agent.infrastructure._flaresolverr import flare_fetch

_EPRICE_HOME = "https://www.eprice.it"
_SESSION = "eprice_session"
_TIMEOUT = 90  # httpx timeout; FlareSolverr maxTimeout is 60000 ms
_MAX_AUTO_PAGES = 6


# ── FlareSolverr fetch ────────────────────────────────────────────────────────

async def _fetch(url: str, retries: int = 2) -> str:
    return await flare_fetch(
        url,
        session=_SESSION,
        max_timeout=60_000,
        http_timeout=_TIMEOUT,
        min_response_bytes=50_000,
        retries=retries,
        platform="eprice",
    )


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


def _extract_bullets(description: str) -> list[str]:
    """Extract bullet points from a plain-text description."""
    bullets: list[str] = []
    for line in description.split("."):
        line = line.strip()
        if 15 < len(line) < 250:
            bullets.append(line)
        if len(bullets) >= 8:
            break
    return bullets


# ── Sales estimation ──────────────────────────────────────────────────────────

def _estimate_total_sales(
    review_count: int, price_usd: float | None, title: str
) -> tuple[str, str]:
    if not review_count:
        return "", ""
    t = title.lower()
    if any(k in t for k in ["tablet", "ipad"]):
        total = review_count * 40
    elif any(k in t for k in ["headphone", "earphone", "earbuds", "cuffie", "auricolari"]):
        total = int(review_count / 0.035)
    elif any(k in t for k in ["laptop", "notebook"]):
        total = int(review_count / 0.0125)
    else:
        total = int(review_count / 0.02)
    revenue = round(total * price_usd, 2) if price_usd else ""
    return str(total), str(revenue) if revenue != "" else ""


# ── Search page parser ────────────────────────────────────────────────────────

def _parse_search_page(html: str, keyword: str) -> list[dict]:
    """
    ePrice embeds full product JSON in data-product="..." attributes on each
    product card. Each object contains: sku, name, brand, priceInfo,
    availability, rating, shop, attributes.features.
    """
    raw_blocks = re.findall(r'data-product="([^"]+)"', html)
    products: list[dict] = []
    seen: set[str] = set()

    for raw in raw_blocks:
        try:
            p = json.loads(_html.unescape(raw))
        except Exception:
            continue

        sku = str(p.get("sku", "")).strip()
        if not sku or sku in seen:
            continue
        seen.add(sku)

        name = (p.get("name") or "").strip()
        brand_raw = p.get("brand") or {}
        brand = brand_raw.get("label") or brand_raw.get("name") or "" if isinstance(brand_raw, dict) else str(brand_raw)

        price_info = p.get("priceInfo") or {}
        selling = price_info.get("sellingPrice") or {}
        try:
            price_eur = float(selling.get("value") or 0) or None
        except (ValueError, TypeError):
            price_eur = None

        original = price_info.get("originalPrice") or {}
        try:
            original_price_eur = float(original.get("value") or 0) or None
        except (ValueError, TypeError):
            original_price_eur = None

        discount_pct = price_info.get("discountPercent") or 0

        price_usd = round(price_eur * EUR_TO_USD, 2) if price_eur else None

        # availability: numeric stock quantity; 0 = out of stock
        avail = p.get("availability", 0)
        in_stock = int(avail) > 0 if avail is not None else True

        rating_data = p.get("rating") or {}
        has_rating = rating_data.get("hasRating", False)
        try:
            rating = float(rating_data.get("averageRating") or 0) if has_rating else None
        except (ValueError, TypeError):
            rating = None
        try:
            review_count = int(rating_data.get("numberOfReviews") or 0)
        except (ValueError, TypeError):
            review_count = 0

        shop = p.get("shop") or {}
        seller = shop.get("name", "")

        # Specs from attributes.features
        features = (p.get("attributes") or {}).get("features") or {}
        specs = "; ".join(f"{k}: {v}" for k, v in features.items()) if features else ""

        permalink = (p.get("permalink") or "").strip()
        if permalink and not permalink.startswith("http"):
            permalink = _EPRICE_HOME + permalink

        products.append({
            "sku": sku,
            "keyword": keyword,
            "url": permalink,
            "title": name,
            "brand": brand,
            "price_eur": price_eur,
            "original_price_eur": original_price_eur,
            "discount_pct": discount_pct,
            "price_usd": price_usd,
            "price": f"${price_usd:.2f}" if price_usd else "",
            "rating": str(rating) if rating is not None else "",
            "review_count": review_count,
            "seller": seller,
            "specs": specs,
            "in_stock": in_stock,
            "description": "",
            "bullets": [],
            "is_valid": bool(name) and price_eur is not None and in_stock,
        })

    print(f"[eprice search] found {len(products)} products ({sum(1 for p in products if p['is_valid'])} valid)")
    return products


# ── Detail page parser ────────────────────────────────────────────────────────

def _parse_detail_page(html: str) -> dict:
    """
    Extract description and full specs from the detail page.
    JSON-LD BuyAction contains: sku, name, description, price, availability.
    HTML specs table: <li><span>key</span><strong>val</strong></li>
    """
    ld_blocks = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )

    name = sku = description = ""
    price_eur: float | None = None
    in_stock = True

    for block in ld_blocks:
        try:
            obj = json.loads(block.strip())
        except Exception:
            continue
        if obj.get("@type") != "BuyAction":
            continue
        prod = obj.get("object") or {}
        name = (prod.get("name") or "").strip()
        sku = str(prod.get("sku") or "").strip()
        description = (prod.get("description") or "").strip()

        offers = prod.get("offers") or {}
        price_spec = offers.get("priceSpecification") or []
        if price_spec:
            try:
                price_eur = float(price_spec[0].get("price") or 0) or None
            except (ValueError, TypeError):
                price_eur = None
        avail = offers.get("availability", "")
        in_stock = "InStock" in avail or not avail
        break

    # Full specs table from HTML
    specs_items = re.findall(
        r'<li><span>(.*?)</span><strong>(.*?)</strong></li>', html
    )
    specs = "; ".join(f"{_strip_tags(k)}: {_strip_tags(v)}" for k, v in specs_items) if specs_items else ""

    bullets = _extract_bullets(description) if description else []

    return {
        "sku": sku,
        "name": name,
        "description": description[:800],
        "bullets": bullets,
        "specs": specs,
        "price_eur": price_eur,
        "in_stock": in_stock,
    }


# ── LLM helpers ───────────────────────────────────────────────────────────────

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


# ── Public API ────────────────────────────────────────────────────────────────

async def scrape_eprice_products(
    keyword: str,
    max_pages: int = 2,
    max_valid: int = 5,
) -> list[dict]:
    valid: list[dict] = []
    effective_max_pages = max_pages

    page = 1
    while page <= effective_max_pages:
        if len(valid) >= max_valid:
            break

        if page == 1:
            url = f"{_EPRICE_HOME}/sa/?qs={keyword}&c_fr=H&c_t=t"
        else:
            url = f"{_EPRICE_HOME}/sa/?qs={keyword}&page={page}"

        print(f"[eprice search] page {page}: {url}")
        try:
            html = await _fetch(url)
            stubs = _parse_search_page(html, keyword)
        except Exception as e:
            print(f"[eprice search] page {page} error: {e}")
            page += 1
            continue

        for stub in stubs:
            if len(valid) >= max_valid:
                break
            if not stub.get("is_valid"):
                continue

            detail_url = stub["url"]
            if not detail_url:
                continue

            print(f"[eprice detail] {stub['sku']}: {detail_url}")
            try:
                detail_html = await _fetch(detail_url)
                detail = _parse_detail_page(detail_html)
            except Exception as e:
                print(f"[eprice detail] {stub['sku']} error: {e}")
                detail = {}

            # Guard against SKU mismatch on detail page
            detail_sku = detail.get("sku", "")
            if detail_sku and detail_sku != stub["sku"]:
                print(f"[eprice detail] SKU mismatch: stub={stub['sku']} detail={detail_sku}, skipping")
                continue

            title = detail.get("name") or stub["title"]
            price_eur = detail.get("price_eur") or stub.get("price_eur")
            price_usd = round(price_eur * EUR_TO_USD, 2) if price_eur else None
            in_stock = detail.get("in_stock", stub.get("in_stock", True))
            description = detail.get("description") or ""
            bullets = detail.get("bullets") or []
            specs = detail.get("specs") or stub.get("specs", "")
            review_count = stub.get("review_count", 0)
            rating = stub.get("rating", "")

            total_sales, total_revenue = _estimate_total_sales(review_count, price_usd, title)

            product = {
                "sku": stub["sku"],
                "product_id": stub["sku"],
                "asin": stub["sku"],
                "keyword": keyword,
                "url": detail_url,
                "title": title,
                "brand": stub.get("brand", ""),
                "price_eur": price_eur,
                "price_usd": price_usd,
                "price": f"${price_usd:.2f}" if price_usd else "",
                "original_price_eur": stub.get("original_price_eur"),
                "discount_pct": stub.get("discount_pct", 0),
                "rating": rating,
                "review_count": review_count,
                "seller": stub.get("seller", ""),
                "description": description,
                "bullets": bullets,
                "specs": specs,
                "in_stock": in_stock,
                "stock_status": "有货" if in_stock else "缺货",
                "总销量估算": total_sales,
                "总销售额估算": total_revenue,
                "monthly_sales_estimate": "",
                "monthly_revenue_estimate": "",
                "monthly_sales_range": "",
                "bsr_rank": "",
                "bsr_category": "",
                "bsr_display": "",
                "crawl_time": datetime.now().isoformat(),
            }
            valid.append(product)
            print(f"[eprice] {stub['sku']} ✓ {title[:50]}")

        if (
            page == effective_max_pages
            and len(valid) < max_valid
            and effective_max_pages < _MAX_AUTO_PAGES
        ):
            effective_max_pages += 1
            print(f"[eprice] 有效产品不足 {max_valid}，继续抓第 {effective_max_pages} 页...")
        page += 1

    print(f"[eprice] 共 {len(valid)} 个有效产品")
    return valid


async def scrape_eprice_reviews(
    sku: str,
    product_url: str,
    max_reviews: int = 60,
) -> dict:
    # ePrice does not expose a public review API.
    print(f"[eprice reviews] {sku}: no public review API available")
    return {"pros": [], "cons": [], "overall": ""}
