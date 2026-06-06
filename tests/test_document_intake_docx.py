import os
import tempfile

from core.document_intake import DocumentIntakeModule


def test_docx_paragraphs_and_table():
    docx = __import__("docx")
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "claim.docx")
        d = docx.Document()
        d.add_paragraph("Претензия шапка")
        table = d.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Сумма"
        table.cell(0, 1).text = "1000 руб"
        d.save(path)

        mod = DocumentIntakeModule()
        out = mod.parse_file(path)
        assert out.get("ok") is True
        text = out.get("text") or ""
        assert "Претензия" in text
        assert "1000" in text


def test_sniff_docx_without_extension():
    docx = __import__("docx")
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "gemma_file_noext")
        d = docx.Document()
        d.add_paragraph("no extension body")
        d.save(path)

        mod = DocumentIntakeModule()
        out = mod.parse_file(path)
        assert out.get("ok") is True
        assert "no extension" in (out.get("text") or "")
