import io
import zipfile

from core.admin_bug_report import build_bug_report_document, parse_admin_bug_command_args
from core.diagnostic_bundle import admin_bug_report_zip_bytes


def test_parse_admin_bug_defaults(monkeypatch):
    monkeypatch.delenv("ADMIN_BUG_LOG_LINES", raising=False)
    net, n, comp, full, note = parse_admin_bug_command_args("")
    assert net is False and n == 80 and comp is None and full is False and note is None


def test_parse_admin_bug_net_note(monkeypatch):
    monkeypatch.delenv("ADMIN_BUG_LOG_LINES", raising=False)
    net, n, comp, full, note = parse_admin_bug_command_args("net ожидал другое")
    assert net is True and n == 80 and comp is None and full is False and note == "ожидал другое"


def test_parse_admin_bug_comp_and_n(monkeypatch):
    monkeypatch.delenv("ADMIN_BUG_LOG_LINES", raising=False)
    net, n, comp, full, note = parse_admin_bug_command_args("net 40 comp=voice")
    assert net and n == 40 and comp == "voice" and full is False and note is None


def test_parse_admin_bug_full_flag(monkeypatch):
    monkeypatch.delenv("ADMIN_BUG_LOG_LINES", raising=False)
    net, n, comp, full, note = parse_admin_bug_command_args("full comp=brain note")
    assert net is False and n == 80 and comp == "brain" and full is True and note == "note"


def test_admin_bug_zip_contents():
    z = admin_bug_report_zip_bytes(
        {"x": 1},
        bug_report={"report_version": 1, "recent_chat_tail": [], "event_timeline": []},
        logs_snapshot={"body": "line1\n", "n": 2},
    )
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        names = set(zf.namelist())
    assert {
        "bundle_summary.json",
        "incident_context.json",
        "bug_report.json",
        "logs_snapshot.json",
        "logs_snapshot.txt",
    }.issubset(names)
    assert "bundle.json" not in names
    assert "КАК_ЧИТАТЬ_БАГРЕПОРТ.txt" in names


def test_admin_bug_zip_full_bundle_contents():
    z = admin_bug_report_zip_bytes(
        {"x": 1},
        bug_report={"report_version": 1, "recent_chat_tail": [], "event_timeline": []},
        logs_snapshot={"body": "line1\n", "n": 2},
        include_full_bundle=True,
    )
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        names = set(zf.namelist())
    assert "bundle.json" in names


def test_build_bug_report_document_reply_chain():
    class U:
        id = 1
        username = "t"
        is_bot = False

    class C:
        id = -100

    class M:
        message_id = 10
        date = None
        chat = C()
        from_user = U()
        text = "parent"
        caption = None
        reply_to_message = None

    class R:
        message_id = 11
        date = None
        chat = C()
        from_user = U()
        is_bot = True
        text = "bad reply"
        caption = None
        reply_to_message = M()

    doc = build_bug_report_document(
        command_chat_id=-100,
        command_message_id=12,
        reporter_user=U(),
        human_note="note",
        reply_to=R(),
    )
    assert doc["reply_missing"] is False
    assert doc["reply_to"]["text_or_caption"] == "bad reply"
    assert doc["reply_parent"]["text_or_caption"] == "parent"
