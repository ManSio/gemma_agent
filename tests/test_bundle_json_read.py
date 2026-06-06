"""Сводка и выборочное чтение bundle.json."""

import json

from core.bundle_json_read import parse_zip_inner_spec, shape_bundle_json_payload


def test_parse_zip_inner_spec():
    a, o = parse_zip_inner_spec("bundle.json section=env full=1")
    assert a == "bundle.json"
    assert o["section"] == "env"
    assert o["full"] == "1"


def test_shape_bundle_summary_includes_extra_keys():
    raw = json.dumps({"autopick": True, "bundle_version": 2}, ensure_ascii=False)
    body = shape_bundle_json_payload(
        f"=== bundle.json (10 байт) ===\n{raw}",
        {},
        member_label="bundle.json",
    )
    assert "сводка" in body.lower() or "Режим: сводка" in body
    assert "autopick" in body


def test_shape_bundle_section():
    raw = json.dumps({"performance": {"cpu": 1}, "env": {"x": 1}}, ensure_ascii=False)
    body = shape_bundle_json_payload(
        f"=== bundle.json ===\n{raw}",
        {"section": "performance"},
        member_label="bundle.json",
    )
    assert "cpu" in body


def test_shape_bundle_chunk():
    raw = json.dumps({"a": "hello"}, ensure_ascii=False)
    body = shape_bundle_json_payload(
        f"=== bundle.json ===\n{raw}",
        {"chunk": "1/2"},
        member_label="bundle.json",
    )
    assert "[Фрагмент текста 1/2" in body
