from core.qdrant_rag import chunk_book_text, _stable_point_id


def test_chunk_book_text_splits_long_text():
    parts = chunk_book_text("x" * 1200, max_chars=400, overlap=40)
    assert len(parts) >= 2
    assert all(len(p) <= 450 for p in parts)


def test_stable_point_id_deterministic():
    assert _stable_point_id("abc", 0) == _stable_point_id("abc", 0)
    assert _stable_point_id("abc", 1) != _stable_point_id("abc", 0)
