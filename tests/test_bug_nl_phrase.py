from core.admin_bug_report import bug_nl_args_remainder, prose_wants_bug_report_capture


def test_prose_wants_bug_nl():
    assert prose_wants_bug_report_capture("зафиксируй баг")
    assert prose_wants_bug_report_capture("Зафиксируй баг net 40")
    assert prose_wants_bug_report_capture("🐞 зафиксируй баг")
    assert prose_wants_bug_report_capture("привет\nзафиксируй баг comp=voice")
    assert not prose_wants_bug_report_capture("/admin_bug")
    assert not prose_wants_bug_report_capture("")
    assert not prose_wants_bug_report_capture("просто текст про баг без команды")


def test_bug_nl_args_remainder():
    assert bug_nl_args_remainder("зафиксируй баг net ожидал другое") == "net ожидал другое"
    assert bug_nl_args_remainder("зафиксируй баг\nnet 50") == "net 50"
