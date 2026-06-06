"""Тесты ZIP-команд модуля tools."""
import asyncio
import os
import tempfile
import zipfile

import pytest

from modules.tools.module import ToolsModule


@pytest.fixture()
def tools_tmp():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _run(mod: ToolsModule, payload: str, context: dict | None = None):
    return asyncio.run(
        mod.execute(
            {
                "input": {"payload": payload},
                "context": context or {},
            }
        )
    )


def test_zip_list_and_read_from_storage(tools_tmp):
    inner = os.path.join(tools_tmp, "diag.zip")
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle.json", '{"ok": true}')
        zf.writestr("readme.txt", "hello")

    mod = ToolsModule({"storage_path": tools_tmp})
    outs = _run(mod, "/zip_list diag.zip")
    assert len(outs) == 1
    assert "bundle.json" in outs[0].payload
    assert "readme.txt" in outs[0].payload

    outs2 = _run(mod, "/zip_read diag.zip bundle.json full=1")
    assert '"ok": true' in outs2[0].payload

    outs_sum = _run(mod, "/zip_read diag.zip bundle.json")
    assert "сводка" in outs_sum[0].payload.lower() or "Режим: сводка" in outs_sum[0].payload

    outs3 = _run(mod, "/zip_read diag.zip")
    assert '"ok": true' in outs3[0].payload or "ok" in outs3[0].payload


def test_zip_read_bundle_json_autopicks_gemma_diagnostic(tools_tmp):
    """/zip_read bundle.json без вложения — новейший gemma_diagnostic_*.zip из data/tools."""
    zpath = os.path.join(tools_tmp, "gemma_diagnostic_20991231_120000.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle.json", '{"autopick": true}')

    mod = ToolsModule({"storage_path": tools_tmp})
    outs = _run(mod, "/zip_read bundle.json")
    assert len(outs) == 1
    assert "Использован архив" in outs[0].payload
    assert "autopick" in outs[0].payload


def test_zip_read_bundle_json_autopicks_gemma_bugreport_if_no_diagnostic(tools_tmp):
    """При отсутствии diagnostic — берётся новейший gemma_bugreport_*.zip."""
    zpath = os.path.join(tools_tmp, "gemma_bugreport_20991231_120000.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle.json", '{"from_bug": true}')

    mod = ToolsModule({"storage_path": tools_tmp})
    outs = _run(mod, "/zip_read bundle.json")
    assert len(outs) == 1
    assert "bugreport" in outs[0].payload.lower() or "gemma_bugreport" in outs[0].payload
    assert "from_bug" in outs[0].payload


def test_zip_read_attachment_context(tools_tmp):
    inner = os.path.join(tools_tmp, "up.zip")
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", "alpha")

    mod = ToolsModule({"storage_path": tools_tmp})
    ctx = {
        "file_context": {
            "local_path": inner,
            "original_name": "up.zip",
            "mime_type": "application/zip",
        }
    }
    outs = _run(mod, "/zip_read a.txt", ctx)
    assert "alpha" in outs[0].payload


def test_zip_pack(tools_tmp):
    mod = ToolsModule({"storage_path": tools_tmp})
    mod._save_file("one.txt", "1")
    mod._save_file("two.txt", "2")
    outs = _run(mod, "/zip_pack packed.zip one.txt two.txt")
    assert "Собран" in outs[0].payload or "packed.zip" in outs[0].payload

    out_zip = os.path.join(tools_tmp, "packed.zip")
    assert os.path.isfile(out_zip)
    with zipfile.ZipFile(out_zip, "r") as zf:
        names = set(zf.namelist())
    assert names == {"one.txt", "two.txt"}
