import os
import tempfile

import pytest

from core.safe_paths import resolve_under


def test_resolve_under_normal():
    with tempfile.TemporaryDirectory() as base:
        path = resolve_under(base, "a", "b.txt")
        assert path == os.path.join(os.path.realpath(base), "a", "b.txt")


def test_resolve_under_rejects_traversal():
    with tempfile.TemporaryDirectory() as base:
        with pytest.raises(ValueError, match="escapes trusted base"):
            resolve_under(base, "..", "etc", "passwd")
