from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from html.parser import HTMLParser

import httpx

from config.config import EUR_TO_USD, LLM_BASE_URL, LLM_MODEL
from mp_agent.infrastructure._flaresolverr import flare_fetch

_FLARESOLVERR_URL = "http://localhost:8191/v1"
_KAUFLAND_HOME = "https://www.kaufland.de"
_SESSION = "kaufland_session"
_TIMEOUT = 180  # seconds for httpx; FlareSolverr maxTimeout is 180000 ms

_KAUFLAND_MAX_AUTO_PAGES = 6


# ── FlareSolverr fetch ────────────────────────────────────────────────────────

def _kaufland_post_process(html: str) -> str:
    return html.replace("\\u002F", "/")


async def _fetch(url: str) -> str:
    """Fetch a URL through FlareSolverr and return the HTML string."""
    return await flare_fetch(
        url,
        session=_SESSION,
        max_timeout=180_000,
        http_timeout=_TIMEOUT + 30,
        platform="kaufland",
        post_process=_kaufland_post_process,
    )


async def _fetch_json(url: str) -> dict:
    """
    Fetch a JSON API endpoint through FlareSolverr.
    FlareSolverr wraps the JSON response in <pre>...</pre> tags.
    """
    html = await _fetch(url)
    m = re.search(r"<pre[^>]*>([\s\S]*?)</pre>", html)
    if not m:
        raise RuntimeError(f"No <pre> JSON wrapper in FlareSolverr response for {url}")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse JSON from {url}: {e}")


# ── SSR IIFE variable decoder ─────────────────────────────────────────────────

def _decode_ssr_vars(script_text: str) -> dict:
    """
    Kaufland embeds data in minified IIFE scripts:
      (function(a,b,c,...){return {...}}(val1,val2,...))
    We extract the params list and args list, then build a var_map.
    """
    # Find the outermost IIFE: (function(...){...}(...))
    m = re.search(r'\(function\(([^)]*)\)\s*\{([\s\S]*?)\}\s*\(([^)]*(?:\([^)]*\)[^)]*)*)\)\s*\)', script_text)
    if not m:
        return {}

    params_str = m.group(1)
    args_str = m.group(3)

    params = [p.strip() for p in params_str.split(",") if p.strip()]

    # Split args carefully — values may contain nested parens/strings
    args = _split_args(args_str)

    var_map: dict[str, object] = {}
    for i, param in enumerate(params):
        if i < len(args):
            raw = args[i].strip()
            var_map[param] = _coerce(raw)

    return var_map


def _split_args(s: str) -> list[str]:
    """Split a comma-separated argument string respecting nesting."""
    parts: list[str] = []
    depth = 0
    in_str: str | None = None
    buf: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if in_str:
            buf.append(c)
            if c == "\\" and i + 1 < len(s):
                i += 1
                buf.append(s[i])
            elif c == in_str:
                in_str = None
        elif c in ('"', "'", "`"):
            in_str = c
            buf.append(c)
        elif c in ("(", "[", "{"):
            depth += 1
            buf.append(c)
        elif c in (")", "]", "}"):
            depth -= 1
            buf.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        else:
            buf.append(c)
        i += 1
    if buf:
        parts.append("".join(buf))
    return parts


def _coerce(raw: str):
    """Convert a raw JS literal string to a Python value."""
    if raw in ("true", "!0"):
        return True
    if raw in ("false", "!1"):
        return False
    if raw in ("null", "undefined", "void 0"):
        return None
    if (raw.startswith('"') and raw.endswith('"')) or \
       (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


# ── Extract named SSR script block ────────────────────────────────────────────

def _extract_ssr_script(html: str, key: str) -> str:
    """
    Find the inline <script> that assigns window["APP_SHELL_SSR_STATE_<key>"] = (...).
    Returns the IIFE text (everything after the '= '), or empty string if not found.

    Strategy: locate the window assignment by the full SSR_STATE key, then find its
    enclosing <script> block and extract from '= ' to </script>.
    This avoids fragile IIFE-boundary regexes that break when args contain '<'.
    """
    marker = f'window["APP_SHELL_SSR_STATE_{key}"]'
    pos = html.find(marker)
    if pos == -1:
        return ""

    script_start = html.rfind("<script>", 0, pos)
    if script_start == -1:
        return ""
    script_end = html.find("</script>", pos)
    if script_end == -1:
        return ""

    script_body = html[script_start + 8 : script_end]  # strip <script>

    eq_pos = script_body.find("] = ")
    if eq_pos == -1:
        return ""

    # Everything after '= ', strip trailing semicolon
    return script_body[eq_pos + 4 :].rstrip("; \n\r\t")


# ── Search page parser ────────────────────────────────────────────────────────

def _parse_search_page(html: str, keyword: str) -> list[dict]:
    """
    Extract product stubs from Kaufland search page SSR state.
    Returns list of {product_id, unit_id, ean, title, rating, review_count, url}.
    Price is intentionally omitted — fetch detail page for reliable price.
    """
    script = _extract_ssr_script(html, "@mf/search-frontend-vue3")
    if not script:
        print("[kaufland search] SSR script not found")
        return []

    var_map = _decode_ssr_vars(script)
    if not var_map:
        print("[kaufland search] var_map is empty")
        return []

    # Locate the tile section: products array starts after 'id:XX,unitId:YY'
    tile_re = re.compile(r'\{id:([A-Za-z0-9_$]+),unitId:([A-Za-z0-9_$]+),')
    products: list[dict] = []
    seen: set[str] = set()

    for m in tile_re.finditer(script):
        id_var = m.group(1)
        unit_var = m.group(2)

        product_id = var_map.get(id_var)
        unit_id = var_map.get(unit_var)
        if not product_id or not unit_id:
            continue

        product_id_str = str(product_id)
        if product_id_str in seen:
            continue
        seen.add(product_id_str)

        # Grab block from this match onwards to pull rating/title/ean
        block = script[m.start():m.start() + 2000]

        # title: first string literal that looks like a product title
        # In the tile block, ean appears as a literal string like "4051234567890"
        ean_m = re.search(r'ean:"([^"]{10,})"', block)
        ean = ean_m.group(1) if ean_m else ""

        title_m = re.search(r'title:"([^"]{10,})"', block)
        title = title_m.group(1) if title_m else ""

        # rating: ratings.average variable
        avg_m = re.search(r'average:([A-Za-z0-9_$]+)', block)
        rating = None
        if avg_m:
            v = var_map.get(avg_m.group(1))
            try:
                rating = float(v) if v is not None else None
            except (ValueError, TypeError):
                pass

        # review count: ratings.count variable
        cnt_m = re.search(r'count:([A-Za-z0-9_$]+)', block)
        review_count = 0
        if cnt_m:
            v = var_map.get(cnt_m.group(1))
            try:
                review_count = int(v) if v is not None else 0
            except (ValueError, TypeError):
                pass

        url = f"{_KAUFLAND_HOME}/product/{product_id_str}/?id_unit={unit_id}"

        products.append({
            "product_id": product_id_str,
            "unit_id": str(unit_id),
            "ean": ean,
            "keyword": keyword,
            "title": title,
            "rating": str(rating) if rating is not None else "",
            "review_count": review_count,
            "url": url,
            "is_valid": bool(title) and len(title) > 5,
        })

    print(f"[kaufland search] found {len(products)} products")
    return products


# ── Detail page parser ────────────────────────────────────────────────────────

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
    items = re.findall(r'<li[^>]*>(.*?)</li>', html_str, re.DOTALL | re.IGNORECASE)
    bullets = []
    for item in items:
        text = re.sub(r'<[^>]+>', '', item).strip()
        if 10 < len(text) < 300:
            bullets.append(text)
    return bullets[:10]


def _resolve_val(raw: str, var_map: dict) -> str:
    """Resolve a JS token: literal string → strip quotes; variable name → look up var_map."""
    raw = raw.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    v = var_map.get(raw)
    return str(v) if v is not None else ""


def _parse_detail_page(html: str, product_id: str) -> dict:
    """
    Parse Kaufland PDP page SSR state for price, reviews, description, seller.
    Returns a dict with price_eur, price_usd, price, rating, review_count,
    description, bullets, seller, category, brand, condition.
    """
    script = _extract_ssr_script(html, "@mf/pdp-frontend")
    if not script:
        print(f"[kaufland detail] SSR script not found for {product_id}")
        return {}

    var_map = _decode_ssr_vars(script)

    # ── condition ──────────────────────────────────────────────────────────────
    # buyBox.offer.condition.statusKey: "new" | "used_as_new" | "used"
    condition = "new"
    cond_m = re.search(r'condition:\{statusKey:"([^"]+)"', script)
    if cond_m:
        condition = cond_m.group(1)

    # ── price ──────────────────────────────────────────────────────────────────
    # For used/refurb listings, prefer lowestNewPrice over the buyBox price.
    # lowestNewPrice:g means g=0 (no new listing tracked) → fall back to buyBox price.
    price_eur: float | None = None

    if condition != "new":
        # Try lowestNewPrice first
        lnp_m = re.search(r'lowestNewPrice:([A-Za-z0-9_$]+)', script)
        if lnp_m:
            v = var_map.get(lnp_m.group(1))
            try:
                f = float(v)  # type: ignore
                if f > 1.0:
                    price_eur = f
            except (ValueError, TypeError):
                pass
        # Try uvp (UVP = Unverbindliche Preisempfehlung = RRP)
        if price_eur is None:
            uvp_m = re.search(r'uvp:([A-Za-z0-9_$]+)', script)
            if uvp_m:
                v = var_map.get(uvp_m.group(1))
                try:
                    f = float(v)  # type: ignore
                    if f > 1.0:
                        price_eur = f
                except (ValueError, TypeError):
                    pass

    # Fall back to buyBox price (also used for new listings)
    if price_eur is None:
        buy_box_m = re.search(r'buyBox\b[\s\S]{0,300}?\bprice:([A-Za-z0-9_$]+)', script)
        if buy_box_m:
            v = var_map.get(buy_box_m.group(1))
            try:
                price_eur = float(v) if v is not None else None
            except (ValueError, TypeError):
                pass

    # Last resort: first plausible price value in script
    if price_eur is None:
        for pm in re.finditer(r'\bprice:([A-Za-z0-9_$]+)', script):
            v = var_map.get(pm.group(1))
            try:
                f = float(v)  # type: ignore
                if 1.0 < f < 10000.0:
                    price_eur = f
                    break
            except (ValueError, TypeError):
                pass

    price_usd = round(price_eur * EUR_TO_USD, 2) if price_eur else None
    price_str = f"${price_usd:.2f}" if price_usd else ""

    # ── reviews ────────────────────────────────────────────────────────────────
    rating: float | None = None
    review_count = 0
    rv_m = re.search(r'reviewMetaData\b[\s\S]{0,200}?numberOfReviews:([A-Za-z0-9_$]+)', script)
    star_m = re.search(r'reviewMetaData\b[\s\S]{0,200}?numberOfStars:([A-Za-z0-9_$]+)', script)
    if rv_m:
        v = var_map.get(rv_m.group(1))
        try:
            review_count = int(v) if v is not None else 0
        except (ValueError, TypeError):
            pass
    if star_m:
        v = var_map.get(star_m.group(1))
        try:
            rating = float(v) if v is not None else None
        except (ValueError, TypeError):
            pass

    # ── seller ─────────────────────────────────────────────────────────────────
    seller = ""
    seller_m = re.search(r'seller\b[\s\S]{0,300}?name:"([^"]{1,100})"', script)
    if seller_m:
        seller = seller_m.group(1)

    # ── category / brand ───────────────────────────────────────────────────────
    category = ""
    cat_m = re.search(r'mainCategoryTitle:"([^"]{1,100})"', script)
    if cat_m:
        category = cat_m.group(1)

    brand = ""
    brand_m = re.search(r'manufacturerData\b[\s\S]{0,200}?name:([A-Za-z0-9_$]+)', script)
    if brand_m:
        v = var_map.get(brand_m.group(1))
        if isinstance(v, str) and v:
            brand = v
    if not brand:
        brand_lit_m = re.search(r'manufacturerData\b[\s\S]{0,200}?name:"([^"]{1,80})"', script)
        if brand_lit_m:
            brand = brand_lit_m.group(1)

    # ── description / bullets ──────────────────────────────────────────────────
    description = ""
    bullets: list[str] = []
    desc_m = re.search(r'descriptionHtml:"((?:[^"\\]|\\.)*)"', script)
    if desc_m:
        raw_html = desc_m.group(1).encode().decode('unicode_escape')
        bullets = _extract_bullets_from_html(raw_html)
        description = _strip_html(raw_html)[:800]

    # ── product attributes / spec table ───────────────────────────────────────
    attrs: list[str] = []
    attr_block_m = re.search(r'attributes:\[([\s\S]{0,6000}?)\]', script)
    if attr_block_m:
        attr_re = re.compile(
            r'\{[^}]*?name:([A-Za-z0-9_$"\']+)[^}]*?value:([A-Za-z0-9_$"\']+)[^}]*?\}'
        )
        for am in attr_re.finditer(attr_block_m.group(1)):
            name = _resolve_val(am.group(1), var_map)
            value = _resolve_val(am.group(2), var_map)
            if name and value and len(name) < 100 and len(value) < 200:
                attrs.append(f"{name}: {value}")
    specs = "; ".join(attrs[:20])

    # ── stock status ───────────────────────────────────────────────────────────
    stock_status = ""
    avail_m = re.search(r'\bavailability\b[\s\S]{0,50}?:([A-Za-z0-9_$"\']+)', script)
    if avail_m:
        raw_av = avail_m.group(1).strip().strip('"\'')
        resolved = var_map.get(raw_av, raw_av)
        _av_map = {
            "available": "有货", "in_stock": "有货", "inStock": "有货",
            "not_available": "缺货", "out_of_stock": "缺货", "unavailable": "缺货",
            "limited": "库存紧张",
        }
        stock_status = _av_map.get(str(resolved), str(resolved))
    if not stock_status:
        instock_m = re.search(r'\binStock:([A-Za-z0-9_$]+)', script)
        if instock_m:
            v = var_map.get(instock_m.group(1), instock_m.group(1))
            stock_status = "有货" if v in (True, "true") else ("缺货" if v in (False, "false") else "")

    # ── number of sellers / offers ─────────────────────────────────────────────
    seller_count = ""
    offers_m = re.search(r'\bnumberOfOffers:([A-Za-z0-9_$]+)', script)
    if offers_m:
        v = var_map.get(offers_m.group(1))
        try:
            seller_count = str(int(v)) if v is not None else ""
        except (ValueError, TypeError):
            pass

    # ── shipping cost ──────────────────────────────────────────────────────────
    shipping_cost = ""
    ship_m = re.search(r'\bshippingCost:([A-Za-z0-9_$]+)', script)
    if ship_m:
        v = var_map.get(ship_m.group(1))
        try:
            f = float(v)  # type: ignore
            shipping_cost = "免运费" if f == 0.0 else f"€{f:.2f}"
        except (ValueError, TypeError):
            pass
    if not shipping_cost:
        ship2_m = re.search(r'\bshipping\b[\s\S]{0,200}?\bprice:([A-Za-z0-9_$]+)', script)
        if ship2_m:
            v = var_map.get(ship2_m.group(1))
            try:
                f = float(v)  # type: ignore
                shipping_cost = "免运费" if f == 0.0 else f"€{f:.2f}"
            except (ValueError, TypeError):
                pass

    return {
        "price_eur": price_eur,
        "price_usd": price_usd,
        "price": price_str,
        "condition": condition,
        "rating": str(rating) if rating is not None else "",
        "review_count": review_count,
        "description": description,
        "bullets": bullets,
        "seller": seller,
        "category": category,
        "brand": brand,
        "specs": specs,
        "stock_status": stock_status,
        "seller_count": seller_count,
        "shipping_cost": shipping_cost,
    }


# ── Review extraction ─────────────────────────────────────────────────────────

_REVIEW_API = "https://www.kaufland.de/api/product-reviews-frontend/v1/reviews/product/{product_id}"


async def _fetch_reviews_api(product_id: str, max_reviews: int = 60) -> list[dict]:
    """
    Fetch reviews from Kaufland's review API via FlareSolverr.
    Returns list of {rating, text}.
    """
    reviews: list[dict] = []
    page = 1
    page_size = 20

    while len(reviews) < max_reviews:
        url = (
            f"{_REVIEW_API.format(product_id=product_id)}"
            f"?sortBy=bestFirst&pageSize={page_size}&page={page}"
        )
        print(f"[kaufland reviews] API page {page}: {url}")
        try:
            data = await _fetch_json(url)
        except Exception as e:
            print(f"[kaufland reviews] API error page {page}: {e}")
            break

        page_reviews = data.get("reviews") or []
        if not page_reviews:
            break

        for r in page_reviews:
            raw_text = r.get("text") or ""
            # API returns HTML-escaped text; unescape &lt; &gt; &amp; <br />
            text = re.sub(r"<br\s*/?>", " ", raw_text)
            text = re.sub(r"<[^>]+>", "", text)
            text = (text
                    .replace("&lt;", "<").replace("&gt;", ">")
                    .replace("&amp;", "&").replace("&quot;", '"')
                    .replace("&#39;", "'"))
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 10:
                reviews.append({"rating": int(r.get("rating", 3)), "text": text[:600]})

        total = data.get("totalReviews", 0)
        total_pages = (total + page_size - 1) // page_size if total else 0
        if len(reviews) >= max_reviews or not total_pages or page >= total_pages:
            break
        page += 1

    print(f"[kaufland reviews] fetched {len(reviews)} reviews")
    return reviews


# ── LLM helpers ───────────────────────────────────────────────────────────────

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

async def scrape_kaufland_products(
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
            url = f"{_KAUFLAND_HOME}/s/?search_value={keyword}"
        else:
            url = f"{_KAUFLAND_HOME}/s/?search_value={keyword}&page={page}"

        print(f"[kaufland search] page {page}: {url}")
        try:
            html = await _fetch(url)
            stubs = _parse_search_page(html, keyword)
        except Exception as e:
            print(f"[kaufland search] page {page} error: {e}")
            page += 1
            continue

        for stub in stubs:
            if len(valid) >= max_valid:
                break
            if not stub.get("is_valid"):
                continue

            product_id = stub["product_id"]
            unit_id = stub["unit_id"]
            # Fetch without id_unit to get the default (new) offer price.
            # id_unit pins to a specific seller which may be used/refurb.
            detail_url = f"{_KAUFLAND_HOME}/product/{product_id}/?search_value={keyword}"
            print(f"[kaufland detail] {product_id}: {detail_url}")

            try:
                detail_html = await _fetch(detail_url)
                detail = _parse_detail_page(detail_html, product_id)
            except Exception as e:
                print(f"[kaufland detail] {product_id} error: {e}")
                detail = {}

            product = {
                **stub,
                **detail,
                "asin": product_id,
                "crawl_time": datetime.now().isoformat(),
            }
            if not product.get("rating") and stub.get("rating"):
                product["rating"] = stub["rating"]
            if not product.get("review_count") and stub.get("review_count"):
                product["review_count"] = stub["review_count"]

            valid.append(product)
            print(f"[kaufland] {product_id} ✓ {product.get('title', '')[:50]}")

        if (page == effective_max_pages
                and len(valid) < max_valid
                and effective_max_pages < _KAUFLAND_MAX_AUTO_PAGES):
            effective_max_pages += 1
            print(f"[kaufland] 有效产品不足 {max_valid}，继续抓第 {effective_max_pages} 页...")
        page += 1

    print(f"[kaufland] 共 {len(valid)} 个有效产品")
    return valid


async def scrape_kaufland_reviews(
    product_id: str,
    product_url: str,
    max_reviews: int = 60,
) -> dict:
    """Fetch reviews via Kaufland's review API (through FlareSolverr) and LLM-summarize."""
    try:
        reviews = await _fetch_reviews_api(product_id, max_reviews=max_reviews)
    except Exception as e:
        print(f"[kaufland reviews] error: {e}")
        return {"pros": [], "cons": [], "overall": ""}

    if not reviews:
        return {"pros": [], "cons": [], "overall": ""}

    positive = [r["text"] for r in reviews if r["rating"] >= 4]
    negative = [r["text"] for r in reviews if r["rating"] <= 3]
    print(f"[kaufland reviews] {len(positive)} positive, {len(negative)} negative")

    return await asyncio.to_thread(_llm_summarize, positive, negative, {})
