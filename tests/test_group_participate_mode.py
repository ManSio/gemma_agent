from __future__ import annotations

from core.input_layer import _group_open_question


def test_group_open_question_markers():
    assert _group_open_question("Как дела?")
    assert _group_open_question("кто был на встрече")
    assert _group_open_question("что делать дальше")
    assert not _group_open_question("всем привет")
    assert not _group_open_question("")
    assert not _group_open_question("/help")
