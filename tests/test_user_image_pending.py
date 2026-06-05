from __future__ import annotations

from pathlib import Path

from core.user_image_pending import (
    clear_pending_images,
    has_pending_image,
    pop_pending_image,
    pop_pending_images,
    register_pending_image,
)


def test_register_and_pop_pending_image(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    src = tmp_path / "in.jpg"
    src.write_bytes(b"fake_jpeg")
    rec = register_pending_image(
        "u1",
        "c1",
        {
            "file_type": "image",
            "local_path": str(src),
            "mime_type": "image/jpeg",
            "original_name": "in.jpg",
        },
    )
    assert isinstance(rec, dict)
    got = pop_pending_image("u1", "c1")
    assert isinstance(got, dict)
    assert got.get("file_type") == "image"
    assert Path(str(got.get("local_path"))).is_file()


def test_clear_pending_images(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    p1 = tmp_path / "old.jpg"
    p1.write_bytes(b"old")
    register_pending_image("u3", "c3", {"file_type": "image", "local_path": str(p1), "original_name": "old.jpg"})
    assert has_pending_image("u3", "c3")
    assert clear_pending_images("u3", "c3") == 1
    assert not has_pending_image("u3", "c3")


def test_pending_keeps_two_latest_images(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    p1 = tmp_path / "a.jpg"
    p2 = tmp_path / "b.jpg"
    p3 = tmp_path / "c.jpg"
    p1.write_bytes(b"a")
    p2.write_bytes(b"b")
    p3.write_bytes(b"c")
    register_pending_image("u2", "c2", {"file_type": "image", "local_path": str(p1), "original_name": "a.jpg"})
    register_pending_image("u2", "c2", {"file_type": "image", "local_path": str(p2), "original_name": "b.jpg"})
    register_pending_image("u2", "c2", {"file_type": "image", "local_path": str(p3), "original_name": "c.jpg"})
    got = pop_pending_images("u2", "c2", limit=2)
    assert len(got) == 2
    names = [str(x.get("original_name")) for x in got]
    assert names == ["c.jpg", "b.jpg"]
