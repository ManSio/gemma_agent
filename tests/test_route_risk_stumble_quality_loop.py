"""Флаг detail quality_loop:* в route_risk — не путать с инцидентом маршрутизации."""

from core.route_risk_memory import stumble_from_turn_quality_loop


def test_stumble_from_turn_quality_loop_positive():
    assert stumble_from_turn_quality_loop("quality_loop:reply_echo")
    assert stumble_from_turn_quality_loop("  quality_loop:price_hallucination  ")
    assert stumble_from_turn_quality_loop("prefix quality_loop:x")


def test_stumble_from_turn_quality_loop_negative():
    assert not stumble_from_turn_quality_loop("")
    assert not stumble_from_turn_quality_loop("timeout")
    assert not stumble_from_turn_quality_loop("hallucination policy")
