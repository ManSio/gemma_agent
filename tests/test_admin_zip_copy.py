"""Копирование админских ZIP в data/tools."""

from core.admin_zip_copy import admin_diagnostic_copy_to_tools_enabled, copy_admin_zip_to_data_tools


def test_copy_admin_zip_to_data_tools_writes_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    out = copy_admin_zip_to_data_tools(b"zipbytes", "gemma_diagnostic_test.zip")
    assert out is not None
    p = tmp_path / "data" / "tools" / "gemma_diagnostic_test.zip"
    assert p.is_file()
    assert p.read_bytes() == b"zipbytes"


def test_copy_respects_disable_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADMIN_DIAGNOSTIC_COPY_TO_TOOLS", "0")
    assert copy_admin_zip_to_data_tools(b"x", "a.zip") is None
    assert admin_diagnostic_copy_to_tools_enabled() is False
