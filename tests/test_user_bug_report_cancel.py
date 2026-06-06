from core.user_bug_report import should_cancel_bug_report_pending


def test_cancel_musor_single_word():
    assert should_cancel_bug_report_pending("мусор")


def test_cancel_wrong_understanding():
    assert should_cancel_bug_report_pending("Ты неправильно понял")


def test_real_bug_description_not_cancelled():
    assert not should_cancel_bug_report_pending(
        "После вопроса про стул бот прислал JSON вместо ответа, см. скрин"
    )
