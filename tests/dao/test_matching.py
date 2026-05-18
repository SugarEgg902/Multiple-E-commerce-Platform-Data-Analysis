# tests/dao/test_matching.py
from mp_agent.dao.matching import _tokenize, _cosine_similarity


def test_tokenize_english():
    tokens = _tokenize("Doogee S98 Pro Rugged Smartphone")
    assert "doogee" in tokens
    assert "s98" in tokens


def test_tokenize_chinese():
    tokens = _tokenize("多格手机 防水耐摔")
    assert len(tokens) > 0


def test_cosine_similarity_identical():
    score = _cosine_similarity("Doogee S98 Pro", "Doogee S98 Pro")
    assert score >= 0.99


def test_cosine_similarity_different():
    score = _cosine_similarity("Doogee S98 Pro", "Samsung Galaxy S23")
    assert score < 0.5
