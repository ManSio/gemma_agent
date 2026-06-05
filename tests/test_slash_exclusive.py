from core.input_handlers.slash_exclusive import orchestrator_should_skip_slash, slash_command_token


def test_slash_token():
    assert slash_command_token("/admin_system@bot") == "admin_system"
    assert slash_command_token("hello") == ""


def test_skip_admin_and_calc():
    assert orchestrator_should_skip_slash("/admin_connectivity")
    assert orchestrator_should_skip_slash("/admin_passport_set x")
    assert orchestrator_should_skip_slash("/auto_suggestions")
    assert not orchestrator_should_skip_slash("/calc 1+2")
    assert not orchestrator_should_skip_slash("/unknown_module_cmd")
    assert orchestrator_should_skip_slash("/help")
    assert orchestrator_should_skip_slash("/filefrom https://x")
