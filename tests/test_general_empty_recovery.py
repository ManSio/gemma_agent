from core.brain.general_empty_recovery import (
    _build_retry_prompt,
    dwg_cad_domain_fallback,
    is_dwg_cad_topic,
)


def test_is_dwg_cad_topic():
    assert is_dwg_cad_topic("формат dwg в autocad")
    assert is_dwg_cad_topic("PDF красивый, а DWG буквы съезжают")
    assert not is_dwg_cad_topic("почему земля круглая")


def test_dwg_why_mess_fallback():
    text = "А почему в PDF всё красиво, а в DWG надписи в кучу?"
    fb = dwg_cad_domain_fallback(text)
    assert "шрифт" in fb.lower() or "PDF" in fb


def test_no_freecad_fallback():
    fb = dwg_cad_domain_fallback("У меня нету фрикада")
    assert "TrueView" in fb or "Viewer" in fb
    assert "FreeCAD не обязателен" in fb


def test_retry_prompt_includes_context():
    rows = [
        {"role": "user", "text": "что такое dwg"},
        {"role": "assistant", "text": "формат AutoCAD"},
        {"role": "user", "text": "А как?"},
    ]
    p = _build_retry_prompt("А как?", recent_dialogue=rows)
    assert "А как?" in p
    assert "AutoCAD" in p
