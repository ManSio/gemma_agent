from core.heuristic_fixes import build_heuristic_hint_block, match_heuristic_hints
from core.user_response_feedback import parse_rate_args


def test_parse_rate_args():
    assert parse_rate_args("+1 отлично")[0] == 1
    assert parse_rate_args("-1 плохо")[0] == -1
    assert parse_rate_args("")[0] is None


def test_heuristic_fixes_match():
    hints = match_heuristic_hints("какой курс рубля к доллару")
    assert hints
    block = build_heuristic_hint_block("последние новости беларуси", intent="news")
    assert "новост" in block.lower() or "HeuristicFix" in block
