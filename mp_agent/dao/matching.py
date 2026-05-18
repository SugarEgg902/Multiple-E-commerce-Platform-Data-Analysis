# mp_agent/dao/matching.py
from __future__ import annotations

import asyncio
import re

import jieba
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity as sk_cosine
from sqlalchemy import select, update

from mp_agent.dao.db import get_async_session
from mp_agent.dao.models import GlobalProduct, PlatformProduct

_MATCH_THRESHOLD = 0.85


def _tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese/English text for TF-IDF."""
    chinese = "".join(re.findall(r"[一-鿿]+", text))
    english = re.sub(r"[一-鿿]", " ", text)
    tokens = list(jieba.cut(chinese)) if chinese else []
    tokens += english.lower().split()
    return [t.strip() for t in tokens if t.strip()]


def _cosine_similarity(a: str, b: str) -> float:
    """Compute TF-IDF cosine similarity between two strings."""
    vec = TfidfVectorizer(tokenizer=_tokenize, lowercase=False)
    try:
        tfidf = vec.fit_transform([a, b])
        return float(sk_cosine(tfidf[0], tfidf[1])[0][0])
    except Exception:
        return 0.0


async def match_and_assign_global_product(product_id: int, title: str) -> None:
    """Find or create a global_product for the given platform_product."""
    async with get_async_session() as session:
        result = await session.execute(
            select(GlobalProduct.id, GlobalProduct.canonical_title)
        )
        candidates = result.all()

    best_id: int | None = None
    best_score = 0.0
    for gp_id, canonical_title in candidates:
        score = _cosine_similarity(title, canonical_title)
        if score > best_score:
            best_score = score
            best_id = gp_id

    async with get_async_session() as session:
        if best_score >= _MATCH_THRESHOLD and best_id is not None:
            global_product_id = best_id
        else:
            gp = GlobalProduct(canonical_title=title)
            session.add(gp)
            await session.flush()
            global_product_id = gp.id

        await session.execute(
            update(PlatformProduct)
            .where(PlatformProduct.id == product_id)
            .values(global_product_id=global_product_id, match_confidence=round(best_score, 3))
        )


def schedule_matching(product_id: int, title: str) -> None:
    """Fire-and-forget: schedule TF-IDF matching without blocking the caller."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(match_and_assign_global_product(product_id, title))
    except RuntimeError:
        asyncio.run(match_and_assign_global_product(product_id, title))
