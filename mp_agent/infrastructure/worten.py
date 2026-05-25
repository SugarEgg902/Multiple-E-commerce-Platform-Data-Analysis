from __future__ import annotations

import json
import re
from datetime import datetime
from html.parser import HTMLParser

from config.config import EUR_TO_USD, LLM_BASE_URL, LLM_MODEL
from mp_agent.infrastructure._flaresolverr import flare_fetch

_WORTEN_HOME = "https://www.worten.pt"
_SESSION = "worten_session"
_TIMEOUT = 210  # httpx timeout; FlareSolverr maxTimeout is 180000 ms

_WORTEN_MAX_AUTO_PAGES = 6


# ── FlareSolverr fetch ────────────────────────────────────────────────────────

async def _fetch(url: str, retries: int = 2) -> str:
    return await flare_fetch(
        url,
        session=_SESSION,
        max_timeout=180_000,
        http_timeout=_TIMEOUT,
        min_response_bytes=500_000,
        retries=retries,
        platform="worten",
    )


# ── HTML helpers ──────────────────────────────────────────────────────────────

class _StripHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(p.strip() for p in self._parts if p.strip())


def _strip_html(html_str: str) -> str:
    p = _StripHTML()
    p.feed(html_str)
    return p.get_text()


def _extract_bullets_from_html(html_str: str) -> list[str]:
    # Try <li> tags first
    items = re.findall(r"<li[^>]*>(.*?)</li>", html_str, re.DOTALL | re.IGNORECASE)
    bullets = []
    for item in items:
        text = re.sub(r"<[^>]+>", "", item).strip()
        if 10 < len(text) < 300:
            bullets.append(text)
    if bullets:
        return bullets[:10]
    # Fallback: numbered list lines like "1. text" or "【heading】text"
    plain = _strip_html(html_str)
    for line in plain.split("\n"):
        line = line.strip()
        if re.match(r"^\d+\.\s+.{10,}", line) or re.match(r"^【.{2,20}】.{10,}", line):
            text = re.sub(r"^\d+\.\s+", "", line).strip()
            if 10 < len(text) < 300:
                bullets.append(text)
    return bullets[:10]


# ── Sales estimation ──────────────────────────────────────────────────────────

def _estimate_total_sales(review_count: int, price_usd: float | None, title: str) -> tuple[str, str]:
    if not review_count:
        return "", ""
    t = title.lower()
    if any(k in t for k in ["tablet", "ipad"]):
        total_sales = review_count * 40
    elif any(k in t for k in ["headphone", "earphone", "headset", "earbuds", "auscultadores"]):
        total_sales = int(review_count / 0.035)
    elif any(k in t for k in ["laptop", "notebook", "chromebook"]):
        total_sales = int(review_count / 0.0125)
    elif any(k in t for k in ["smartphone", "telemóvel", "phone"]):
        total_sales = int(review_count / 0.02)
    else:
        total_sales = int(review_count / 0.02)
    total_revenue = round(total_sales * price_usd, 2) if price_usd else ""
    return str(total_sales), str(total_revenue) if total_revenue != "" else ""


# ── Search page parser ────────────────────────────────────────────────────────

def _parse_search_page(html: str, keyword: str) -> list[dict]:
    ld_blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    products: list[dict] = []
    seen: set[str] = set()

    for block in ld_blocks:
        try:
            d = json.loads(block)
        except Exception:
            continue
        if d.get("@type") != "ItemList":
            continue
        items = d.get("itemListElement", [])
        for item in items:
            prod = item.get("item", {})
            sku = prod.get("sku", "")
            if not sku or sku in seen:
                continue
            seen.add(sku)

            offers = prod.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            try:
                price_eur = float(offers.get("price") or 0)
            except (ValueError, TypeError):
                price_eur = None
            if price_eur and price_eur <= 0:
                price_eur = None

            price_usd = round(price_eur * EUR_TO_USD, 2) if price_eur else None
            avail = offers.get("availability", "")
            in_stock = "InStock" in avail

            rating_data = prod.get("aggregateRating") or {}
            try:
                rating = float(str(rating_data.get("ratingValue") or ""))
            except (ValueError, TypeError):
                rating = None
            try:
                review_count = int(str(rating_data.get("reviewCount") or 0).replace(",", ""))
            except (ValueError, TypeError):
                review_count = 0

            url = prod.get("url", "")
            if url and not url.startswith("http"):
                url = _WORTEN_HOME + url
            title = (prod.get("name") or "").strip()

            products.append({
                "sku": sku,
                "asin": sku,
                "keyword": keyword,
                "url": url,
                "title": title,
                "price_eur": price_eur,
                "price": f"${price_usd:.2f}" if price_usd else "",
                "rating": str(rating) if rating is not None else "",
                "review_count": review_count,
                "brand": "",
                "description": "",
                "bullets": [],
                "in_stock": in_stock,
                "is_valid": bool(title) and price_usd is not None and in_stock,
                "crawl_time": datetime.now().isoformat(),
            })

    print(f"[worten search] found {len(products)} products")
    return products


# ── Detail page parser ────────────────────────────────────────────────────────

def _parse_detail_page(html: str) -> dict:
    # ── JSON-LD blocks ─────────────────────────────────────────────────────────
    ld_blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    name = brand = description = sku = ""
    price_eur: float | None = None
    in_stock = True
    rating: float | None = None
    review_count = 0

    # Priority order: PRODUCT > SERVICE (bundle main product) > others
    # Worten uses SERVICE @type for the main product on bundle/pack pages
    candidates = []
    for block in ld_blocks:
        try:
            d = json.loads(block)
        except Exception:
            continue
        t = d.get("@type", "").upper()
        if t in ("PRODUCT", "SERVICE") and d.get("sku"):
            # Prefer PRODUCT with SKU; SERVICE with SKU is the main product on bundle pages
            candidates.append((0 if t == "PRODUCT" else 1, d))
    candidates.sort(key=lambda x: x[0])

    for _, d in candidates:
        name = (d.get("name") or "").strip()
        sku = (d.get("sku") or "").strip()
        brand_raw = d.get("brand") or {}
        brand = brand_raw.get("name", "") if isinstance(brand_raw, dict) else str(brand_raw)
        description = (d.get("description") or "").strip()

        offers = d.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        try:
            price_eur = float(offers.get("price") or 0) or None
        except (ValueError, TypeError):
            price_eur = None
        avail = offers.get("availability", "")
        in_stock = "InStock" in avail or "inStock" in avail or not avail

        rating_data = d.get("aggregateRating") or {}
        try:
            rating = float(str(rating_data.get("ratingValue") or ""))
        except (ValueError, TypeError):
            rating = None
        try:
            review_count = int(str(rating_data.get("reviewCount") or 0).replace(",", ""))
        except (ValueError, TypeError):
            review_count = 0
        break

    # ── Description / bullets from HTML ───────────────────────────────────────
    bullets: list[str] = []
    if description:
        bullets = _extract_bullets_from_html(description)
        description = _strip_html(description)[:800]

    # ── Fallback: NUXT_DATA for long_description ───────────────────────────────
    if not description:
        nuxt_idx = html.find('__NUXT_DATA__">')
        if nuxt_idx >= 0:
            start = nuxt_idx + len('__NUXT_DATA__">')
            end = html.find("</script>", start)
            try:
                nuxt_data = json.loads(html[start:end])
                for item in nuxt_data:
                    if isinstance(item, dict) and "long_description" in item:
                        idx = item["long_description"]
                        if isinstance(idx, int) and 0 <= idx < len(nuxt_data):
                            raw_html = nuxt_data[idx] or ""
                            bullets = _extract_bullets_from_html(raw_html)
                            description = _strip_html(raw_html)[:800]
                        break
            except Exception:
                pass

    # ── Product rating from rendered HTML ─────────────────────────────────────
    # "X avaliações do produto" — distinct from seller reviews
    if not review_count:
        prod_rev_m = re.search(r"(\d+)\s*avalia[çc][oõ]es\s*do\s*produto", html)
        if prod_rev_m:
            review_count = int(prod_rev_m.group(1))
    if not rating:
        star_vals = re.findall(r'rating__star-value[^>]*>[^<]*<[^>]*>([0-9.]+)', html)
        # First value is product rating (if present), second is seller rating
        if star_vals:
            try:
                rating = float(star_vals[0])
            except (ValueError, TypeError):
                pass

    price_usd = round(price_eur * EUR_TO_USD, 2) if price_eur else None

    return {
        "name": name,
        "sku": sku,
        "brand": brand,
        "description": description,
        "bullets": bullets,
        "price_eur": price_eur,
        "price_usd": price_usd,
        "price": f"${price_usd:.2f}" if price_usd else "",
        "rating": str(rating) if rating is not None else "",
        "review_count": review_count,
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


def _llm_summarize(positive: list[str], negative: list[str], product: dict) -> dict:
    from openai import OpenAI

    client = OpenAI(base_url=LLM_BASE_URL, api_key="EMPTY")
    pos_text = "\n".join(f"- {t}" for t in positive[:20])
    neg_text = "\n".join(f"- {t}" for t in negative[:10])
    sys_prompt = (
        "你是一个电商竞品分析助手。"
        "根据葡萄牙语买家评论（正面和负面），用简体中文总结优缺点和综合分析。"
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


# ── Public API ────────────────────────────────────────────────────────────────

async def scrape_worten_products(
    keyword: str,
    max_pages: int = 2,
    max_valid: int = 5,
    headless: bool = False,
) -> list[dict]:
    valid: list[dict] = []
    effective_max_pages = max_pages

    page = 1
    while page <= effective_max_pages:
        if len(valid) >= max_valid:
            break
        if page == 1:
            url = f"{_WORTEN_HOME}/search?query={keyword}"
        else:
            url = f"{_WORTEN_HOME}/search?query={keyword}&page={page}"

        print(f"[worten search] page {page}: {url}")
        try:
            html = await _fetch(url)
            stubs = _parse_search_page(html, keyword)
        except Exception as e:
            print(f"[worten search] page {page} error: {e}")
            page += 1
            continue

        for stub in stubs:
            if len(valid) >= max_valid:
                break
            if not stub.get("is_valid"):
                continue

            detail_url = stub["url"]
            print(f"[worten detail] {stub['sku']}: {detail_url}")
            try:
                detail_html = await _fetch(detail_url)
                detail = _parse_detail_page(detail_html)
            except Exception as e:
                print(f"[worten detail] {stub['sku']} error: {e}")
                detail = {}

            # Skip if detail page returned a completely different product
            detail_sku = detail.get("sku", "")
            if detail_sku and detail_sku != stub["sku"]:
                print(f"[worten detail] SKU mismatch: stub={stub['sku']} detail={detail_sku}, skipping")
                continue

            title = detail.get("name") or stub["title"]
            price_eur = detail.get("price_eur") or stub.get("price_eur")
            price_usd = detail.get("price_usd") or (
                round(price_eur * EUR_TO_USD, 2) if price_eur else None
            )
            rating = detail.get("rating") or stub.get("rating", "")
            review_count = detail.get("review_count") or stub.get("review_count", 0)

            total_sales, total_revenue = _estimate_total_sales(
                review_count, price_usd, title
            )

            product = {
                "sku": stub["sku"],
                "product_id": stub["sku"],
                "asin": stub["sku"],
                "keyword": keyword,
                "url": detail_url,
                "title": title,
                "price_eur": price_eur,
                "price_usd": price_usd,
                "price": f"${price_usd:.2f}" if price_usd else "",
                "rating": rating,
                "review_count": review_count,
                "brand": detail.get("brand", ""),
                "description": detail.get("description", ""),
                "bullets": detail.get("bullets", []),
                "in_stock": detail.get("in_stock", stub.get("in_stock", True)),
                "stock_status": "有货" if detail.get("in_stock", stub.get("in_stock", True)) else "缺货",
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
            print(f"[worten] {stub['sku']} ✓ {title[:50]}")

        if (
            page == effective_max_pages
            and len(valid) < max_valid
            and effective_max_pages < _WORTEN_MAX_AUTO_PAGES
        ):
            effective_max_pages += 1
            print(f"[worten] 有效产品不足 {max_valid}，继续抓第 {effective_max_pages} 页...")
        page += 1

    print(f"[worten] 共 {len(valid)} 个有效产品")
    return valid


async def scrape_worten_reviews(
    sku: str,
    product_url: str,
    max_reviews: int = 60,
) -> dict:
    # Worten does not expose a public review API; return empty.
    print(f"[worten reviews] {sku}: no public review API available")
    return {"pros": [], "cons": [], "overall": ""}
